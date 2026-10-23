from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml
from pydantic import ValidationError

from config.settings import BASE_DIR
from .agent_registry import AgentRegistry
from .skill_format import SkillManifest, parse_frontmatter


class SkillError(RuntimeError):
    pass


class SkillNotFoundError(SkillError):
    pass


class SkillPermissionError(SkillError):
    pass


class SkillResourceNotFoundError(SkillError):
    pass


class SkillValidationError(SkillError):
    pass


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    user_invocable: bool
    path: str
    trigger_words: tuple[str, ...]
    step_count: int
    origin: str = 'builtin'
    readonly: bool = True
    version: int | str = ''
    content_digest: str = ''
    title: str = ''
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": _strip_trigger_tail(self.description),
            "user_invocable": self.user_invocable,
            "trigger_words": list(self.trigger_words),
            "step_count": self.step_count,
            "path": self.path,
            "origin": self.origin, "readonly": self.readonly, "version": self.version,
            "content_digest": self.content_digest, "title": self.title or self.name,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class SkillDefinition:
    manifest: SkillManifest
    content: str
    body: str
    skill_dir: Path
    details: dict
    origin: str = 'builtin'
    version: int | str = ''
    content_digest: str = ''
    enabled: bool = True

    def summary(self) -> SkillSummary:
        return SkillSummary(
            name=self.manifest.name,
            description=self.manifest.description,
            user_invocable=self.manifest.user_invocable,
            path=f"skills/{self.manifest.name}/SKILL.md",
            trigger_words=tuple(extract_trigger_words(self.manifest.description)),
            step_count=len(self.details["steps"]),
            origin=self.origin, readonly=self.origin=='builtin', version=self.version,
            content_digest=self.content_digest or hashlib.sha256(self.content.encode()).hexdigest(),
            title=self.details.get('title', self.manifest.name),
            enabled=self.enabled,
        )

    def to_detail_dict(self) -> dict:
        return {
            **(self.details if self.origin=='user' else {}),
            **self.summary().to_dict(),
            "purpose": self.details["purpose"],
            "steps": self.details["steps"],
            "constraints": self.details["constraints"],
            "capabilities": self.details["capabilities"],
            "content": self.content,
            "body": self.body.strip(),
            "toolsets": self.manifest.toolsets,
        }


class SkillRegistry:
    _RESOURCE_DIRS = frozenset({"references", "scripts", "assets"})

    def __init__(
        self,
        skills_root: str | Path | None = None,
        *,
        agent_registry: AgentRegistry | None = None,
        library=None,
    ) -> None:
        self.skills_root = Path(skills_root or BASE_DIR / "skills").resolve()
        self.agent_registry = agent_registry or AgentRegistry()
        self.library = library
        self._cache_snapshot: tuple | None = None
        self._cache: dict[str, SkillDefinition] = {}

    def list_all(self) -> list[SkillSummary]:
        return [item.summary() for item in self._ordered_definitions()]

    def list_for_agent(self, skill_ids: list[str]) -> list[SkillSummary]:
        definitions = self._definitions()
        result = []
        for skill_id in skill_ids:
            try:
                result.append(definitions[skill_id].summary())
            except KeyError as exc:
                raise SkillNotFoundError(f"Skill 不存在: {skill_id}") from exc
        return result

    def get(
        self, skill_id: str, *, include_disabled: bool = False
    ) -> SkillDefinition:
        if self.library is not None and skill_id.startswith('user-'):
            from services.skills.contracts import SkillMissing
            try:
                detail = self.library.get(
                    skill_id, include_disabled=include_disabled
                )
            except SkillMissing as exc:
                raise SkillNotFoundError(str(exc)) from exc
            metadata, body = _parse_frontmatter(detail['content'])
            return SkillDefinition(SkillManifest.model_validate(metadata),detail['content'],body,
                                   self.library.data_dir/detail['package_relative_path'],detail,
                                   'user',detail['version'],detail['content_digest'],
                                   bool(detail['enabled']))
        try:
            return self._definitions()[skill_id]
        except KeyError as exc:
            raise SkillNotFoundError(f"Skill 不存在: {skill_id}") from exc

    def read_skill(self, agent_id: str, skill_id: str) -> str:
        self._authorize(agent_id, skill_id)
        return self.get(skill_id).content

    def read_resource(self, agent_id: str, skill_id: str, relative_path: str) -> str:
        self._authorize(agent_id, skill_id)
        definition = self.get(skill_id)
        normalized = str(relative_path or "").strip().replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
        ):
            raise SkillPermissionError("Skill 资源路径无效")
        path = PurePosixPath(normalized)
        if ".." in path.parts or "." in path.parts:
            raise SkillPermissionError("Skill 资源路径越界")
        if path.parts[0] not in self._RESOURCE_DIRS:
            path = PurePosixPath("references") / path
        target = (definition.skill_dir / Path(*path.parts)).resolve()
        allowed_root = (definition.skill_dir / path.parts[0]).resolve()
        if not _is_relative_to(target, allowed_root) or not allowed_root.is_relative_to(definition.skill_dir.resolve()):
            raise SkillPermissionError("Skill 资源路径越界")
        if not target.is_file():
            raise SkillResourceNotFoundError(
                f"Skill 资源不存在: {skill_id}/{path.as_posix()}"
            )
        try:
            return _atomic_read_text(target)
        except (OSError, UnicodeError) as exc:
            raise SkillResourceNotFoundError(
                f"Skill 资源无法读取: {skill_id}/{path.as_posix()}"
            ) from exc

    def _authorize(self, agent_id: str, skill_id: str) -> None:
        agent = self.agent_registry.get(agent_id)
        if skill_id.startswith('user-') and self.library is not None and agent.manifest.allow_user_skills:
            self.get(skill_id)
            return
        if skill_id not in agent.manifest.skills:
            raise SkillPermissionError(
                f"智能体 {agent_id} 未授权使用 Skill: {skill_id}"
            )

    def search_for_agent(self, agent_id: str, *, query: str='', limit: int=20):
        agent = self.agent_registry.get(agent_id)
        limit = max(1, min(limit, 50))
        items = [item for item in self.list_for_agent(agent.manifest.skills)
                 if not query or query.casefold() in (item.name+' '+item.description).casefold()]
        if self.library is not None and agent.manifest.allow_user_skills:
            for item in self.library.list(query=query, limit=limit):
                items.append(SkillSummary(item['name'],item['description'],True,item['path'],(),item['step_count'],
                                          'user',False,item['version'],item['content_digest'],item['title'],
                                          item.get('enabled', True)))
        return items[:limit]

    def _ordered_definitions(self) -> list[SkillDefinition]:
        return sorted(self._definitions().values(), key=lambda item: item.manifest.name)

    def _definitions(self) -> dict[str, SkillDefinition]:
        snapshot = self._snapshot()
        if snapshot != self._cache_snapshot:
            self._cache = self._load_all()
            self._cache_snapshot = snapshot
        return self._cache

    def _snapshot(self) -> tuple:
        if not self.skills_root.is_dir():
            return ()
        files = []
        for path in sorted(self.skills_root.glob("*/SKILL.md")):
            stat = path.stat()
            files.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(files)

    def _load_all(self) -> dict[str, SkillDefinition]:
        candidates: list[tuple[Path, dict, str, str]] = []
        if self.skills_root.is_dir():
            for path in sorted(self.skills_root.glob("*/SKILL.md")):
                try:
                    content = _atomic_read_text(path)
                    metadata, body = _parse_frontmatter(content)
                except (OSError, UnicodeError, yaml.YAMLError) as exc:
                    raise SkillValidationError(f"无法解析 Skill: {path}") from exc
                candidates.append((path, metadata, body, content))

        names = [str(metadata.get("name") or "") for _, metadata, _, _ in candidates]
        duplicates = sorted({name for name in names if name and names.count(name) > 1})
        if duplicates:
            raise SkillValidationError(f"Skill name 重复: {', '.join(duplicates)}")

        definitions = {}
        for path, metadata, body, content in candidates:
            try:
                manifest = SkillManifest.model_validate(metadata)
            except ValidationError as exc:
                raise SkillValidationError(
                    f"Skill frontmatter 无效 {path}: {exc.errors(include_url=False)}"
                ) from exc
            if path.parent.name != manifest.name:
                raise SkillValidationError(
                    f"Skill 目录与 frontmatter name 不一致: "
                    f"{path.parent.name} != {manifest.name}"
                )
            definitions[manifest.name] = SkillDefinition(
                manifest=manifest,
                content=content,
                body=body,
                skill_dir=path.parent,
                details=_parse_document_details(body),
            )
        return definitions


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    return parse_frontmatter(text)


def _parse_document_details(body: str) -> dict:
    purpose = _section(body, "职责")
    constraints = _section(body, "约束")
    steps = []
    pattern = r"###\s*步骤\s*(\d+)[:：]\s*([^\n]+)\n(.*?)(?=\n###\s|\n##\s|\Z)"
    for match in re.finditer(pattern, body, re.DOTALL):
        block = match.group(3)
        branches = []
        branch_pattern = (
            r"####\s*分支\s*(\S+)[:：]\s*([^\n]+)\n(.*?)(?=\n####\s|\n###\s|\n##\s|\Z)"
        )
        for branch in re.finditer(branch_pattern, block, re.DOTALL):
            branches.append(
                {
                    "key": branch.group(1).strip(),
                    "title": branch.group(2).strip(),
                    "summary": _first_plain_line(branch.group(3))[:100],
                }
            )
        main = re.sub(
            r"####\s*分支.*?(?=\n###\s|\n##\s|\Z)", "", block, flags=re.DOTALL
        )
        title = match.group(2).strip()
        steps.append(
            {
                "n": int(match.group(1)),
                "title": title.replace("★", "").strip(),
                "summary": _first_plain_line(main)[:120],
                "branch_point": "★" in title,
                "branches": branches,
            }
        )
    if not steps:
        workflow = _section(body, "工作方法") or re.split(r"(?m)^##\s+", body, maxsplit=1)[0]
        for line in workflow.splitlines():
            match = re.match(r"^(?:\d+\.\s+|-\s+)(.+)", line)
            if match:
                instruction = match.group(1).strip()
                steps.append(dict(n=len(steps)+1, title=instruction[:70], summary=instruction,
                                  branch_point=False, branches=[]))
    steps.sort(key=lambda item: item["n"])
    return {
        'title':next((line.removeprefix('# ').strip() for line in body.splitlines() if line.startswith('# ')),''),
        "purpose": purpose.replace("\n", " "),
        "steps": steps,
        "constraints": constraints,
        "capabilities": _parse_capabilities(body),
    }


def _section(body: str, title: str) -> str:
    match = re.search(
        rf"##\s*{re.escape(title)}\s*\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL
    )
    return match.group(1).strip() if match else ""


def _first_plain_line(text: str) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return re.sub(r"[#*`>]+|^[-*]\s*", "", line).strip()


def _parse_capabilities(body: str) -> dict:
    models = []
    section = re.search(r"##\s*模型索引[^\n]*\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL)
    if section:
        for line in section.group(1).splitlines():
            match = re.match(r"-\s*\*\*([A-G])\s*([^*]+)\*\*[:：]\s*(.+)", line.strip())
            if match:
                items = [
                    item.strip()
                    for item in re.split(r"\s+/\s+", match.group(3))
                    if item.strip()
                ]
                models.append(
                    {
                        "category": match.group(1).strip(),
                        "name": match.group(2).strip(),
                        "count": len(items),
                        "items": items,
                    }
                )
    stats = []
    section = re.search(r"##\s*分析工具[^\n]*\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL)
    if section:
        first = next(
            (
                line.strip()
                for line in section.group(1).splitlines()
                if line.strip() and not line.startswith((">", "-"))
            ),
            "",
        )
        stats = [
            re.sub(r"\s*\(.*?\).*$", "", item).strip()
            for item in re.split(r"\s*/\s*", first)
            if item.strip()
        ]
    charts = []
    section = re.search(r"##\s*图表模板索引[^\n]*\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL)
    if section:
        raw = " ".join(
            line.strip()
            for line in section.group(1).splitlines()
            if line.strip()
            and not line.strip().startswith("-")
            and not re.search(r"[\u4e00-\u9fff]|[（(]", line)
        )
        charts = [item.strip() for item in re.split(r"\s*/\s*", raw) if item.strip()]
    return {"models": models, "stats": stats, "charts": charts}


def extract_trigger_words(description: str) -> list[str]:
    match = re.search(r"触发词[:：]\s*[\[【]?\s*([^\]】\n]+)", description or "")
    if not match:
        return []
    return [
        word.strip() for word in re.split(r"[、,，/|]+", match.group(1)) if word.strip()
    ]


def _strip_trigger_tail(description: str) -> str:
    match = re.search(r"触发词[:：]", description or "")
    return description[: match.start()].rstrip("。；;，, \t") if match else description


def _atomic_read_text(path: Path) -> str:
    for _ in range(3):
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size):
            return content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    raise OSError(f"文件在读取期间持续变化: {path}")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
