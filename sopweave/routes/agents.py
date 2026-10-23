"""Agents API — agent listing and configuration.

Phase 7.4: Provides agent list and detail endpoints for frontend agent picker.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import yaml
from flask import Blueprint, current_app

from agent_runtime.definitions.agent_registry import (
    AgentNotFoundError,
    AgentRegistry,
    DefinitionValidationError,
)
from routes._utils import error, payload, success
from routes.definition_io import record_definition_audit, replace_definition_files

agents_bp = Blueprint("agents_api", __name__, url_prefix="/api/agents")


def _registry() -> AgentRegistry:
    registry = current_app.config.get("AGENT_REGISTRY")
    return registry if isinstance(registry, AgentRegistry) else AgentRegistry()


# ------------------------------------------------------------------
# GET /api/agents
# ------------------------------------------------------------------
@agents_bp.get("")
def list_agents():
    """Return enabled file-backed agent definitions."""
    try:
        registry = _registry()
        return success(
            {"agents": [summary.to_dict() for summary in registry.list_enabled()],
             "definition_errors": registry.diagnostics()}
        )
    except DefinitionValidationError as exc:
        return error(f"智能体定义无效: {exc}", 422)


# ------------------------------------------------------------------
# GET /api/agents/<agent_id>
# ------------------------------------------------------------------
@agents_bp.get("/<agent_id>")
def get_agent(agent_id: str):
    """Return a specific agent's full configuration."""
    try:
        return success({"agent": _registry().get(agent_id).to_dict()})
    except AgentNotFoundError:
        return error("智能体不存在", 404)
    except DefinitionValidationError as exc:
        return error(f"智能体定义无效: {exc}", 422)


@agents_bp.put("/<agent_id>")
def save_agent(agent_id: str):
    registry = _registry()
    try:
        current = registry.get(agent_id)
    except AgentNotFoundError:
        return error("智能体不存在", 404)
    except DefinitionValidationError as exc:
        return error(f"智能体定义无效: {exc}", 422)
    data = payload()
    instructions = str(data.get("instructions") or "")
    raw_yaml = data.get("manifest_yaml")
    if raw_yaml is None:
        manifest = data.get("manifest")
        if not isinstance(manifest, dict):
            return error("manifest 必须是对象", 422)
        raw_yaml = yaml.safe_dump(
            manifest,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    try:
        loaded = yaml.safe_load(str(raw_yaml))
    except yaml.YAMLError as exc:
        return error(f"智能体 YAML 无效: {exc}", 422)
    if not isinstance(loaded, dict) or loaded.get("id") != agent_id:
        return error("智能体 ID 必须与目录一致", 422)
    if not instructions.strip():
        return error("智能体 instructions 不能为空", 422)

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate_root = Path(temp_dir) / "agent_profiles"
            shutil.copytree(registry.profiles_root, candidate_root)
            candidate = candidate_root / agent_id
            (candidate / "agent.yaml").write_text(str(raw_yaml), encoding="utf-8")
            (candidate / "instructions.md").write_text(instructions, encoding="utf-8")
            checked = AgentRegistry(
                candidate_root,
                skill_roots=list(registry.skill_roots),
                known_toolsets=set(registry.known_toolsets),
            )
            checked.list_enabled()
            checked.get(agent_id)
    except (
        DefinitionValidationError,
        AgentNotFoundError,
        OSError,
        UnicodeError,
    ) as exc:
        return error(f"智能体定义无效: {exc}", 422)

    manifest_path = current.profile_dir / "agent.yaml"
    instructions_path = current.profile_dir / "instructions.md"
    try:
        replace_definition_files(
            {
                instructions_path: instructions,
                manifest_path: str(raw_yaml),
            }
        )
        record_definition_audit(
            "agent",
            agent_id,
            [manifest_path, instructions_path],
        )
        saved = registry.get(agent_id).to_dict()
    except (OSError, DefinitionValidationError) as exc:
        return error(f"保存智能体失败: {exc}", 500)
    return success({"agent": saved})


# ------------------------------------------------------------------
# GET /api/agents/<agent_id>/tools
# ------------------------------------------------------------------
@agents_bp.get("/<agent_id>/tools")
def get_agent_tools(agent_id: str):
    """Return the validated V2 tool catalog grouped by toolset."""
    try:
        definition = _registry().get(agent_id)
        from agent_runtime.tools.catalog import ToolCatalog

        return success(
            {
                "agent_id": agent_id,
                "catalog": ToolCatalog.default().describe(definition),
            }
        )
    except AgentNotFoundError:
        return error("智能体不存在", 404)
    except DefinitionValidationError as exc:
        return error(f"智能体定义无效: {exc}", 422)
    except Exception as exc:
        return error(f"加载工具清单失败: {exc}")
