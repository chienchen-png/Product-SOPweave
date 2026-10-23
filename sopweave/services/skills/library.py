"""Versioned user skills. SQLite is authoritative; package files never self-publish."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from storage._utils import iso_now
from storage.skill_store import SkillStore, ensure_skill_availability_schema
from services.skills.contracts import (
    SkillConflict,
    SkillProblem,
    SkillReadonly,
    SkillRef,
    SkillSpec,
)
from services.skills.rendering import canonical, compile_skill, digest_text


class SkillLibrary:
    def __init__(self, db_path: Path, *, data_dir: Path | None = None):
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir or self.db_path.parent).resolve()
        self.root = self.data_dir / "skills"
        self.after_package_commit = (
            None  # Fault injection at the filesystem/database boundary.
        )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        ensure_skill_availability_schema(db)
        db.commit()
        try:
            yield db
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def generation(self):
        with self.connection() as db:
            return SkillStore(db).generation()

    def list_drafts(self, *, limit=20, after=''):
        if type(limit) is not int or not 1 <= limit <= 50 or len(after) > 100:
            raise SkillProblem('草稿分页参数无效')
        with self.connection() as db:
            try:
                items = SkillStore(db).list_drafts(limit=limit+1, after=after)
            except ValueError as exc:
                raise SkillProblem(str(exc)) from exc
        return {'drafts':items[:limit], 'next_cursor':items[limit-1]['draft_id'] if len(items)>limit else ''}

    def create_draft(self, *, base_skill_id=None, base_version=None, source=None):
        with self.connection() as db:
            store = SkillStore(db)
            base = (
                store.get_active_version(base_skill_id, include_disabled=True)
                if base_skill_id
                else None
            )
            if base and base["version"] != base_version:
                raise SkillConflict("基础版本已经更新，请重新打开编辑")
            result = store.create_draft(
                base_skill_id=base_skill_id, base_version=base_version, source=source
            )
            if base:
                result = store.update_draft_cas(
                    result["draft_id"],
                    1,
                    spec_json=canonical(base["spec"]),
                    source_json=canonical(base["source"]),
                )
            db.commit()
            return self.get_draft(result["draft_id"])

    def get_draft(self, draft_id):
        with self.connection() as db:
            draft = SkillStore(db).get_draft(draft_id)
        if draft["spec"]:
            compiled = compile_skill(
                SkillSpec.model_validate(draft["spec"]),
                skill_id=self._draft_skill_id(draft),
            )
            draft["preview"] = compiled.detail
            original = next(
                (
                    s.get("text")
                    for s in draft["source"]
                    if s.get("kind") in {"zip", "github"} and s.get("text")
                ),
                None,
            )
            if original:
                import difflib

                draft["import_diff"] = "\n".join(
                    difflib.unified_diff(
                        original.splitlines(),
                        compiled.content.splitlines(),
                        fromfile="原始 SKILL.md",
                        tofile="当前待发布版本",
                        lineterm="",
                    )
                )
        return draft

    def attach_source(self, draft_id, *, expected_revision, source):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            store = SkillStore(db)
            draft = store.get_draft(draft_id)
            sources = draft["source"] + [source]
            documents = [s for s in sources if s.get("kind") == "document"]
            if (
                len(documents) > 5
                or sum(s.get("size", 0) for s in documents) > 50 * 1024 * 1024
            ):
                raise SkillProblem("最多上传5个附件，合计不能超过50 MiB")
            if sum(len(s.get("text", "")) for s in sources) > 100000:
                raise SkillProblem("来源文字合计超过100000字符，请拆分SOP")
            store.update_draft_cas(
                draft_id,
                expected_revision,
                source_json=canonical(sources),
                state="created",
                generated_digest="",
            )
            db.commit()
        return self.get_draft(draft_id)

    @staticmethod
    def _draft_skill_id(draft):
        return draft["base_skill_id"] or "user-" + draft["draft_id"].removeprefix(
            "draft-"
        )

    @staticmethod
    def _confirmation_digest(draft, compiled):
        return digest_text(
            canonical(
                {
                    "spec": draft["spec"],
                    "source": draft["source"],
                    "content": compiled.content,
                }
            )
        )

    def update_draft(
        self, draft_id, *, expected_revision, spec=None, answers=None, text=None
    ):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            store = SkillStore(db)
            draft = store.get_draft(draft_id)
            fields = {}
            if text is not None:
                if not isinstance(text, str) or len(text) > 100000:
                    raise SkillProblem("补充文字超过100000字符")
                sources = [
                    s for s in draft["source"] if s.get("role") != "followup"
                ] + [{"kind": "text", "role": "followup", "text": text}]
                if sum(len(s.get("text", "")) for s in sources) > 100000:
                    raise SkillProblem("来源文字合计超过100000字符")
                draft["source"] = sources
                fields["source_json"] = canonical(sources)
            if answers is not None:
                from services.skills.contracts import DraftAnswer

                fields["answers_json"] = canonical(
                    [DraftAnswer.model_validate(a).model_dump() for a in answers]
                )
            if spec is not None:
                validated = SkillSpec.model_validate(spec)
                compiled = compile_skill(
                    validated, skill_id=self._draft_skill_id(draft)
                )
                draft["spec"] = validated.model_dump()
                fields.update(
                    spec_json=canonical(draft["spec"]),
                    state="needs_input" if validated.blockers else "ready",
                    generated_digest=self._confirmation_digest(draft, compiled),
                )
            else:
                fields.update(state="created", generated_digest="")
            store.update_draft_cas(draft_id, expected_revision, **fields)
            db.commit()
        return self.get_draft(draft_id)

    def publish(
        self, draft_id, *, expected_revision, confirmed_digest, idempotency_key
    ):
        if not idempotency_key or len(idempotency_key) > 128:
            raise SkillProblem("发布需要有效的幂等键")
        draft = self.get_draft(draft_id)
        with self.connection() as db:
            published = db.execute(
                "SELECT * FROM skill_publications WHERE idempotency_key=? OR draft_id=?",
                (idempotency_key, draft_id),
            ).fetchone()
            if published:
                return self._published_ref(db, published, draft_id, confirmed_digest)
        if (
            draft["draft_revision"] != expected_revision
            or not confirmed_digest
            or draft["generated_digest"] != confirmed_digest
        ):
            raise SkillConflict("预览已经变化，请重新审阅并确认")
        spec = SkillSpec.model_validate(draft["spec"])
        from services.skills.policies import compile_policy

        compile_policy(spec)
        if spec.blockers or draft["state"] != "ready":
            raise SkillProblem("请先解决草稿中的问题并生成当前预览")
        skill_id = self._draft_skill_id(draft)
        compiled = compile_skill(spec, skill_id=skill_id)
        if self._confirmation_digest(draft, compiled) != confirmed_digest:
            raise SkillConflict("发布内容与确认预览不一致")
        # Staging I/O happens before the short publication transaction.
        staging = self.root / "staging" / uuid.uuid4().hex
        staging.mkdir(parents=True)
        try:
            (staging / "SKILL.md").write_text(
                compiled.content, encoding="utf-8", newline="\n"
            )
            hashes = {"SKILL.md": digest_text(compiled.content)}
            import hashlib

            for source in draft["source"]:
                if source.get("kind") != "bundle":
                    continue
                root = (self.data_dir / source["bundle_relative_path"]).resolve()
                if not any(
                    root.is_relative_to((self.root / kind).resolve())
                    for kind in ("drafts", "packages")
                ):
                    raise SkillProblem("导入资源来源路径无效")
                for name, expected in source["resource_hashes"].items():
                    path = (root / name).resolve()
                    if not path.is_relative_to(root):
                        raise SkillProblem("导入资源路径越界")
                    data = path.read_bytes()
                    if hashlib.sha256(data).hexdigest() != expected:
                        raise SkillConflict("导入资源已变化，请重新导入并确认")
                    target = staging / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    hashes[name] = expected
            (staging / ".bundle.json").write_text(canonical(hashes), encoding="utf-8")
            bundle_digest = digest_text(canonical(hashes))
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                store = SkillStore(db)
                published = db.execute(
                    "SELECT * FROM skill_publications WHERE idempotency_key=? OR draft_id=?",
                    (idempotency_key, draft_id),
                ).fetchone()
                if published:
                    return self._published_ref(
                        db, published, draft_id, confirmed_digest
                    )
                current_draft = store.get_draft(draft_id)
                if (
                    current_draft["draft_revision"] != expected_revision
                    or current_draft["generated_digest"] != confirmed_digest
                ):
                    raise SkillConflict("草稿已变化，请重新确认")
                if draft["base_skill_id"]:
                    current = store.get_active_version(
                        skill_id, include_disabled=True
                    )
                    if current["version"] != draft["base_version"]:
                        raise SkillConflict(
                            "另一份编辑已经发布，请重新基于最新版本编辑"
                        )
                    version = current["version"] + 1
                else:
                    version = 1
                    db.execute(
                        "INSERT INTO user_skills(skill_id,title,created_at,updated_at) VALUES(?,?,?,?)",
                        (skill_id, spec.title, iso_now(), iso_now()),
                    )
                relative = Path("skills") / "packages" / skill_id / str(version)
                target = self.data_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if self._verify_package(target) != bundle_digest:
                        raise SkillConflict("目标版本存在不一致的残留包，请检查存储")
                else:
                    os.replace(staging, target)
                if self.after_package_commit:
                    self.after_package_commit()
                summary = {
                    key: compiled.detail[key]
                    for key in (
                        "name",
                        "title",
                        "description",
                        "step_count",
                        "toolsets",
                        "required_tools",
                        "origin",
                        "readonly",
                        "path",
                        "trigger_words",
                        "user_invocable",
                    )
                }
                db.execute(
                    "INSERT INTO user_skill_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        skill_id,
                        version,
                        canonical(spec.model_dump()),
                        spec.description,
                        canonical(summary),
                        bundle_digest,
                        relative.as_posix(),
                        canonical(spec.execution_policy.model_dump()),
                        canonical(
                            [
                                dict(s, bundle_relative_path=relative.as_posix())
                                if s.get("kind") == "bundle"
                                else s
                                for s in draft["source"]
                            ]
                        ),
                        iso_now(),
                    ),
                )
                db.execute(
                    "UPDATE user_skills SET current_version=?,title=?,updated_at=? WHERE skill_id=?",
                    (version, spec.title, iso_now(), skill_id),
                )
                ref = SkillRef(
                    skill_id=skill_id,
                    origin="user",
                    version=version,
                    content_digest=bundle_digest,
                )
                store.update_draft_cas(
                    draft_id,
                    expected_revision,
                    state="published",
                    publication_ref=ref.model_dump_json(),
                )
                db.execute(
                    "INSERT INTO skill_publications VALUES(?,?,?,?,?)",
                    (idempotency_key, draft_id, skill_id, version, confirmed_digest),
                )
                store.bump_generation()
                db.commit()
                return ref
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _published_ref(self, db, publication, draft_id, confirmed_digest):
        if (
            publication["draft_id"] != draft_id
            or publication["confirmed_digest"] != confirmed_digest
        ):
            raise SkillConflict("幂等键已用于其它内容")
        row = SkillStore(db).get_active_version(
            publication["skill_id"],
            publication["version"],
            include_disabled=True,
        )
        return SkillRef(
            skill_id=row["skill_id"],
            origin="user",
            version=row["version"],
            content_digest=row["content_digest"],
        )

    def _verify_package(self, package):
        try:
            if not package.resolve().is_relative_to((self.root / "packages").resolve()):
                raise ValueError("package path")
            index = json.loads((package / ".bundle.json").read_text(encoding="utf-8"))
            if (
                not isinstance(index, dict)
                or "SKILL.md" not in index
                or len(index) > 257
            ):
                raise ValueError("package index")
            for relative, digest in index.items():
                target = (package / relative).resolve()
                if not target.is_relative_to(package.resolve()) or not target.is_file():
                    raise ValueError("package resource path")
                import hashlib

                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise ValueError("package digest")
            return digest_text(canonical(index))
        except (OSError, ValueError, TypeError) as exc:
            raise SkillProblem("Skill 资源包损坏或被修改，请从备份恢复") from exc

    def get(self, skill_id, version=None, *, include_disabled=False):
        with self.connection() as db:
            row = SkillStore(db).get_active_version(
                skill_id, version, include_disabled=include_disabled
            )
        package = self.data_dir / row["package_relative_path"]
        if self._verify_package(package) != row["content_digest"]:
            raise SkillProblem("Skill 资源包摘要不一致")
        compiled = compile_skill(
            SkillSpec.model_validate(row["spec"]), skill_id=skill_id
        )
        return dict(
            compiled.detail,
            version=row["version"],
            content_digest=row["content_digest"],
            package_relative_path=row["package_relative_path"],
            spec=row["spec"],
            source=row["source"],
            enabled=bool(row["enabled"]),
        )

    def list(
        self, *, query="", limit=50, after="", include_disabled=False
    ):
        with self.connection() as db:
            return [
                dict(
                    row["summary"],
                    version=row["version"],
                    content_digest=row["content_digest"],
                    enabled=bool(row["enabled"]),
                )
                for row in SkillStore(db).list_active(
                    query=query,
                    limit=limit,
                    after=after,
                    include_disabled=include_disabled,
                )
            ]

    def set_enabled(
        self,
        skill_id,
        *,
        enabled,
        expected_version,
        expected_generation,
    ):
        if not skill_id.startswith("user-"):
            raise SkillReadonly("内置 Skill 为只读")
        if type(enabled) is not bool:
            raise SkillProblem("启用状态无效")
        if type(expected_version) is not int or expected_version < 1:
            raise SkillProblem("Skill 版本无效")
        if type(expected_generation) is not int or expected_generation < 0:
            raise SkillProblem("Skill 目录版本无效")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            store = SkillStore(db)
            row = store.get_active_version(skill_id, include_disabled=True)
            if row["version"] != expected_version:
                raise SkillConflict("Skill 版本已变化，请刷新后重试")
            current_generation = store.generation()
            if current_generation != expected_generation:
                raise SkillConflict("SOP 目录已更新，请刷新后重试")
            db.execute(
                "UPDATE user_skills SET enabled=?,updated_at=? WHERE skill_id=?",
                (1 if enabled else 0, iso_now(), skill_id),
            )
            store.bump_generation()
            db.commit()
        return {
            "skill_id": skill_id,
            "enabled": enabled,
            "generation": current_generation + 1,
        }

    def delete(self, skill_id, *, expected_version):
        if not skill_id.startswith("user-"):
            raise SkillReadonly("内置 Skill 为只读")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            store = SkillStore(db)
            row = store.get_active_version(skill_id, include_disabled=True)
            if row["version"] != expected_version:
                raise SkillConflict("Skill 版本已变化，请刷新后重试")
            refs = store.list_run_refs(skill_id)
            if refs:
                raise SkillConflict(
                    "Skill 正被任务使用，结束任务后再删除",
                    details={"run_ids": [r["run_id"] for r in refs]},
                )
            db.execute(
                "UPDATE user_skills SET status='deleted',deleted_at=?,updated_at=? WHERE skill_id=?",
                (iso_now(), iso_now(), skill_id),
            )
            store.bump_generation()
            db.commit()
        # Retain immutable versions for history/backup. Tombstones prevent rediscovery.
