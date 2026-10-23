import asyncio
import unittest
from contextlib import aclosing
from agents.testing import ScriptedModel, ModelStep, assistant_message, function_call
from agent_runtime.api import _stream_events
from agent_runtime.service import RunService, RunRequest
from tests.agent_runtime.skill_fixtures import SkillTestFixture


class SkillStreamCompletionTests(SkillTestFixture, unittest.TestCase):
    def execute_until_frontend_closes(self, model, terminal):
        service = RunService(
            db_path=self.db_path,
            skill_library=self.library,
            model_provider=lambda _: model,
        )
        request = RunRequest(
            "ui-close",
            "执行当前任务",
            "project",
            "conversation",
            "general-agent",
            "test",
            self.root,
        )

        class Transport:
            async def is_disconnected(self):
                return False

        async def run():
            async with aclosing(
                _stream_events(
                    service.start(request),
                    request=Transport(),
                    service=service,
                    run_id=request.run_id,
                )
            ) as stream:
                async for packet in stream:
                    if "event: " + terminal + "\n" in packet:
                        break

        asyncio.run(run())
        with self.library.connection() as db:
            row = db.execute(
                "SELECT status FROM runs WHERE id=?", (request.run_id,)
            ).fetchone()
        self.assertEqual(service._owned_model_clients, {})
        return row["status"]

    def test_frontend_closing_after_completion_preserves_history(self):
        model = ScriptedModel([ModelStep(output=[assistant_message("执行完成")])])
        self.assertEqual(
            self.execute_until_frontend_closes(model, "run.completed"), "completed"
        )

    def test_frontend_closing_after_approval_preserves_waiting(self):
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "write_text",
                            {
                                "path": "report.md",
                                "content": "待审批报告",
                                "expected_version": "missing",
                            },
                            call_id="write",
                        )
                    ]
                )
            ]
        )
        self.assertEqual(
            self.execute_until_frontend_closes(model, "run.waiting"), "waiting"
        )
        self.assertFalse((self.root / "report.md").exists())
