from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from agents import RunContextWrapper
from agents.testing import ScriptedModel, ModelStep, function_call, assistant_message
from agent_runtime.service import RunService
from tests.agent_runtime import test_run_service as fixtures


class ProgressiveToolsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RunServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_followup_turn_restores_discovered_tools_after_service_restart(self):
        first = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "read_skill", {"skill_id": "office-files"}, call_id="office"
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("ready")]),
            ]
        )
        service = RunService(
            db_path=self.fixture.db_path, model_provider=lambda _: first
        )
        events = asyncio.run(
            fixtures._collect(service.start(self.fixture._request("discover")))
        )
        self.assertEqual(events[-1].type, "run.completed", events[-1].payload)
        followup = ScriptedModel(
            [
                ModelStep(
                    output=[function_call("list_datasets", {}, call_id="datasets")]
                ),
                ModelStep(output=[assistant_message("done")]),
            ]
        )
        reloaded = RunService(
            db_path=self.fixture.db_path, model_provider=lambda _: followup
        )
        request = self.fixture._request("followup")
        events = asyncio.run(fixtures._collect(reloaded.start(request)))
        self.assertEqual(events[-1].type, "run.completed", events[-1].payload)
        result = next(
            e.payload["output"]
            for e in events
            if e.type == "tool.completed" and e.payload["tool_id"] == "datasets"
        )
        self.assertTrue(result["success"], result)
        # Discovery carries tool availability, never old SOP instructions or pins.
        self.assertEqual(
            reloaded.skill_runtime.get_snapshot("followup").loaded_refs, []
        )
        for isolated in [
            replace(request, conversation_id="other"),
            replace(request, model_id="other"),
            replace(request, project_id="other"),
        ]:
            agent, context = reloaded._build_agent(isolated)
            tools = asyncio.run(agent.get_all_tools(RunContextWrapper(context=context)))
            self.assertNotIn("list_datasets", [tool.name for tool in tools])

    def test_default_has_eight_tools_and_skill_enables_only_authorized_groups(self):
        service = RunService(
            db_path=self.fixture.db_path, model_provider=lambda _: ScriptedModel([])
        )
        agent, context = service._build_agent(self.fixture._request("progressive"))

        async def check():
            first = await agent.get_all_tools(RunContextWrapper(context=context))
            self.assertEqual(len(first), 8)
            self.assertNotIn("query_dataset", [tool.name for tool in first])
            from tests.agent_runtime.test_tool_catalog import _invoke

            tool = next(tool for tool in first if tool.name == "read_skill")
            await _invoke(tool, context, {"skill_id": "data-reconcile"})
            enabled = {
                tool.name
                for tool in await agent.get_all_tools(
                    RunContextWrapper(context=context)
                )
            }
            self.assertIn("query_dataset", enabled)
            self.assertIn("reconcile_variables", enabled)

        asyncio.run(check())

    def test_skill_then_office_tool_runs_in_same_sdk_session(self):
        import openpyxl

        path = self.fixture.root / "book.xlsx"
        book = openpyxl.Workbook()
        book.active["A1"] = 123
        book.save(path)
        book.close()
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "read_skill", {"skill_id": "office-files"}, call_id="skill"
                        )
                    ]
                ),
                ModelStep(
                    output=[
                        function_call(
                            "read_sheet_range",
                            {
                                "path": path.name,
                                "sheet": "Sheet",
                                "cell_range": "A1:A1",
                            },
                            call_id="excel",
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("done")]),
            ]
        )
        service = RunService(
            db_path=self.fixture.db_path, model_provider=lambda _: model
        )
        events = asyncio.run(
            fixtures._collect(service.start(self.fixture._request("office")))
        )
        self.assertEqual(events[-1].type, "run.completed")
        result = next(
            e.payload["output"]
            for e in events
            if e.type == "tool.completed" and e.payload["tool_id"] == "excel"
        )
        self.assertEqual(result["cells"][0]["value"], 123)
        reloaded = RunService(
            db_path=self.fixture.db_path, model_provider=lambda _: ScriptedModel([])
        )
        self.assertIn("office", reloaded._load_request("office").tool_groups)


if __name__ == "__main__":
    unittest.main()
