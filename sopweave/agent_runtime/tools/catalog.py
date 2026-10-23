from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
import uuid
import time

from agents import FunctionTool
from agents.strict_schema import ensure_strict_json_schema
from pydantic import BaseModel, ValidationError

from agent_runtime.context import WorkbenchRunContext
from agent_runtime.definitions.agent_registry import AgentDefinition
from agent_runtime.workspace import resolve_path
from config.settings import AGENT_EXECUTOR

DEFAULT_TOOLSETS = frozenset({"core", "skills", "interaction"})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    toolset: str
    input_model: type[BaseModel]
    invoke: Callable
    timeout: float = 120.0
    risk: str = "read"

    @property
    def params_json_schema(self):
        return ensure_strict_json_schema(
            _inline_schema(self.input_model.model_json_schema()),
            _reject_open_objects=True,
        )


class ToolCatalog:
    def __init__(self, specs: list[ToolSpec]):
        self._specs = {spec.name: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ValueError("工具名称重复")
        if any(
            spec.risk not in {"read", "write", "data_change", "command"}
            for spec in specs
        ):
            raise ValueError("工具必须声明有效的副作用类别")

    @classmethod
    def default(cls):
        from . import analysis, core, datasets, interaction, office, skills,skill_authoring

        return cls(
            [
                spec
                for module in (core, office, datasets, analysis, skills, interaction,skill_authoring)
                for spec in module.specs()
            ]
        )

    @property
    def names(self):
        return tuple(self._specs)

    def describe(self, agent: AgentDefinition | None = None):
        groups = {}
        for spec in self._selected_specs(agent) if agent else self._specs.values():
            groups.setdefault(spec.toolset, []).append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "risk": spec.risk,
                    "loading": "on_demand"
                    if agent
                    and agent.manifest.progressive_tools
                    and spec.toolset not in DEFAULT_TOOLSETS
                    else "default",
                }
            )
        return [
            {"category": category, "tools": tools}
            for category, tools in sorted(groups.items())
        ]

    def build(self, agent: AgentDefinition, context: WorkbenchRunContext):
        return [
            self._build_tool(spec, context, agent.manifest.approval_policy)
            for spec in self._selected_specs(agent)
        ]

    def _selected_specs(self, agent: AgentDefinition):
        manifest = agent.manifest
        explicit = set(manifest.tools)
        if unknown := explicit - set(self._specs):
            raise ValueError(f"智能体引用了未知工具: {sorted(unknown)}")
        groups = set(manifest.toolsets)
        return [
            spec
            for spec in self._specs.values()
            if spec.name!='prepare_skill_draft' or manifest.allow_user_skills
            if (
                spec.toolset in groups
                or "workbench" in groups
                or spec.name in explicit
            )
            and (not explicit or spec.name in explicit)
        ]

    def decision(self, spec, context, arguments, approval_policy=None):
        sop_confirmation = False
        if context.skill_runtime:
            result = context.skill_runtime.policy_decision(context.run_id,spec.name,spec.risk,arguments,context.work_dir)
            if result.action=='deny':
                return 'deny'
            if result.action=='confirm':
                sop_confirmation = True
        try:
            for name in ("path", "chart_file"):
                if arguments.get(name):
                    resolve_path(context.work_dir, arguments[name])
        except (ValueError, OSError):
            return "deny"
        if context.progressive_tools and spec.toolset not in context.active_toolsets:
            return "deny"
        for denied in context.denied_operations:
            if spec.name == "run_process":
                return "deny"
            if spec.risk != "read" and any(
                arguments.get(key)
                and arguments.get(key) == denied.get("arguments", {}).get(key)
                for key in ("path", "file")
            ):
                return "deny"
        if spec.name == "run_process":
            from tools.command_policy import _check_blacklist

            if arguments.get("program") == "shell" and _check_blacklist(
                arguments.get("code", "")
            ):
                return "deny"
            # Local arbitrary execution has explicit human authorization.
            if AGENT_EXECUTOR == "local":
                return "confirm"
        configured = (approval_policy or {}).get(spec.name)
        if configured == "always" or sop_confirmation:
            return "confirm"
        if context.work_mode == "auto":
            return "auto"
        if spec.risk != "read" or configured == "work_mode":
            return "confirm"
        return "auto"

    def _build_tool(self, spec, bound_context, policies):
        def context_from(wrapper):
            candidate = getattr(wrapper, "context", None)
            return (
                candidate
                if isinstance(candidate, WorkbenchRunContext)
                else bound_context
            )

        async def needs_approval(wrapper, arguments, _call_id):
            try:
                arguments = spec.input_model.model_validate(arguments).model_dump()
            except ValidationError:
                return False
            return (
                self.decision(spec, context_from(wrapper), arguments, policies)
                == "confirm"
            )

        async def invoke(wrapper, raw_arguments):
            context = context_from(wrapper)
            if context.event_callback:
                sink = context.event_callback
                context = replace(
                    context,
                    event_callback=lambda kind, payload: sink(
                        kind,
                        {
                            **payload,
                            "tool_id": getattr(wrapper, "tool_call_id", ""),
                            "name": spec.name,
                        },
                    ),
                )
            try:
                arguments = spec.input_model.model_validate_json(
                    raw_arguments
                ).model_dump()
            except ValidationError as exc:
                return {
                    "success": False,
                    "status": "error",
                    "error_code": "invalid_arguments",
                    "errors": exc.errors(include_input=False, include_context=False),
                }
            if self.decision(spec, context, arguments, policies) == "deny":
                reason = context.skill_runtime.policy_decision(context.run_id,spec.name,spec.risk,arguments,context.work_dir).reason if context.skill_runtime else ''
                return {
                    "success": False,
                    "status": "error",
                    "error_code": "tool_denied",
                    "message": reason or "工具调用超出授权能力或工作目录",
                }
            try:
                result = await asyncio.wait_for(
                    spec.invoke(context, arguments), timeout=spec.timeout
                )
            except TimeoutError:
                return {
                    "success": False,
                    "status": "error",
                    "error_code": "tool_timeout",
                }
            except (ValueError, OSError, KeyError) as exc:
                return {
                    "success": False,
                    "status": "error",
                    "error_code": "tool_error",
                    "message": str(exc),
                }
            if context.skill_runtime:
                context.skill_runtime.record_result(context.run_id,spec.name,result)
            result = _jsonable(result)
            if isinstance(result, dict):
                result.setdefault(
                    "status", "error" if result.get("success") is False else "success"
                )
                if result.get("success") is False:
                    result.setdefault("error_code", "business_error")
                encoded = json.dumps(result, ensure_ascii=False)
                if len(encoded) > 48000 and spec.name != 'read_skill':
                    from agent_runtime.workspace import atomic_bytes

                    relative = f".agent-artifacts/{spec.name}-{uuid.uuid4().hex}.json"
                    atomic_bytes(
                        resolve_path(context.work_dir, relative),
                        encoded.encode("utf-8"),
                        "missing",
                    )
                    result = {
                        "success": result.get("success", True),
                        "status": result["status"],
                        "truncated": True,
                        "summary": "完整结果已保存为 JSON 产物，请缩小查询或按需读取产物。",
                        "artifact": relative,
                        "preview": encoded[:6000],
                        **{
                            key: result[key]
                            for key in ("revision", "version", "error_code")
                            if key in result
                        },
                    }
            return result

        async def timed_invoke(wrapper, raw_arguments):
            started = time.monotonic()
            try:
                return await invoke(wrapper, raw_arguments)
            finally:
                call_id = getattr(wrapper, "tool_call_id", "")
                if call_id:
                    context_from(wrapper).tool_durations[call_id] = max(
                        1, round((time.monotonic() - started) * 1000)
                    )

        return FunctionTool(
            name=spec.name,
            description=spec.description,
            params_json_schema=spec.params_json_schema,
            on_invoke_tool=timed_invoke,
            strict_json_schema=True,
            needs_approval=needs_approval,
            is_enabled=lambda wrapper, _agent: (
                (spec.name!='prepare_skill_draft' or (context_from(wrapper).skill_runtime is not None and context_from(wrapper).skill_registry.library is not None))
                and (not bound_context.progressive_tools
                or spec.toolset in context_from(wrapper).active_toolsets
                )
            ),
        )


def _jsonable(value):
    def default(item):
        if isinstance(item, Path):
            return str(item)
        if hasattr(item, "item"):
            return item.item()
        if hasattr(item, "to_dict"):
            return item.to_dict()
        return str(item)

    return json.loads(json.dumps(value, ensure_ascii=False, default=default))


def _inline_schema(schema):
    """Finite local references become portable strict FunctionTool schemas."""

    def expand(value, seen=()):
        if isinstance(value, list):
            return [expand(item, seen) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            ref = value["$ref"]
            if not ref.startswith("#/$defs/") or ref in seen:
                raise ValueError("工具参数不支持递归或外部 schema 引用")
            target = schema["$defs"][ref.split("/")[-1]]
            return expand(
                {
                    **target,
                    **{key: value for key, value in value.items() if key != "$ref"},
                },
                (*seen, ref),
            )
        return {
            key: expand(item, seen) for key, item in value.items() if key != "$defs"
        }

    return expand(schema)
