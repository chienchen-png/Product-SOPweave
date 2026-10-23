from __future__ import annotations

from agent_runtime.definitions.skill_registry import SkillRegistry
from pydantic import Field, model_validator
from .contracts import Arguments, typed_tool


class ReadSkill(Arguments):
    skill_id: str = Field(
        default='', description="当前智能体授权的 Skill ID，与 query 二选一"
    )
    query: str = Field(default='',max_length=1000,description='搜索可用 Skill 的功能和名称，每次最多20项')
    resource_path: str = Field(
        default="",
        description="空值读取正文；非空读取该 Skill 的 references/scripts/assets 相对路径",
    )


    @model_validator(mode='after')
    def one_operation(self):
        if bool(self.skill_id)==bool(self.query) or (self.resource_path and not self.skill_id):
            raise ValueError('skill_id 与 query 必须二选一，资源路径仅用于 Skill 加载')
        return self


async def read_skill(context, args):
    registry = context.skill_registry or SkillRegistry()
    if args.query:
        items = context.skill_runtime.search_for_run(context.run_id,query=args.query) if context.skill_runtime else registry.search_for_agent(context.agent_id,query=args.query)
        return {'success':True,'skills':[s.to_dict() for s in items]}
    if context.skill_runtime:
        loaded = context.skill_runtime.load_for_run(context.run_id,args.skill_id,resource_path=args.resource_path)
        context.active_toolsets.update(set(loaded.toolsets)&context.allowed_toolsets)
        return {'success':True,'skill_id':args.skill_id,'content':loaded.content,'ref':loaded.ref.model_dump(),'active_toolsets':sorted(context.active_toolsets)}
    content = (
        registry.read_resource(context.agent_id, args.skill_id, args.resource_path)
        if args.resource_path
        else registry.read_skill(context.agent_id, args.skill_id)
    )
    requested = set(registry.get(args.skill_id).manifest.toolsets)
    context.active_toolsets.update(requested & context.allowed_toolsets)
    return {
        "success": True,
        "skill_id": args.skill_id,
        "content": content,
        "active_toolsets": sorted(context.active_toolsets),
    }


def specs():
    return [
        typed_tool(
            "read_skill",
            "读取已授权 Skill 或资源，并激活该 Skill 需要且当前智能体获准的领域工具。",
            ReadSkill,
            read_skill,
            group="skills",
        )
    ]
