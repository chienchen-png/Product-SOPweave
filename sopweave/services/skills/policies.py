"""Finite, host-enforced financial rules; no natural-language rule execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from decimal import InvalidOperation
from agent_runtime.workspace import resolve_path, file_version
from services.money import parse_amount
from services.skills.contracts import SkillProblem, SkillSpec
from services.skills.rendering import tool_index

SUPPORTED_CHECKS = frozenset(
    {
        "required_input",
        "amount_precision",
        "source_version",
        "approval",
        "reconciliation_complete",
    }
)
SYSTEM_TOOLS = frozenset(
    {
        "list_files",
        "search_text",
        "read_text",
        "read_skill",
        "ask_user",
        "prepare_skill_draft",
    }
)


@dataclass(frozen=True)
class SkillOperation:
    name: str
    risk: str
    arguments: dict
    work_dir: Path
    inputs: dict = field(default_factory=dict)
    completed_checks: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason: str = ""


@dataclass(frozen=True)
class SkillPolicy:
    spec: SkillSpec

    def evaluate(self, operation: SkillOperation, *, snapshot=None) -> PolicyDecision:
        policy = self.spec.execution_policy
        if operation.risk == "command":
            return PolicyDecision(
                "deny", "受控 SOP 禁止任意命令，请使用已验证的业务工具"
            )
        if (
            operation.name not in SYSTEM_TOOLS
            and operation.name not in self.spec.required_tools
        ):
            return PolicyDecision("deny", "工具未声明为当前 SOP 所需能力")
        if operation.risk == "read" or operation.name == "prepare_skill_draft":
            return PolicyDecision("allow")
        missing = [
            name for name in policy.required_inputs if not operation.inputs.get(name)
        ]
        if missing:
            return PolicyDecision("deny", "缺少必填输入：" + "、".join(missing))
        args = operation.arguments
        target = args.get("path") or args.get("file")
        if target:
            try:
                actual = resolve_path(operation.work_dir, target)
                allowed = [
                    (
                        resolve_path(operation.work_dir, path),
                        path.endswith(("/", "\\")),
                    )
                    for path in policy.write_paths
                ]
                if not any(
                    actual == allowed_path
                    or (
                        is_directory
                        and actual.is_relative_to(allowed_path)
                    )
                    for allowed_path, is_directory in allowed
                ):
                    return PolicyDecision("deny", "写入目标不在当前 SOP 允许范围")
            except (OSError, ValueError):
                return PolicyDecision("deny", "SOP 写入路径无效")
        elif operation.risk != "read":
            return PolicyDecision("deny", "无法核验当前操作的实际写入目标")
        for source in policy.source_versions:
            try:
                if (
                    file_version(resolve_path(operation.work_dir, source.path))
                    != source.version
                ):
                    return PolicyDecision("deny", "源文件版本冲突，请重新开始任务")
            except (OSError, ValueError):
                return PolicyDecision("deny", "无法核验源文件版本")
        if (
            "reconciliation_complete" in policy.checks
            and "reconciliation_complete" not in operation.completed_checks
        ):
            return PolicyDecision("deny", "尚未完成程序核对，不能写回")
        values = [(v.get("field"), v.get("value")) for v in args.get("values", [])]
        values += [
            (f"{v.get('sheet')}!{v.get('cell')}", v.get("value"))
            for v in args.get("changes", [])
        ]
        for row in args.get("rows", []):
            values += [(v.get("field"), v.get("value")) for v in row.get("values", [])]
        for name, value in values:
            if name in policy.amount_fields:
                try:
                    amount = parse_amount(value)
                    if amount != amount.quantize(
                        parse_amount(10) ** -policy.decimal_places
                    ):
                        return PolicyDecision("deny", "金额超过当前 SOP 允许的小数位")
                except (ValueError, InvalidOperation):
                    return PolicyDecision("deny", "金额不是有效十进制数")
        if operation.name in policy.require_approval_for:
            return PolicyDecision("confirm", "当前 SOP 要求用户审批")
        return PolicyDecision("allow")


def compile_policy(spec: SkillSpec, *, catalog=None) -> SkillPolicy:
    unknown = set(spec.execution_policy.checks) - SUPPORTED_CHECKS
    if unknown:
        raise SkillProblem("尚未实现的强制校验：" + "、".join(sorted(unknown)))
    names = tool_index()
    if set(spec.execution_policy.require_approval_for) - names.keys():
        raise SkillProblem("审批规则引用了不存在的工具")
    if any(
        not path or Path(path).is_absolute() or ":" in path or ".." in Path(path).parts
        for path in spec.execution_policy.write_paths
    ):
        raise SkillProblem("SOP 写入目标必须是工作区内的相对路径")
    return SkillPolicy(spec)


def enforce_operation(context, name, risk, arguments):
    if context.skill_runtime:
        decision = context.skill_runtime.policy_decision(
            context.run_id, name, risk, arguments, context.work_dir
        )
        if decision.action == "deny":
            raise SkillProblem(decision.reason)
