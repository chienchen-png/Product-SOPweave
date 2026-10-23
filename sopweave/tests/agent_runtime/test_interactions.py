from __future__ import annotations

import asyncio
import unittest
from agents.testing import ScriptedModel, ModelStep, function_call, assistant_message
from agent_runtime.service import ResumeCommand
from tests.agent_runtime import test_run_service as fixtures

_collect = fixtures._collect


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RunServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_approved_process_streams_output_and_cancellation_stops_late_write(self):
        self.fixture.registry.definition.manifest.tools.append("run_process")
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "run_process",
                            {
                                "program": "python",
                                "code": "import time,pathlib;print('ready',flush=True);time.sleep(1);pathlib.Path('late.txt').write_text('bad')",
                                "reason": "verify cancellation",
                                "timeout": 10,
                            },
                            call_id="process",
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("done")]),
            ]
        )
        service = self.fixture._service(model)

        async def scenario():
            events = await _collect(service.start(self.fixture._request("process")))
            approval = next(e.payload for e in events if e.type == "approval.required")
            command = ResumeCommand(
                action="approve",
                interaction_id=approval["interaction_id"],
                version=approval["version"],
            )
            output = []
            async for event in service.resume("process", command):
                output.append(event)
                if event.type == "tool.output":
                    service.cancel("process")
            self.assertTrue(any(e.type == "tool.output" for e in output))
            self.assertEqual(output[-1].type, "run.cancelled")
            await asyncio.sleep(1.1)
            self.assertFalse((self.fixture.root / "late.txt").exists())

        asyncio.run(scenario())

    def test_question_restart_validation_and_idempotent_answer(self):
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "ask_user",
                            {
                                "questions": [
                                    {
                                        "id": "q",
                                        "title": "选择",
                                        "kind": "multiple",
                                        "options": [
                                            {"id": "a", "label": "A"},
                                            {"id": "b", "label": "B"},
                                        ],
                                        "allow_custom": False,
                                    }
                                ]
                            },
                            call_id="ask",
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("done")]),
            ]
        )
        service = self.fixture._service(model)
        waiting = asyncio.run(
            _collect(service.start(self.fixture._request("question")))
        )
        item = next(e.payload for e in waiting if e.type == "input.required")
        service = self.fixture._service(
            model
        )  # reconstruct service, keep durable state
        base = dict(
            action="respond",
            interaction_id=item["interaction_id"],
            version=item["version"],
        )
        with self.assertRaises(ValueError):
            asyncio.run(
                _collect(
                    service.resume(
                        "question",
                        ResumeCommand(
                            **base,
                            answers=[{"question_id": "q", "selected": ["unknown"]}],
                        ),
                    )
                )
            )
        command = ResumeCommand(
            **base, answers=[{"question_id": "q", "selected": ["a", "b"]}]
        )
        completed = asyncio.run(_collect(service.resume("question", command)))
        self.assertEqual(completed[-1].type, "run.completed")
        duplicate = asyncio.run(_collect(service.resume("question", command)))
        self.assertTrue(
            any(
                e.type == "interaction.resolved" and e.payload.get("duplicate")
                for e in duplicate
            )
        )
        self.assertEqual(service.interactions.list("question", "pending"), [])

    def test_multiple_approvals_show_arguments_and_execute_after_all_decisions(self):
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "write_text",
                            {
                                "path": "a.txt",
                                "content": "a",
                                "expected_version": "missing",
                            },
                            call_id="a",
                        ),
                        function_call(
                            "write_text",
                            {
                                "path": "b.txt",
                                "content": "b",
                                "expected_version": "missing",
                            },
                            call_id="b",
                        ),
                    ]
                ),
                ModelStep(output=[assistant_message("done")]),
            ]
        )
        service = self.fixture._service(model)
        events = asyncio.run(
            _collect(service.start(self.fixture._request("approvals")))
        )
        pending = [e.payload for e in events if e.type == "approval.required"]
        self.assertEqual(len(pending), 2)
        self.assertEqual({p["arguments"]["path"] for p in pending}, {"a.txt", "b.txt"})
        item = pending[0]
        with self.assertRaises(ValueError):
            asyncio.run(
                _collect(
                    service.resume(
                        "approvals",
                        ResumeCommand(
                            action="respond",
                            interaction_id=item["interaction_id"],
                            version=item["version"],
                        ),
                    )
                )
            )
        first = asyncio.run(
            _collect(
                service.resume(
                    "approvals",
                    ResumeCommand(
                        action="approve",
                        interaction_id=item["interaction_id"],
                        version=item["version"],
                        note="approved a",
                    ),
                )
            )
        )
        self.assertEqual(first[-1].type, "run.waiting")
        self.assertFalse((self.fixture.root / "a.txt").exists())
        item = pending[1]
        final = asyncio.run(
            _collect(
                service.resume(
                    "approvals",
                    ResumeCommand(
                        action="reject",
                        interaction_id=item["interaction_id"],
                        version=item["version"],
                        note="reject b",
                    ),
                )
            )
        )
        self.assertEqual(final[-1].type, "run.completed")
        self.assertTrue((self.fixture.root / "a.txt").exists())
        self.assertFalse((self.fixture.root / "b.txt").exists())
        self.assertEqual(
            service.interactions.list("approvals")[0]["response"]["note"], "approved a"
        )

    def test_changed_definition_cannot_execute_old_approval(self):
        from dataclasses import replace

        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "write_text",
                            {
                                "path": "x.txt",
                                "content": "x",
                                "expected_version": "missing",
                            },
                            call_id="write",
                        )
                    ]
                )
            ]
        )
        service = self.fixture._service(model)
        events = asyncio.run(_collect(service.start(self.fixture._request("changed"))))
        item = next(e.payload for e in events if e.type == "approval.required")
        self.fixture.registry.definition = replace(
            self.fixture.registry.definition, instructions="changed implementation"
        )
        with self.assertRaises(ValueError):
            asyncio.run(
                _collect(
                    service.resume(
                        "changed",
                        ResumeCommand(
                            action="approve",
                            interaction_id=item["interaction_id"],
                            version=1,
                        ),
                    )
                )
            )
        self.assertFalse((self.fixture.root / "x.txt").exists())
        self.assertEqual(len(service.interactions.list("changed", "pending")), 1)

    def test_cancel_and_skip_have_explicit_states(self):
        for disposition, required in [("skip", False), ("cancel", True)]:
            model = ScriptedModel(
                [
                    ModelStep(
                        output=[
                            function_call(
                                "ask_user",
                                {
                                    "questions": [
                                        {
                                            "id": "q",
                                            "title": "optional",
                                            "required": required,
                                        }
                                    ]
                                },
                                call_id="q",
                            )
                        ]
                    ),
                    ModelStep(output=[assistant_message("done")]),
                ]
            )
            service = self.fixture._service(model)
            events = asyncio.run(
                _collect(service.start(self.fixture._request(disposition)))
            )
            item = next(e.payload for e in events if e.type == "input.required")
            final = asyncio.run(
                _collect(
                    service.resume(
                        disposition,
                        ResumeCommand(
                            action="respond",
                            interaction_id=item["interaction_id"],
                            version=1,
                            disposition=disposition,
                        ),
                    )
                )
            )
            self.assertEqual(
                final[-1].type,
                "run.cancelled" if disposition == "cancel" else "run.completed",
            )

    def test_concurrent_submission_is_consumed_once(self):
        from concurrent.futures import ThreadPoolExecutor

        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "ask_user",
                            {"questions": [{"id": "q", "title": "input"}]},
                            call_id="q",
                        )
                    ]
                )
            ]
        )
        service = self.fixture._service(model)
        events = asyncio.run(_collect(service.start(self.fixture._request("race"))))
        item = next(e.payload for e in events if e.type == "input.required")
        command = ResumeCommand(
            action="respond",
            interaction_id=item["interaction_id"],
            version=1,
            answers=[{"question_id": "q", "text": "yes"}],
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(
                pool.map(
                    lambda _: service.interactions.resolve("race", command), range(2)
                )
            )
        self.assertEqual(sum(not duplicate for _, duplicate, _ in result), 1)


if __name__ == "__main__":
    unittest.main()
