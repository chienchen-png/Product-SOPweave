from __future__ import annotations

import asyncio
import json
from contextlib import closing
from dataclasses import replace
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx2
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from agents.usage import Usage
from openai import AsyncOpenAI, APITimeoutError, BadRequestError
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from agent_runtime.definitions.agent_registry import AgentDefinition, AgentManifest
from agent_runtime.service import ResumeCommand, RunRequest, RunService


class _Registry:
    def __init__(self, definition: AgentDefinition) -> None:
        self.definition = definition

    def get(self, agent_id: str) -> AgentDefinition:
        if agent_id != self.definition.manifest.id:
            raise KeyError(agent_id)
        return self.definition


class RunServiceTests(unittest.TestCase):
    def test_default_and_legacy_model_references_are_pinned_before_run_creation(self):
        from storage.db import _ensure_model_schema
        from storage.model_store import ModelStore

        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            _ensure_model_schema(db)
            store = ModelStore(db)
            provider = store.create_provider({'name':'fixture','api_url':'https://fixture.invalid/v1','api_key':'test'})
            model = store.create_model(provider['id'], {'name':'legacy-name','type':'chat','is_default':True})
        service = RunService(db_path=self.db_path, agent_registry=self.registry)

        async def verify():
            for index, reference in enumerate(('', 'legacy-name')):
                run_id = f'pinned-{index}'
                stream = service.start(replace(self._request(run_id), model_id=reference))
                try:
                    event = await anext(stream)
                    self.assertEqual(event.payload['model_id'], model['id'])
                    with closing(sqlite3.connect(self.db_path)) as db:
                        self.assertEqual(db.execute('SELECT model_id FROM runs WHERE id=?',(run_id,)).fetchone()[0], model['id'])
                        saved = json.loads(db.execute('SELECT request_json FROM agent_run_requests WHERE run_id=?',(run_id,)).fetchone()[0])
                        self.assertEqual(saved['model_id'], model['id'])
                finally:
                    await stream.aclose()
        asyncio.run(verify())

    def test_chat_completions_reasoning_stream_is_persisted_and_replayed(self):
        from services.conversation_history import history_run
        from storage.run_store import RunStore

        async def verify():
            chunks = []
            for delta, finish in [
                ({"role": "assistant", "reasoning_content": "First "}, None),
                ({"reasoning_content": "verify totals."}, None),
                ({"content": "TOTAL_OK"}, None),
                ({}, "stop"),
            ]:
                chunks.append({"id": "chat-reasoning", "object": "chat.completion.chunk",
                    "created": 1, "model": "test-model",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
            wire = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, text=wire, headers={"content-type": "text/event-stream"})
            )) as transport:
                client = AsyncOpenAI(api_key="test", base_url="https://example.test/v1", http_client=transport)
                model = OpenAIChatCompletionsModel("test-model", openai_client=client)
                service = self._service(model)
                events = await _collect(service.start(self._request("reasoning-stream")))
            self.assertEqual(events[-1].type, "run.completed")
            reasoning = [event for event in events if event.type == "reasoning.delta"]
            self.assertEqual([event.payload["delta"] for event in reasoning], ["First ", "verify totals."])
            self.assertLess(reasoning[-1].seq, next(event.seq for event in events if event.type == "message.delta"))
            with closing(sqlite3.connect(self.db_path)) as db:
                db.row_factory = sqlite3.Row
                store = RunStore(db)
                history = history_run(db, store, store.get_run("reasoning-stream"))
                self.assertEqual(history["reasoning"], "First verify totals.")
                self.assertEqual(history["assistant_text"], "TOTAL_OK")
        asyncio.run(verify())

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "runtime.sqlite3"
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "storage" / "schema.sql"
        db.executescript(schema.read_text(encoding="utf-8"))
        db.close()
        manifest = AgentManifest(
            id="general-agent",
            name="General",
            version="1.0.0",
            description="General",
            enabled=True,
            toolsets=["workbench"],
            tools=["read_text", "write_text", "ask_user"],
            skills=[],
            approval_policy={"write_text": "always"},
            max_turns=10,
        )
        self.registry = _Registry(AgentDefinition(manifest, "Be concise.", self.root))

    def test_text_tool_usage_and_completion_events_are_ordered_and_persisted(self) -> None:
        (self.root / "input.txt").write_text("data", encoding="utf-8")
        model = ScriptedModel([
            ModelStep(
                output=[function_call("read_text", {"path": "input.txt"}, call_id="call-1")],
                usage=Usage(requests=1, input_tokens=3, output_tokens=2, total_tokens=5),
            ),
            ModelStep(
                output=[assistant_message("读取完成")],
                usage=Usage(requests=1, input_tokens=4, output_tokens=3, total_tokens=7),
            ),
        ])
        service = self._service(model)

        events = asyncio.run(_collect(service.start(self._request("run-text"))))

        types = [event.type for event in events]
        self.assertEqual(types[0], "run.created")
        self.assertIn("message.delta", types)
        self.assertLess(types.index("tool.started"), types.index("tool.completed"))
        self.assertLess(types.index("tool.completed"), types.index("run.completed"))
        self.assertEqual(events[-2].type, "usage.updated")
        self.assertEqual(events[-2].payload["total_tokens"], 12)
        self.assertEqual([event.seq for event in events], list(range(1, len(events) + 1)))
        db = sqlite3.connect(self.db_path)
        count = db.execute(
            "SELECT COUNT(*) FROM run_events WHERE run_id='run-text'"
        ).fetchone()[0]
        db.close()
        self.assertEqual(count, len(events))

    def test_approval_checkpoint_can_be_resumed_once(self) -> None:
        model = ScriptedModel([
            ModelStep(output=[function_call(
                "write_text", {"path": "output.txt", "content": "ok", "expected_version":"missing"}, call_id="call-write"
            )]),
            ModelStep(output=[assistant_message("已写入")]),
        ])
        service = self._service(model)

        from services.model_usage import UsageRecorder
        from agent_runtime.models.usage import UsageIdentity
        service.usage_recorder = UsageRecorder(self.db_path)
        service.usage_recorder.activate()
        service.usage_identity = UsageIdentity('p','供应商','deepseek','m','模型','api')
        waiting = asyncio.run(_collect(service.start(self._request("run-approval"))))
        interaction=next(e.payload for e in waiting if e.type=='approval.required')
        command=ResumeCommand(action='approve',tool_call_id='call-write',interaction_id=interaction['interaction_id'],version=interaction['version'])
        resumed = asyncio.run(_collect(service.resume(
            "run-approval", command
        )))

        self.assertEqual(waiting[-1].type, "run.waiting")
        self.assertIn("approval.required", [event.type for event in waiting])
        self.assertEqual(resumed[-1].type, "run.completed")
        self.assertEqual((self.root / "output.txt").read_text(encoding="utf-8"), "ok")
        duplicate=asyncio.run(_collect(service.resume('run-approval',command)))
        self.assertTrue(duplicate[0].payload['duplicate'])
        from contextlib import closing
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM token_usage').fetchone()[0],2)


    def test_ask_user_waits_and_continues_as_a_new_turn_in_the_same_session(self) -> None:
        model = ScriptedModel([
            ModelStep(output=[function_call(
                "ask_user",
                {'questions':[{'id':'continue','title':'是否继续？','kind':'confirm'}]},
                call_id="call-question",
            )]),
            ModelStep(output=[assistant_message("继续执行")]),
        ])
        service = self._service(model)

        waiting = asyncio.run(_collect(service.start(self._request("run-question"))))
        interaction=next(e.payload for e in waiting if e.type=='input.required')
        continued = asyncio.run(_collect(service.resume(
            "run-question", ResumeCommand(action='respond',interaction_id=interaction['interaction_id'],version=interaction['version'],answers=[{'question_id':'continue','confirmed':True}])
        )))

        self.assertIn("input.required", [event.type for event in waiting])
        self.assertEqual(waiting[-1].type, "run.waiting")
        self.assertEqual(continued[-1].type, "run.completed")
        self.assertTrue(any(
            event.type == "message.delta" and "继续" in event.payload.get("delta", "")
            for event in continued
        ))

    def test_provider_errors_are_normalized_without_secret_text(self) -> None:
        model = ScriptedModel([RuntimeError("provider failed with sk-secret-value")])
        service = self._service(model)

        events = asyncio.run(_collect(service.start(self._request("run-error"))))

        self.assertEqual(events[-1].type, "run.failed")
        rendered = str([event.to_dict() for event in events])
        self.assertNotIn("sk-secret-value", rendered)

    def test_context_overflow_has_an_actionable_terminal_error(self) -> None:
        body = {
            "error": {
                "message": "maximum context length exceeded",
                "code": "context_length_exceeded",
            }
        }
        response = httpx2.Response(
            400,
            request=httpx2.Request(
                "POST", "https://example.test/v1/chat/completions"
            ),
            json=body,
        )
        model = ScriptedModel([
            BadRequestError(
                "maximum context length exceeded",
                response=response,
                body=body,
            )
        ])
        service = self._service(model)

        events = asyncio.run(_collect(service.start(self._request("run-overflow"))))

        self.assertEqual(events[-1].type, "run.failed")
        self.assertEqual(
            events[-1].payload["error_code"], "context_length_exceeded"
        )
        self.assertIn("清理上下文", events[-1].payload["message"])

    def test_cancel_delegates_to_the_active_sdk_stream(self) -> None:
        service = self._service(ScriptedModel([]))

        class Active:
            cancelled = False

            def cancel(self, mode="immediate"):
                self.cancelled = mode == "immediate"

        active = Active()
        service._active["run-cancel"] = active

        service.cancel("run-cancel")

        self.assertTrue(active.cancelled)

    def test_thirty_turn_limit_terminates_with_a_failed_status(self):
        self.registry.definition = replace(self.registry.definition,
            manifest=self.registry.definition.manifest.model_copy(update={"max_turns": 30}))
        (self.root / "input.txt").write_text("data", encoding="utf-8")
        model = ScriptedModel([ModelStep(output=[function_call(
            "read_text", {"path": "input.txt"}, call_id=f"loop-{index}"
        )]) for index in range(31)])
        events = asyncio.run(_collect(self._service(model).start(self._request("turn-limit"))))
        self.assertEqual(events[-1].type, "run.failed")
        self.assertEqual(events[-1].payload["error_code"], "max_turns_exceeded")
        self.assertEqual(sum(event.type == "tool.completed" for event in events), 30)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("select status from runs where id='turn-limit'").fetchone()[0], "failed")

    def test_timeout_is_actionable_and_does_not_leak_provider_details(self):
        error = APITimeoutError(request=httpx2.Request("POST", "https://example.test/v1"))
        events = asyncio.run(_collect(self._service(ScriptedModel([error])).start(self._request("timeout"))))
        self.assertEqual(events[-1].type, "run.failed")
        self.assertEqual(events[-1].payload["error_code"], "timeout")

    def test_tool_duration_measures_execution_and_is_retained_in_history(self):
        from unittest.mock import patch
        import time
        from agent_runtime.tools import core
        from services.conversation_history import history_run
        from storage.run_store import RunStore
        original = core.read_text
        def measured_read(context, arguments):
            time.sleep(.02)
            return original(context, arguments)
        (self.root / "input.txt").write_text("value", encoding="utf-8")
        model = ScriptedModel([ModelStep(output=[function_call("read_text", {"path":"input.txt"}, call_id="timed")]), ModelStep(output=[assistant_message("done")])])
        with patch.object(core, "read_text", measured_read):
            service = self._service(model)
            events = asyncio.run(_collect(service.start(self._request("tool-timing"))))
        completed = next(e for e in events if e.type == "tool.completed")
        self.assertGreaterEqual(completed.payload.get("duration_ms", -1), 10)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            store = RunStore(db)
            card = history_run(db, store, store.get_run("tool-timing"))["tool_cards"][0]
            self.assertEqual(card["duration_ms"], completed.payload["duration_ms"])

    def test_reasoning_output_exhaustion_has_specific_user_guidance(self):
        from agents.exceptions import ModelBehaviorError
        error = ModelBehaviorError("Chat Completions stream terminated with finish_reason='length' but produced no assistant text, tool call, or refusal.")
        events = asyncio.run(_collect(self._service(ScriptedModel([error])).start(self._request("output-limit"))))
        self.assertEqual(events[-1].type, "run.failed")
        self.assertEqual(events[-1].payload["error_code"], "output_limit_exceeded")
        self.assertIn("回复上限", events[-1].payload["message"])

    def test_empty_model_response_is_not_reported_as_success(self):
        model = ScriptedModel([ModelStep(output=[assistant_message("  ")])])
        events = asyncio.run(_collect(self._service(model).start(self._request("empty"))))
        self.assertEqual(events[-1].type, "run.failed")
        self.assertEqual(events[-1].payload["error_code"], "invalid_model_response")

    def test_budget_filter_preserves_stored_history_and_reports_omissions(self):
        from agent_runtime.session import WorkbenchSession
        from services.conversation_history import history_run
        from storage.run_store import RunStore
        session = WorkbenchSession("conversation-1:general-agent:test-model", db_path=self.db_path)
        old = [{"role":"user","content":"old"*40000}, {"role":"assistant","content":"old"*40000}]
        asyncio.run(session.add_items(old))
        model = ScriptedModel([ModelStep(output=[assistant_message("current answer")])])
        events = asyncio.run(_collect(self._service(model).start(self._request("budget-filter"))))
        self.assertEqual(events[-1].type,"run.completed")
        self.assertNotIn("old"*100, json.dumps(model.calls[0].input))
        event = next(event for event in events if event.type=="context.updated")
        self.assertEqual(event.payload['omitted_items'],2)
        self.assertLessEqual(event.payload['current_tokens'],event.payload['effective_budget'])
        self.assertEqual(asyncio.run(session.get_items())[:2],old)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory=sqlite3.Row
            store=RunStore(db)
            self.assertEqual(history_run(db,store,store.get_run('budget-filter'))['context_omitted_items'],2)

    def test_oversized_current_task_is_rejected_before_calling_the_model(self):
        request=replace(self._request('budget-overflow'),prompt='x'*200001)
        model=ScriptedModel([])
        events=asyncio.run(_collect(self._service(model).start(request)))
        self.assertEqual(events[-1].type,'run.failed')
        self.assertEqual(events[-1].payload['error_code'],'context_length_exceeded')
        self.assertEqual(len(model.calls),0)

    def _service(self, model: ScriptedModel) -> RunService:
        return RunService(
            db_path=self.db_path,
            agent_registry=self.registry,
            model_provider=lambda _request: model,
        )

    def _request(self, run_id: str) -> RunRequest:
        return RunRequest(
            run_id=run_id,
            prompt="处理文件",
            project_id="project-1",
            conversation_id="conversation-1",
            agent_id="general-agent",
            model_id="test-model",
            work_dir=self.root,
            work_mode="manual",
        )


async def _collect(stream):
    return [event async for event in stream]


if __name__ == "__main__":
    unittest.main()
