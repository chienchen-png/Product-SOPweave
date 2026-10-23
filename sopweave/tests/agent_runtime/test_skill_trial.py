import asyncio
import unittest
from agents.testing import ScriptedModel, ModelStep, function_call, assistant_message
from tests.agent_runtime.skill_fixtures import SkillTestFixture
from services.skills.trials import trial_skill


class SkillTrialTests(SkillTestFixture, unittest.TestCase):
    def test_resumed_trial_keeps_tool_evidence_from_before_approval(self):
        from services.skills.trials import resume_trial
        from agent_runtime.service import ResumeCommand
        from tests.agent_runtime.test_skill_contracts import sample_spec

        draft = self.library.create_draft()
        spec = sample_spec(
            required_tools=["read_text", "write_text"],
            steps=[
                {
                    "id": "read",
                    "title": "读取",
                    "instruction": "读取input.txt后写report.md",
                    "tool_ids": ["read_text", "write_text"],
                }
            ],
            execution_policy={
                "write_paths": ["report.md"],
                "require_approval_for": ["write_text"],
                "checks": ["approval"],
            },
        )
        draft = self.library.update_draft(
            draft["draft_id"], expected_revision=1, spec=spec.model_dump()
        )
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "read_text", {"path": "input.txt"}, call_id="read"
                        )
                    ]
                ),
                ModelStep(
                    output=[
                        function_call(
                            "write_text",
                            {
                                "path": "report.md",
                                "content": "合成报告",
                                "expected_version": "missing",
                            },
                            call_id="write",
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("完成")]),
            ]
        )

        async def run():
            first = await trial_skill(
                self.library,
                draft["draft_id"],
                model_id="test",
                model_provider=lambda _: model,
            )
            self.assertEqual(first["status"], "waiting", first)
            self.assertFalse(first["passed"])
            item = first["interactions"][0]
            return await resume_trial(
                self.library,
                first["trial_id"],
                ResumeCommand(
                    action="approve",
                    interaction_id=item["interaction_id"],
                    version=item["version"],
                ),
                model_provider=lambda _: model,
            )

        result = asyncio.run(run())
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["missing_tools"], [])
        self.assertFalse((self.root / "report.md").exists())

    def test_loading_instructions_alone_does_not_pass_trial(self):
        draft = self.ready()
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "read_skill",
                            {"skill_id": "office-files"},
                            call_id="load-only",
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("完成")]),
            ]
        )
        result = asyncio.run(
            trial_skill(
                self.library,
                draft["draft_id"],
                model_id="test",
                model_provider=lambda _: model,
            )
        )
        self.assertFalse(result["passed"], result)
        self.assertIn("read_sheet_range", result["missing_tools"])

    def test_trial_and_cancel_missing_draft_return_domain_error(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agent_runtime.skill_authoring_api import register_skill_authoring_api
        from services.skills.authoring import SkillAuthoringService

        app = FastAPI()
        register_skill_authoring_api(app, SkillAuthoringService(self.library))
        with TestClient(app) as client:
            for path, body in [("trial", {"model_id": "test"}), ("cancel", {})]:
                response = client.post("/api/skills/drafts/missing/" + path, json=body)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()["code"], "skill_not_found")

    def test_trial_uses_real_sdk_office_tools_without_touching_project(self):
        path = self.root / "bank.xlsx"
        path.write_bytes(b"real-project-data")
        draft = self.ready()
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "read_sheet_range",
                            {
                                "path": "bank.xlsx",
                                "sheet": "Sheet",
                                "cell_range": "A1:B2",
                            },
                            call_id="office",
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("样例金额270")]),
            ]
        )
        result = asyncio.run(
            trial_skill(
                self.library,
                draft["draft_id"],
                model_id="test",
                model_provider=lambda _: model,
            )
        )
        self.assertTrue(result["passed"], result)
        self.assertEqual(path.read_bytes(), b"real-project-data")
        self.assertEqual(self.library.list(), [])
        self.assertIn("read_sheet_range", [t["name"] for t in result["tools"]])

    def test_trial_cannot_escape_workspace_or_execute_external_scripts(self):
        draft = self.ready()
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "run_process",
                            {
                                "program": "python",
                                "code": 'raise RuntimeError("must not run")',
                                "reason": "检测不可绕过执行策略",
                                "timeout": 10,
                                "output_limit": 1024,
                                "artifact_paths": [],
                            },
                            call_id="command",
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("停止")]),
            ]
        )
        result = asyncio.run(
            trial_skill(
                self.library,
                draft["draft_id"],
                model_id="test",
                model_provider=lambda _: model,
            )
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["tools"][0]["output"]["error_code"], "tool_denied")
