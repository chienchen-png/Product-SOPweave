"""Strict contracts shared by the skill library, authoring API and runtime."""

from __future__ import annotations

from typing import Literal
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class SkillProblem(ValueError):
    status = 422
    code = "skill_invalid"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class SkillConflict(SkillProblem):
    status = 409
    code = "skill_conflict"


class SkillMissing(SkillProblem):
    status = 404
    code = "skill_not_found"


class SkillReadonly(SkillProblem):
    status = 403
    code = "skill_readonly"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    required: bool = True
    default: str = Field(default="", max_length=1000)


class SkillStep(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    title: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=4000)
    tool_ids: list[str] = Field(default_factory=list, max_length=30)


class SourceVersion(StrictModel):
    path: str = Field(min_length=1, max_length=400)
    version: str = Field(min_length=1, max_length=128)


class ExecutionPolicy(StrictModel):
    currency: str = Field(default="", max_length=20)
    decimal_places: int = Field(default=2, ge=0, le=6)
    tolerance: str = Field(default="", max_length=40)
    required_inputs: list[str] = Field(default_factory=list, max_length=40)
    require_approval_for: list[str] = Field(default_factory=list, max_length=30)
    write_paths: list[str] = Field(default_factory=list, max_length=30)
    source_versions: list[SourceVersion] = Field(default_factory=list, max_length=30)
    amount_fields: list[str] = Field(default_factory=list, max_length=30)
    checks: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("tolerance")
    @classmethod
    def valid_tolerance(cls, value):
        if value:
            try:
                number = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError("容差必须是十进制金额") from exc
            if not number.is_finite() or number < 0:
                raise ValueError("容差必须为有限非负金额")
        return value


class SkillSpec(StrictModel):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    inputs: list[SkillInput] = Field(default_factory=list, max_length=40)
    outputs: list[str] = Field(default_factory=list, max_length=30)
    required_tools: list[str] = Field(default_factory=list, max_length=30)
    steps: list[SkillStep] = Field(min_length=1, max_length=100)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    advisory_rules: list[str] = Field(default_factory=list, max_length=40)
    blockers: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_references(self):
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)) or len(self.required_tools) != len(
            set(self.required_tools)
        ):
            raise ValueError("步骤 ID 和工具名称不能重复")
        if any(set(step.tool_ids) - set(self.required_tools) for step in self.steps):
            raise ValueError("步骤只能引用已声明的所需工具")
        return self


class SkillRef(StrictModel):
    skill_id: str
    origin: Literal["builtin", "user"]
    version: int | str
    content_digest: str


class SopSelection(StrictModel):
    mode: Literal["auto", "none", "explicit"] = "auto"
    skill_id: str = ""

    @model_validator(mode="after")
    def selection_matches_id(self):
        if (self.mode == "explicit") != bool(self.skill_id):
            raise ValueError("指定 SOP 时必须选择 Skill，其它模式不能携带 Skill ID")
        return self


class RunSkillSnapshot(StrictModel):
    schema_version: Literal[1] = 1
    mode: Literal["auto", "none", "explicit"] = "auto"
    catalog_generation: int = 0
    advertised_refs: list[SkillRef] = Field(default_factory=list)
    explicit_ref: SkillRef | None = None
    loaded_refs: list[SkillRef] = Field(default_factory=list)
    policy_digest: str = ""
    inputs: dict[str, str] = Field(default_factory=dict)
    resolved_sources: list[SourceVersion] = Field(default_factory=list)
    completed_checks: list[str] = Field(default_factory=list)


class DraftQuestion(StrictModel):
    question_id: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=1000)
    required: bool = True
    options: list[str] = Field(default_factory=list, max_length=10)


class DraftAnswer(StrictModel):
    question_id: str
    value: str = Field(max_length=4000)
