"""Bind skill versions to durable runs, including approvals and restarts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from storage._utils import iso_now
from storage.skill_store import SkillStore
from services.skills.contracts import (
    RunSkillSnapshot,
    SkillRef,
    SopSelection,
    SkillProblem,
    SkillConflict,
)
from services.skills.rendering import canonical, digest_text


@dataclass(frozen=True)
class LoadedSkill:
    content: str
    ref: SkillRef
    toolsets: tuple[str, ...]


class SkillRuntime:
    def __init__(self, library, registry):
        self.library = library
        self.registry = registry

    @staticmethod
    def _ref(summary):
        return SkillRef(
            skill_id=summary.name,
            origin=summary.origin,
            version=summary.version or summary.content_digest,
            content_digest=summary.content_digest,
        )

    def get_snapshot(self, run_id, *, connection=None):
        if connection is None:
            with self.library.connection() as db:
                return self.get_snapshot(run_id, connection=db)
        row = connection.execute(
            "SELECT snapshot_json FROM skill_run_contexts WHERE run_id=?", (run_id,)
        ).fetchone()
        return RunSkillSnapshot.model_validate_json(row[0]) if row else None

    def _save(self, connection, run_id, snapshot):
        connection.execute(
            "INSERT INTO skill_run_contexts VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET snapshot_json=excluded.snapshot_json",
            (run_id, snapshot.model_dump_json()),
        )

    def resolve_start(
        self,
        selection: SopSelection,
        *,
        run_id,
        agent_id,
        connection,
        work_dir=None,
        inputs=None,
    ):
        items = self.registry.search_for_agent(agent_id, limit=50)
        if selection.mode == "none":
            items = [s for s in items if s.origin == "builtin"]
        if selection.mode == "explicit":
            self.registry._authorize(agent_id, selection.skill_id)
            chosen = self.registry.get(selection.skill_id).summary()
            items = [s for s in items if s.name == "office-files"] + [chosen]
        snapshot = RunSkillSnapshot(
            mode=selection.mode,
            catalog_generation=SkillStore(connection).generation(),
            advertised_refs=[self._ref(s) for s in items],
            inputs=inputs or {},
        )
        if selection.mode == "explicit":
            loaded = self._pin(
                connection, run_id, snapshot, self._ref(chosen), work_dir=work_dir
            )
            snapshot.explicit_ref = loaded.ref
        self._save(connection, run_id, snapshot)
        return snapshot

    def search_for_run(self, run_id, *, query, limit=20):
        with self.library.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = self.get_snapshot(run_id, connection=db)
            run = self._live_run(db, run_id)
            items = self.registry.search_for_agent(
                run["agent_id"], query=query, limit=min(limit, 20)
            )
            if snapshot.mode == "none":
                items = [s for s in items if s.origin == "builtin"]
            elif snapshot.mode == "explicit":
                items = [
                    s
                    for s in items
                    if s.name == "office-files"
                    or s.name == snapshot.explicit_ref.skill_id
                ]
            known = {r.skill_id for r in snapshot.advertised_refs}
            snapshot.advertised_refs.extend(
                self._ref(s) for s in items if s.name not in known
            )
            self._save(db, run_id, snapshot)
            db.commit()
            return items

    @staticmethod
    def _live_run(db, run_id):
        run = db.execute(
            "SELECT agent_id,status FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None or run["status"] not in {
            "pending",
            "starting",
            "running",
            "resuming",
            "waiting",
        }:
            raise SkillConflict("当前任务已结束，不能继续加载 Skill")
        return run

    def load_for_run(self, run_id, skill_id, *, resource_path=""):
        with self.library.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._live_run(db, run_id)
            snapshot = self.get_snapshot(run_id, connection=db)
            if snapshot is None:
                raise SkillConflict("此任务没有可核验的 Skill 快照，请重新发起任务")
            manifest = self.registry.agent_registry.get(run["agent_id"]).manifest
            if skill_id.startswith("user-"):
                if (
                    not manifest.allow_user_skills
                    or self.registry.library is None
                    or snapshot.mode == "none"
                ):
                    raise SkillProblem("当前任务未授权用户 SOP")
                if (
                    snapshot.mode == "explicit"
                    and snapshot.explicit_ref.skill_id != skill_id
                ):
                    raise SkillProblem("当前任务已固定其它 SOP")
            elif skill_id not in manifest.skills:
                raise SkillProblem("当前智能体未授权此 Skill")
            ref = next(
                (r for r in snapshot.loaded_refs if r.skill_id == skill_id), None
            )
            if ref is None:
                ref = next(
                    (r for r in snapshot.advertised_refs if r.skill_id == skill_id),
                    None,
                )
                if ref is None:
                    raise SkillProblem("请先通过 read_skill.query 查找此 Skill")
            loaded = self._pin(db, run_id, snapshot, ref, resource_path=resource_path)
            self._save(db, run_id, snapshot)
            db.commit()
            return loaded

    def _pin(self, db, run_id, snapshot, ref, *, resource_path="", work_dir=None):
        if ref.origin == "user":
            detail = self.library.get(
                ref.skill_id, int(ref.version), include_disabled=True
            )
            if detail["content_digest"] != ref.content_digest:
                raise SkillConflict("Skill 版本摘要不一致")
            root = self.library.data_dir / detail["package_relative_path"]
            content = detail["content"]
            groups = detail["toolsets"]
            from services.skills.contracts import SourceVersion
            from agent_runtime.workspace import resolve_path, file_version

            if detail["spec"]["execution_policy"]["source_versions"]:
                if work_dir is None:
                    from config.paths import resolve_work_dir

                    row = db.execute(
                        "SELECT request_json FROM agent_run_requests WHERE run_id=?",
                        (run_id,),
                    ).fetchone()
                    work_dir = resolve_work_dir(json.loads(row[0])["work_dir"])
                for source in detail["spec"]["execution_policy"]["source_versions"]:
                    if not any(
                        v.path == source["path"] for v in snapshot.resolved_sources
                    ):
                        version = (
                            file_version(resolve_path(work_dir, source["path"]))
                            if source["version"] == "at-start"
                            else source["version"]
                        )
                        snapshot.resolved_sources.append(
                            SourceVersion(path=source["path"], version=version)
                        )
        else:
            root = self.library.root / "runtime-snapshots" / str(ref.version)
            if not root.is_dir():
                definition = self.registry.get(ref.skill_id)
                if digest_text(definition.content) != ref.content_digest:
                    raise SkillConflict("内置 Skill 自目录展示后发生变化，请重新开始")
                files = {"SKILL.md": definition.content.encode("utf-8")}
                for folder in ("references", "assets", "scripts"):
                    for path in (definition.skill_dir / folder).rglob("*"):
                        if path.is_file():
                            if not path.resolve().is_relative_to(
                                definition.skill_dir.resolve()
                            ):
                                raise SkillProblem("内置资源越界")
                            files[path.relative_to(definition.skill_dir).as_posix()] = (
                                path.read_bytes()
                            )
                hashes = {p: hashlib.sha256(v).hexdigest() for p, v in files.items()}
                digest = digest_text(canonical(hashes))
                root = self.library.root / "runtime-snapshots" / digest
                for relative, data in files.items():
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        target.write_bytes(data)
                (root / ".bundle.json").write_text(canonical(hashes), encoding="utf-8")
                ref = SkillRef(
                    skill_id=ref.skill_id,
                    origin="builtin",
                    version=digest,
                    content_digest=digest,
                )
            index = json.loads((root / ".bundle.json").read_text(encoding="utf-8"))
            if digest_text(canonical(index)) != ref.content_digest:
                raise SkillConflict("内置 Skill 快照摘要错误")
            for relative, digest in index.items():
                if hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest:
                    raise SkillConflict("内置 Skill 固定资源被修改")
            from agent_runtime.definitions.skill_format import validate_skill_document

            content = (root / "SKILL.md").read_text(encoding="utf-8")
            groups = validate_skill_document(content).manifest.toolsets
        if resource_path:
            relative = PurePosixPath(resource_path.replace("\\", "/"))
            if (
                not relative.parts
                or relative.is_absolute()
                or ":" in str(relative)
                or ".." in relative.parts
            ):
                raise SkillProblem("Skill 资源路径无效")
            if relative.parts[0] not in {"references", "assets", "scripts"}:
                relative = PurePosixPath("references") / relative
            target = (root / str(relative)).resolve()
            index = json.loads((root / ".bundle.json").read_text(encoding="utf-8"))
            if str(relative) not in index or not target.is_relative_to(root.resolve()):
                raise SkillProblem("Skill 资源不存在或未包含在固定版本中")
            content = target.read_text(encoding="utf-8")
        if len(content.encode("utf-8")) > 128 * 1024:
            raise SkillProblem("Skill 正文或资源过长，请拆分引用资源")
        if not any(r.skill_id == ref.skill_id for r in snapshot.loaded_refs):
            snapshot.loaded_refs.append(ref)
        snapshot.policy_digest = digest_text(
            canonical([r.model_dump() for r in snapshot.loaded_refs])
        )
        db.execute(
            "INSERT OR IGNORE INTO skill_run_refs(run_id,skill_id,version,origin,content_digest,created_at) VALUES(?,?,?,?,?,?)",
            (
                run_id,
                ref.skill_id,
                str(ref.version),
                ref.origin,
                ref.content_digest,
                iso_now(),
            ),
        )
        return LoadedSkill(content, ref, tuple(groups))

    def pinned_instructions(self, run_id):
        snapshot = self.get_snapshot(run_id)
        return (
            "\n".join(
                self.load_for_run(run_id, r.skill_id).content
                for r in snapshot.loaded_refs
            )
            if snapshot
            else ""
        )

    def directory_prompt(self, run_id):
        snapshot = self.get_snapshot(run_id)
        if snapshot is None:
            return ""
        rows = []
        for ref in snapshot.advertised_refs[:50]:
            if ref.origin == "user":
                with self.library.connection() as db:
                    from services.skills.contracts import SkillMissing

                    try:
                        row = SkillStore(db).get_active_version(
                            ref.skill_id,
                            int(ref.version),
                            include_disabled=True,
                        )
                    except SkillMissing:
                        continue
                description = row["description"]
            else:
                description = self.registry.get(ref.skill_id).manifest.description
            item = dict(id=ref.skill_id, version=ref.version, description=description)
            if len(canonical(rows + [item])) > 8000:
                break
            rows.append(item)
        return (
            "可用 Skill（先 read_skill；更多使用 query 搜索，每次最多20项）：\n"
            + canonical(rows)
        )

    def policy_decision(self, run_id, name, risk, arguments, work_dir):
        from services.skills.policies import (
            PolicyDecision,
            compile_policy,
            SkillOperation,
        )
        from services.skills.contracts import SkillSpec

        snapshot = self.get_snapshot(run_id)
        decision = PolicyDecision("allow")
        if snapshot is None:
            return decision
        for ref in snapshot.loaded_refs:
            if ref.origin != "user":
                continue
            detail = self.library.get(
                ref.skill_id, int(ref.version), include_disabled=True
            )
            spec = SkillSpec.model_validate(detail["spec"])
            resolved = {s.path: s for s in snapshot.resolved_sources}
            spec.execution_policy.source_versions = [
                resolved.get(s.path, s) for s in spec.execution_policy.source_versions
            ]
            inputs = {v.name: v.default for v in spec.inputs if v.default}
            inputs.update(snapshot.inputs)
            with self.library.connection() as db:
                if db.execute(
                    "SELECT name FROM sqlite_master WHERE name='agent_interactions'"
                ).fetchone():
                    replies = db.execute(
                        "SELECT response_json FROM agent_interactions WHERE run_id=? AND kind='input' AND status='resolved' ORDER BY updated_at",
                        (run_id,),
                    ).fetchall()
                    for row in replies:
                        for answer in json.loads(row[0]).get("answers", []):
                            inputs[answer["question_id"]] = answer.get(
                                "text"
                            ) or ",".join(answer.get("selected", []))
            operation = SkillOperation(
                name,
                risk,
                arguments,
                work_dir,
                inputs,
                frozenset(snapshot.completed_checks),
            )
            current = compile_policy(spec).evaluate(operation, snapshot=snapshot)
            if current.action == "deny":
                return current
            if current.action == "confirm":
                decision = current
        return decision

    def record_result(self, run_id, name, result):
        if (
            name != "reconcile_variables"
            or not isinstance(result, dict)
            or not result.get("success")
        ):
            return
        summary = result.get("summary", {})
        complete = sum(
            summary.get(k, 0) for k in ("ok", "a1", "a2", "b", "c")
        ) == summary.get("total", -1)
        if not complete:
            return
        with self.library.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = self.get_snapshot(run_id, connection=db)
            if snapshot and "reconciliation_complete" not in snapshot.completed_checks:
                snapshot.completed_checks.append("reconciliation_complete")
                self._save(db, run_id, snapshot)
            db.commit()
