from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from config.settings import BASE_DIR


class DefinitionError(RuntimeError):
    pass


class AgentNotFoundError(DefinitionError):
    pass


class DefinitionValidationError(DefinitionError):
    pass


class AgentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    enabled: bool = True
    default_model_id: str = ""
    toolsets: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    approval_policy: dict[str, Literal["always", "never", "work_mode"]] = Field(
        default_factory=dict
    )
    max_turns: int = Field(default=30, ge=1, le=1000)
    progressive_tools: bool = False
    allow_user_skills: bool = False

    @field_validator("toolsets", "tools", "skills")
    @classmethod
    def unique_names(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("列表中不能包含空名称")
        if len(normalized) != len(set(normalized)):
            raise ValueError("列表中不能包含重复名称")
        return normalized


@dataclass(frozen=True)
class AgentSummary:
    id: str
    name: str
    version: str
    description: str
    enabled: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class AgentDefinition:
    manifest: AgentManifest
    instructions: str
    profile_dir: Path

    def to_dict(self) -> dict:
        manifest = self.manifest
        return {
            **manifest.model_dump(),
            "role": manifest.description,
            "system_prompt": self.instructions,
            "workflow": {"max_turns": manifest.max_turns},
            "tool_policy": {
                "toolsets": manifest.toolsets,
                "allowed_tools": manifest.tools,
                "approval_policy": manifest.approval_policy,
            },
            "skill_refs": [
                {"id": skill_id, "path": f"skills/{skill_id}/SKILL.md"}
                for skill_id in manifest.skills
            ],
        }


class AgentRegistry:
    def __init__(
        self,
        profiles_root: str | Path | None = None,
        *,
        skill_roots: list[str | Path] | None = None,
        known_toolsets: set[str] | None = None,
    ) -> None:
        self.profiles_root = Path(
            profiles_root or BASE_DIR / "agent_profiles"
        ).resolve()
        roots = skill_roots or [BASE_DIR / "skills"]
        self.skill_roots = tuple(Path(root).resolve() for root in roots)
        self.known_toolsets = frozenset(
            known_toolsets
            or {
                "workbench",
                "project-data",
                "analysis",
                "skills",
                "interaction",
                "core",
                "office",
            }
        )
        self._cache_snapshot: tuple | None = None
        self._cache: dict[str, AgentDefinition] = {}
        self._errors: dict[str, dict[str, str]] = {}

    def get(self, agent_id: str) -> AgentDefinition:
        definitions = self._definitions()
        if agent_id in self._errors:
            raise DefinitionValidationError(self._errors[agent_id]["message"])
        try:
            definition = definitions[agent_id]
        except KeyError as exc:
            raise AgentNotFoundError(f"智能体不存在: {agent_id}") from exc
        if not definition.manifest.enabled:
            raise AgentNotFoundError(f"智能体未启用: {agent_id}")
        return definition

    def list_enabled(self) -> list[AgentSummary]:
        return [
            AgentSummary(
                id=definition.manifest.id,
                name=definition.manifest.name,
                version=definition.manifest.version,
                description=definition.manifest.description,
                enabled=definition.manifest.enabled,
            )
            for definition in sorted(
                self._definitions().values(),
                key=lambda item: (item.manifest.name, item.manifest.id),
            )
            if definition.manifest.enabled
        ]

    def _definitions(self) -> dict[str, AgentDefinition]:
        snapshot = self._snapshot()
        if snapshot != self._cache_snapshot:
            loaded, errors = self._load_all()
            self._cache = loaded
            self._errors = errors
            self._cache_snapshot = snapshot
        return self._cache

    def diagnostics(self) -> list[dict[str, str]]:
        self._definitions()
        return [dict(issue) for issue in self._errors.values()]

    def _snapshot(self) -> tuple:
        files = []
        if self.profiles_root.is_dir():
            for path in sorted(self.profiles_root.glob("*/agent.yaml")):
                instructions = path.parent / "instructions.md"
                for item in (path, instructions):
                    if item.exists():
                        stat = item.stat()
                        files.append((str(item), stat.st_mtime_ns, stat.st_size))
                    else:
                        files.append((str(item), None, None))
        skills = tuple(
            sorted(
                str(child)
                for root in self.skill_roots
                if root.is_dir()
                for child in root.iterdir()
                if child.is_dir()
            )
        )
        return tuple(files), skills

    def _load_all(self) -> tuple[dict[str, AgentDefinition], dict[str, dict[str, str]]]:
        raw_manifests: list[tuple[Path, dict]] = []
        errors: dict[str, dict[str, str]] = {}

        def record_error(path: Path, exc: DefinitionValidationError) -> None:
            errors[path.parent.name] = {
                "agent_id": path.parent.name,
                "path": path.relative_to(self.profiles_root).as_posix(),
                "message": str(exc),
            }

        if self.profiles_root.is_dir():
            for manifest_path in sorted(self.profiles_root.glob("*/agent.yaml")):
                try:
                    raw = self._read_yaml(manifest_path)
                    raw_manifests.append((manifest_path, raw))
                except DefinitionValidationError as exc:
                    record_error(manifest_path, exc)

        ids = [str(raw.get("id") or "") for _, raw in raw_manifests]
        duplicates = {agent_id for agent_id, count in Counter(ids).items() if agent_id and count > 1}

        known_skills = {
            child.name
            for root in self.skill_roots
            if root.is_dir()
            for child in root.iterdir()
            if child.is_dir()
        }
        definitions: dict[str, AgentDefinition] = {}
        for manifest_path, raw in raw_manifests:
            try:
                if str(raw.get("id") or "") in duplicates:
                    raise DefinitionValidationError(f"智能体 ID 重复: {raw['id']}")
                definition = self._load_profile(manifest_path, raw, known_skills)
                definitions[definition.manifest.id] = definition
            except DefinitionValidationError as exc:
                record_error(manifest_path, exc)
        return definitions, errors

    def _load_profile(self, manifest_path: Path, raw: dict, known_skills: set[str]) -> AgentDefinition:
        try:
            manifest = AgentManifest.model_validate(raw)
        except ValidationError as exc:
            raise DefinitionValidationError(
                f"智能体配置无效 {manifest_path}: {exc.errors(include_url=False)}"
            ) from exc
        if manifest_path.parent.name != manifest.id:
            raise DefinitionValidationError(
                f"智能体目录与 ID 不一致: {manifest_path.parent.name} != {manifest.id}"
            )
        unknown_toolsets = sorted(set(manifest.toolsets) - self.known_toolsets)
        if unknown_toolsets:
            raise DefinitionValidationError(f"未知 toolset: {', '.join(unknown_toolsets)}")
        unknown_skills = sorted(set(manifest.skills) - known_skills)
        if unknown_skills:
            raise DefinitionValidationError(f"未知 Skill: {', '.join(unknown_skills)}")
        if manifest.tools:
            from agent_runtime.tools.catalog import ToolCatalog

            unknown_tools = sorted(set(manifest.tools) - set(ToolCatalog.default().names))
            if unknown_tools:
                raise DefinitionValidationError(f"未知工具: {', '.join(unknown_tools)}")
        instructions_path = manifest_path.parent / "instructions.md"
        try:
            instructions = _atomic_read_text(instructions_path)
        except (OSError, UnicodeError) as exc:
            raise DefinitionValidationError(f"无法读取智能体 instructions: {instructions_path}") from exc
        if not instructions.strip():
            raise DefinitionValidationError(f"智能体 instructions 不能为空: {manifest.id}")
        return AgentDefinition(manifest=manifest, instructions=instructions, profile_dir=manifest_path.parent)

    @staticmethod
    def _read_yaml(path: Path) -> dict:
        try:
            loaded = yaml.safe_load(_atomic_read_text(path))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise DefinitionValidationError(f"无法解析智能体配置: {path}") from exc
        if not isinstance(loaded, dict):
            raise DefinitionValidationError(f"智能体配置必须是 YAML 对象: {path}")
        return loaded


def _atomic_read_text(path: Path) -> str:
    for _ in range(3):
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size):
            return content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    raise OSError(f"文件在读取期间持续变化: {path}")
