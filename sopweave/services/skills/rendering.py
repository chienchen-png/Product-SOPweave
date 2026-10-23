"""One deterministic compiler for runtime Markdown and frontend details."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import yaml
from agent_runtime.definitions.skill_format import validate_skill_document
from services.skills.contracts import SkillProblem, SkillSpec


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CompiledSkill:
    content: str
    body: str
    content_digest: str
    detail: dict


def tool_index():
    from agent_runtime.tools.catalog import ToolCatalog

    return {
        tool["name"]: group["category"]
        for group in ToolCatalog.default().describe()
        for tool in group["tools"]
    }


def compile_skill(spec: SkillSpec, *, skill_id: str) -> CompiledSkill:
    tools = tool_index()
    if unknown := set(spec.required_tools) - tools.keys():
        raise SkillProblem(f"引用了不存在的工具：{', '.join(sorted(unknown))}")
    groups = sorted({tools[name] for name in spec.required_tools})
    metadata = dict(name=skill_id, description=spec.description, toolsets=groups)
    metadata["allowed-tools"] = " ".join(spec.required_tools)
    lines = [f"# {spec.title}", "", "## 职责", "", spec.description, "", "## 输入", ""]
    lines += [
        f"- {v.name}：{v.description}（{'必填' if v.required else '可选'}）"
        for v in spec.inputs
    ] or ["- 按当前任务提供输入。"]
    lines += ["", "## 输出", ""] + [f"- {v}" for v in spec.outputs]
    for n, step in enumerate(spec.steps, 1):
        lines += ["", f"### 步骤 {n}: {step.title}", "", step.instruction]
        if step.tool_ids:
            lines += ["工具：" + "、".join(step.tool_ids)]
    lines += [
        "",
        "## 程序执行约束",
        "",
        "以下策略由宿主执行，模型不得放宽或绕过。",
        "```json",
        canonical(spec.execution_policy.model_dump()),
        "```",
        "",
        "## 提示性规则",
        "",
    ]
    lines += [f"- {v}" for v in spec.advisory_rules]
    body = "\n".join(lines).rstrip() + "\n"
    content = (
        "---\n"
        + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
        + "---\n"
        + body
    )
    try:
        validate_skill_document(content)
    except ValueError as exc:
        raise SkillProblem(str(exc)) from exc
    detail = dict(
        name=skill_id,
        title=spec.title,
        description=spec.description,
        purpose=spec.description,
        steps=[
            dict(
                n=n,
                **v.model_dump(),
                summary=v.instruction,
                branch_point=False,
                branches=[],
            )
            for n, v in enumerate(spec.steps, 1)
        ],
        inputs=[v.model_dump() for v in spec.inputs],
        outputs=spec.outputs,
        constraints=canonical(spec.execution_policy.model_dump()),
        execution_policy=spec.execution_policy.model_dump(),
        advisory_rules=spec.advisory_rules,
        capabilities=dict(models=[], stats=[], charts=[]),
        required_tools=spec.required_tools,
        toolsets=groups,
        trigger_words=[],
        user_invocable=True,
        step_count=len(spec.steps),
        origin="user",
        readonly=False,
        path=f"skills/{skill_id}/SKILL.md",
        content=content,
        body=body.strip(),
    )
    return CompiledSkill(content, body, digest_text(content), detail)
