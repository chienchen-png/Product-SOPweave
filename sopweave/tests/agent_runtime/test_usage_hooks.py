import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import closing

from agent_runtime.models.factory import ModelRuntimeFactory, resolve_model_config
from agent_runtime.models.usage import UsageIdentity
from agent_runtime.usage_hooks import UsageRunHooks
from services.model_usage import UsageRecorder
from tests.agent_runtime.fake_provider import FakeProviderServer


class UsageCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "app.db"
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(
                (Path(__file__).parents[2] / "storage/schema.sql").read_text(
                    encoding="utf-8"
                )
            )
            from storage.token_store import ensure_usage_schema

            ensure_usage_schema(db)
            db.commit()
        self.recorder = UsageRecorder(self.path)
        self.recorder.activate()
        self.identity = UsageIdentity("p", "供应商", "deepseek", "m", "模型", "api")

    def rows(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [
                dict(row) for row in db.execute("SELECT * FROM token_usage ORDER BY id")
            ]

    def test_hooks_record_single_responses_and_only_pending_round_is_interrupted(self):
        async def run():
            hooks = UsageRunHooks(self.recorder, self.identity, run_id="r")
            await hooks.on_llm_start(None, None, None, [])
            await hooks.on_llm_end(
                None,
                None,
                SimpleNamespace(raw_usage={"total_tokens": 9}, request_id="remote"),
            )
            await hooks.on_llm_start(None, None, None, [])
            await hooks.close("cancelled")
            await hooks.close("cancelled")

        asyncio.run(run())
        rows = self.rows()
        self.assertEqual(
            [(r["total_tokens"], r["request_state"]) for r in rows],
            [(9, "completed"), (None, "cancelled")],
        )

    def test_direct_complete_and_stream_use_real_usage_for_each_vendor(self):
        async def run(server, vendor):
            cfg = resolve_model_config(
                dict(
                    id=vendor,
                    name="测试模型",
                    api_model="contract-model",
                    api_url=server.base_url,
                    api_key="test",
                    provider_kind=vendor,
                    provider_id="p",
                    provider_name="供应商",
                )
            )
            client = ModelRuntimeFactory(
                usage_recorder=self.recorder
            ).build_direct_client(cfg)
            try:
                await client.complete([{"role": "user", "content": "hello"}])
                async for _ in client.stream([{"role": "user", "content": "hello"}]):
                    pass
            finally:
                await client.aclose()

        with FakeProviderServer() as server:
            for vendor in ["deepseek", "zhipu", "siliconflow"]:
                asyncio.run(run(server, vendor))
        rows = self.rows()
        self.assertEqual(len(rows), 6)
        self.assertTrue(
            all(r["usage_state"] == "reported" and r["total_tokens"] > 0 for r in rows)
        )

    def test_closed_stream_is_cancelled_and_network_failure_is_preserved(self):
        async def run(server):
            cfg = resolve_model_config(
                dict(
                    id="m",
                    name="test",
                    api_model="error-401",
                    api_url=server.base_url,
                    api_key="test",
                )
            )
            client = ModelRuntimeFactory(
                usage_recorder=self.recorder
            ).build_direct_client(cfg)
            try:
                with self.assertRaises(Exception):
                    await client.complete([{"role": "user", "content": "hello"}])
            finally:
                await client.aclose()

        with FakeProviderServer() as server:
            asyncio.run(run(server))
        self.assertEqual(
            (self.rows()[0]["request_state"], self.rows()[0]["usage_state"]),
            ("failed", "missing"),
        )

    def test_sdk_model_response_raw_usage_reaches_public_hooks(self):
        from agents import Agent, Runner

        async def run(server):
            cfg = resolve_model_config(
                dict(
                    id="m",
                    name="test",
                    api_model="contract-model",
                    api_url=server.base_url,
                    api_key="test",
                )
            )
            factory = ModelRuntimeFactory(usage_recorder=self.recorder)
            model = factory.build_agents_model(cfg)
            hooks = UsageRunHooks(self.recorder, UsageIdentity.from_config(cfg))
            try:
                result = Runner.run_streamed(
                    Agent(
                        name="test",
                        instructions="Be brief",
                        model=model,
                        model_settings=factory.build_model_settings(cfg),
                    ),
                    "hello",
                    hooks=hooks,
                )
                async for _ in result.stream_events():
                    pass
            finally:
                await hooks.close()
                await model._client.close()

        with FakeProviderServer() as server:
            asyncio.run(run(server))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["usage_state"], "reported")

    def test_terminal_content_usage_repeated_chunks_and_early_close(self):
        from agent_runtime.models.factory import DirectModelClient

        class Transport:
            async def create(self, **kwargs):
                async def chunks():
                    yield SimpleNamespace(choices=["content"], usage=None)
                    yield SimpleNamespace(
                        choices=["terminal content"], usage={"total_tokens": 7}
                    )
                    yield SimpleNamespace(choices=[], usage={"total_tokens": 7})

                return chunks()

        async def run():
            transport = SimpleNamespace(chat=SimpleNamespace(completions=Transport()))
            client = DirectModelClient(
                transport,
                "m",
                usage_recorder=self.recorder,
                usage_identity=self.identity,
            )
            chunks = [chunk async for chunk in client.stream([])]
            self.assertEqual(len(chunks), 3)
            stream = client.stream([])
            await anext(stream)
            await stream.aclose()

        asyncio.run(run())
        rows = self.rows()
        self.assertEqual(
            [(r["total_tokens"], r["request_state"]) for r in rows],
            [(7, "completed"), (None, "cancelled")],
        )

    def test_accounting_failure_does_not_repeat_or_fail_model_call(self):
        class Transport:
            calls = 0

            async def create(self, **kwargs):
                self.calls += 1
                return SimpleNamespace(usage={"total_tokens": 0})

        def unavailable():
            raise sqlite3.OperationalError("unavailable")

        from agent_runtime.models.factory import DirectModelClient

        async def run():
            transport = Transport()
            recorder = UsageRecorder(self.path, connection_factory=unavailable)
            client = DirectModelClient(
                SimpleNamespace(chat=SimpleNamespace(completions=transport)),
                "m",
                usage_recorder=recorder,
                usage_identity=self.identity,
            )
            result = await client.complete([])
            self.assertIsNotNone(result)
            self.assertEqual(transport.calls, 1)
            self.assertTrue(recorder.warning)

        asyncio.run(run())
