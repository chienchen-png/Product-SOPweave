from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from agents import FunctionTool, RunContextWrapper
from agents.tool_context import ToolContext

from agent_runtime.context import WorkbenchRunContext
from agent_runtime.definitions.agent_registry import AgentDefinition, AgentManifest
from agent_runtime.tools.catalog import ToolCatalog


class ToolCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work_dir = Path(self.temp.name).resolve()
        self.context = WorkbenchRunContext(
            project_id="project-1",
            work_dir=self.work_dir,
            run_id="run-1",
            conversation_id="conversation-1",
            work_mode="manual",
            agent_id="general-agent",
        )
        self.catalog = ToolCatalog.default()

    def test_names_are_unique_and_agent_permissions_filter_tools(self) -> None:
        self.assertEqual(len(self.catalog.names), len(set(self.catalog.names)))
        agent = _agent(toolsets=["core"], tools=["read_text"])

        tools = self.catalog.build(agent, self.context)

        self.assertEqual([tool.name for tool in tools], ["read_text"])
        self.assertTrue(all(isinstance(tool, FunctionTool) for tool in tools))
        self.assertNotIn("run_process", json.dumps([tool.params_json_schema for tool in tools]))

    def test_parameter_schemas_are_strict_and_have_no_open_objects(self) -> None:
        tools = self.catalog.build(_agent(), self.context)

        for tool in tools:
            with self.subTest(tool=tool.name):
                self.assertFalse(_has_open_object(tool.params_json_schema))
                self.assertFalse(tool.params_json_schema.get("additionalProperties", True))

    def test_tool_outputs_are_json_serializable_and_context_is_injected(self) -> None:
        target = self.work_dir / "hello.txt"
        target.write_text("你好", encoding="utf-8")
        tool = _by_name(self.catalog.build(_agent(), self.context), "read_text")

        result = asyncio.run(_invoke(tool, self.context, {"path": "hello.txt"}))

        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "你好")
        json.dumps(result, ensure_ascii=False)
        self.assertNotIn("work_dir", tool.params_json_schema["properties"])

    def test_manual_mode_approval_covers_boundary_and_data_changes(self) -> None:
        tools = self.catalog.build(_agent(), self.context)
        read = _by_name(tools, "read_text")
        update = _by_name(tools, "apply_data_changes")
        wrapper = RunContextWrapper(context=self.context)

        inside = asyncio.run(read.needs_approval(wrapper, {"path": "inside.txt"}, "c1"))
        outside = asyncio.run(read.needs_approval(wrapper, {"path": "../outside.txt"}, "c2"))
        data_change = asyncio.run(update.needs_approval(wrapper, {'file':'a.xlsx','operation':'update','expected_revision':'r',
            'filters':[{'field':'code','value':'A'}],'values':[{'field':'amount','value':1}]}, "c3"))

        self.assertFalse(inside)
        self.assertFalse(outside)  # denied rather than an approval that could widen the workspace
        denied=asyncio.run(_invoke(read,self.context,{'path':'../outside.txt'}))
        self.assertEqual(denied['error_code'],'tool_denied')
        self.assertTrue(data_change)

    def test_blacklisted_commands_are_rejected_in_manual_and_auto_modes(self) -> None:
        command = "shutdown /s"
        for mode in ("manual", "auto"):
            context = self.context.with_work_mode(mode)
            tool = _by_name(self.catalog.build(_agent(), context), "run_process")
            result = asyncio.run(_invoke(tool, context, {'program':'shell','code':command,'reason':'test'}))
            self.assertFalse(result["success"])
            self.assertEqual(result["error_code"], "tool_denied")

    def test_auto_mode_skips_boundary_and_data_change_approval(self) -> None:
        context = self.context.with_work_mode("auto")
        tools = self.catalog.build(_agent(), context)
        wrapper = RunContextWrapper(context=context)

        self.assertFalse(asyncio.run(
            _by_name(tools, "read_text").needs_approval(
                wrapper, {"path": "../outside.txt"}, "c1"
            )
        ))
        self.assertFalse(asyncio.run(
            _by_name(tools, "apply_data_changes").needs_approval(
                wrapper, {'file':'a.xlsx','operation':'delete','expected_revision':'r','filters':[{'field':'code','value':'A'}]}, "c2"
            )
        ))

    def test_skill_tool_uses_the_active_agent_identity(self) -> None:
        tool = _by_name(
            self.catalog.build(_agent(tools=["read_skill"]), self.context),
            "read_skill",
        )

        result = asyncio.run(_invoke(
            tool, self.context, {"skill_id": "data-reconcile"}
        ))

        self.assertTrue(result["success"])
        self.assertIn("# 数据核对 Skill", result["content"])

    def test_ask_user_returns_a_structured_pause_payload(self) -> None:
        tool = _by_name(
            self.catalog.build(_agent(tools=["ask_user"]), self.context),
            "ask_user",
        )

        result = asyncio.run(_invoke(tool, self.context, {
            'questions':[{'id':'continue','title':'是否继续？','kind':'confirm'}],
        }))

        self.assertEqual(result["status"], "input_required")
        self.assertEqual(result['questions'][0]['title'], '是否继续？')


def _agent(
    *, toolsets: list[str] | None = None, tools: list[str] | None = None
) -> AgentDefinition:
    manifest = AgentManifest(
        id="general-agent",
        name="General",
        version="1.0.0",
        description="General",
        enabled=True,
        default_model_id="",
        toolsets=toolsets or ["workbench"],
        tools=tools or [],
        skills=[],
        approval_policy={},
        max_turns=20,
    )
    return AgentDefinition(manifest, "Instructions", Path("."))


def _by_name(tools: list[FunctionTool], name: str) -> FunctionTool:
    return next(tool for tool in tools if tool.name == name)


async def _invoke(tool: FunctionTool, context: WorkbenchRunContext, arguments: dict):
    raw = json.dumps(arguments, ensure_ascii=False)
    wrapper = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-1",
        tool_arguments=raw,
    )
    return await tool.on_invoke_tool(wrapper, raw)


def _has_open_object(value) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "object" and value.get("additionalProperties", False) is True:
            return True
        if value.get("items") == {}:
            return True
        return any(_has_open_object(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_open_object(item) for item in value)
    return False


if __name__ == "__main__":
    unittest.main()
