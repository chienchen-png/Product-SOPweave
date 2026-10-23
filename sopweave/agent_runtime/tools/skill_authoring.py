"""One draft-only entry point; publication is always a host UI action."""

from typing import Literal
from pydantic import Field
from .contracts import Arguments, typed_tool
from services.skills.contracts import SkillProblem


class PrepareSkillDraft(Arguments):
    source_kind: Literal["text", "conversation", "github"] = "text"
    text: str = Field(default="", max_length=100000)
    github_url: str = Field(default="", max_length=2000)
    skill_path: str = Field(
        default="",
        max_length=400,
        description="仓库包含多个 Skill 时，从返回的 choices 中选择目录；根目录为点号",
    )
    run_ids: list[str] = Field(default_factory=list, max_length=100)


def prepare_skill_draft(context, args):
    if not context.skill_runtime or context.skill_registry.library is None:
        raise SkillProblem("当前智能体未启用用户 Skill 库")
    library = context.skill_registry.library
    manifest = context.skill_registry.agent_registry.get(context.agent_id).manifest
    if not manifest.allow_user_skills:
        raise SkillProblem("当前智能体未授权准备用户 SOP")
    if args.source_kind == "github":
        from services.skills.imports import SkillImporter

        preview = SkillImporter(library).preview_github(
            args.github_url, skill_path=args.skill_path
        )
        if "draft" not in preview:
            return {
                "success": True,
                "requires_selection": True,
                "choices": preview["choices"],
                "resolved_url": preview.get("resolved_url", ""),
                "message": "请让用户选择一个目录，再携带 skill_path 准备导入草稿。",
            }
        return {
            "success": True,
            "draft_id": preview["draft"]["draft_id"],
            "requires_user_confirmation": True,
            "warnings": preview["warnings"],
            "message": "导入草稿已准备，请先审阅原文、兼容性和适配差异，再确认发布。",
        }
    if args.source_kind == "conversation":
        from services.conversation_history import conversation_source

        with library.connection() as db:
            source = [
                conversation_source(
                    db, context.project_id, context.conversation_id, args.run_ids
                )
            ]
    else:
        source = [{"kind": "text", "text": args.text}]
    if sum(len(s["text"]) for s in source) > 100000:
        raise SkillProblem("选中的对话太长，请缩小范围")
    draft = library.create_draft(source=source)
    return {
        "success": True,
        "draft_id": draft["draft_id"],
        "requires_user_confirmation": True,
        "message": "草稿已准备，请在审阅窗口生成并确认后发布。",
    }


def specs():
    return [
        typed_tool(
            "prepare_skill_draft",
            "用户要求整理流程、创建 SOP 或保存本次对话时准备待审草稿；不能发布或删除。",
            PrepareSkillDraft,
            prepare_skill_draft,
            group="skills",
            risk="write",
        )
    ]
