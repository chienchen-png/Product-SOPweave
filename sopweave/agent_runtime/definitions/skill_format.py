"""Agent Skills text format, independent of registries and persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass
import yaml
from pydantic import BaseModel, ConfigDict, Field


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    user_invocable: bool = Field(default=True, alias="user-invocable")
    toolsets: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ParsedSkill:
    manifest: SkillManifest
    body: str


def parse_frontmatter(text: str) -> tuple[dict, str]:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not match:
        raise yaml.YAMLError("SKILL.md 缺少 YAML frontmatter")
    metadata = yaml.safe_load(match.group(1))
    if not isinstance(metadata, dict):
        raise yaml.YAMLError("SKILL.md frontmatter 必须是对象")
    return metadata, text[match.end() :]


def validate_skill_document(content: str) -> ParsedSkill:
    if len(content.encode("utf-8")) > 128 * 1024:
        raise ValueError("SKILL.md 超过 128 KiB，请拆分引用资源")
    metadata, body = parse_frontmatter(content)
    return ParsedSkill(SkillManifest.model_validate(metadata), body)
