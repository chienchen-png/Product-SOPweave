from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from .contracts import Arguments, typed_tool


class Option(Arguments):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=1000)


class Question(Arguments):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=2000)
    kind: Literal["text", "single", "multiple", "confirm"] = "text"
    options: list[Option] = Field(default_factory=list, max_length=20)
    required: bool = True
    allow_custom: bool = True

    @model_validator(mode="after")
    def validate_options(self):
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("选项 ID 不能重复")
        if self.kind in {"single", "multiple"} and not self.options:
            raise ValueError("选择题需要非空 options")
        if self.kind in {"text", "confirm"} and self.options:
            raise ValueError("文本和确认问题不接受选项")
        return self


class AskUser(Arguments):
    questions: list[Question] = Field(
        min_length=1,
        max_length=5,
        description="相关问题组合为同一表单，不重复询问已知信息",
    )

    @model_validator(mode="after")
    def unique_ids(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("问题 ID 不能重复")
        return self


async def ask_user(context, arguments):
    return {
        "status": "input_required",
        "questions": [question.model_dump() for question in arguments.questions],
    }


def specs():
    return [
        typed_tool(
            "ask_user",
            "通过问题组收集必要信息，支持单选、多选、文本和确认。此工具不授予操作权限。",
            AskUser,
            ask_user,
            group="interaction",
        )
    ]
