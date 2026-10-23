from __future__ import annotations

import asyncio
import json
import sqlite3
import unittest
from pathlib import Path

from agent_runtime.models.contract import ProviderContractRunner, contract_runner_for
from agent_runtime.models.factory import ModelRuntimeFactory, resolve_model_config
from agent_runtime.models.types import RuntimePurpose
from storage.db import _ensure_model_schema
from storage.model_store import ModelStore
from tests.agent_runtime.fake_provider import FakeProviderServer


class ProviderContractTests(unittest.TestCase):
    def test_low_output_provider_probe_disables_reasoning_over_http(self):
        async def exercise(url):
            config=resolve_model_config(_record('zhipu','glm-low-output',url))
            client=ModelRuntimeFactory(usage_recorder=None).build_direct_client(config,RuntimePurpose.PROVIDER_CHECK)
            try:
                return await ProviderContractRunner(client).run()
            finally:
                await client.aclose()
        with FakeProviderServer() as server:
            result=asyncio.run(exercise(server.base_url))
        self.assertTrue(result.passed,result.to_dict())

    def test_five_part_contract_passes_against_an_openai_compatible_server(self) -> None:
        with FakeProviderServer() as server:
            result = asyncio.run(_run_contract(server.base_url, "contract-model"))

        self.assertTrue(result.passed, result.to_dict())
        self.assertEqual(
            {name: check.status for name, check in result.checks.items()},
            {
                "text": "passed",
                "stream": "passed",
                "tools": "passed",
                "tool_resume": "passed",
                "usage": "passed",
            },
        )
        self.assertEqual(result.checks["tools"].detail["arguments"], {"value": 2})

    def test_auth_rate_limit_timeout_and_disconnect_are_classified(self) -> None:
        expected = {
            "error-401": "authentication_error",
            "error-404": "model_not_found",
            "error-429": "rate_limit_error",
            "timeout": "timeout",
            "disconnect": "connection_error",
        }
        with FakeProviderServer() as server:
            for model, error_code in expected.items():
                with self.subTest(model=model):
                    result = asyncio.run(_run_contract(server.base_url, model, timeout=0.05))
                    self.assertFalse(result.passed)
                    self.assertEqual(result.checks["text"].error_code, error_code)
                    self.assertNotIn("secret-contract-key", json.dumps(result.to_dict()))

    def test_contract_results_are_persisted_per_model_and_check(self) -> None:
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "storage" / "schema.sql"
        db.executescript(schema.read_text(encoding="utf-8"))
        _ensure_model_schema(db)
        store = ModelStore(db)
        with FakeProviderServer() as server:
            result = asyncio.run(_run_contract(server.base_url, "contract-model"))

        store.save_capability_checks("model-1", result.to_dict()["checks"])
        saved = store.list_capability_checks("model-1")

        self.assertEqual(len(saved), 5)
        self.assertTrue(all(item["status"] == "passed" for item in saved))
        self.assertEqual({item["check_name"] for item in saved}, set(result.checks))

    def test_rate_limit_retries_once_and_honors_retry_after_variants(self) -> None:
        expected_delays = {
            "rate-limit-once-valid": 0.01,
            "rate-limit-once-missing": 0.02,
            "rate-limit-once-malformed": 0.02,
        }
        with FakeProviderServer() as server:
            for model, expected_delay in expected_delays.items():
                with self.subTest(model=model):
                    delays: list[float] = []
                    result = asyncio.run(
                        _run_contract(
                            server.base_url,
                            model,
                            max_rate_limit_retries=1,
                            default_retry_seconds=0.02,
                            sleep=_record_sleep(delays),
                        )
                    )
                    self.assertTrue(result.passed, result.to_dict())
                    self.assertEqual(delays, [expected_delay])
                    self.assertEqual(server.request_count(model), 6)

    def test_rate_limit_never_retries_an_operation_more_than_once(self) -> None:
        with FakeProviderServer() as server:
            result = asyncio.run(
                _run_contract(
                    server.base_url,
                    "error-429",
                    max_rate_limit_retries=1,
                    default_retry_seconds=0,
                    sleep=_record_sleep([]),
                )
            )

            self.assertFalse(result.passed)
            self.assertEqual(server.request_count("error-429"), 10)
            serialized = json.dumps(result.to_dict())
            self.assertIn("rate_limit_error", serialized)
            self.assertNotIn("UPSTREAM-SECRET-BODY", serialized)

    def test_glm_contract_runner_enables_pacing_and_one_retry(self) -> None:
        config = resolve_model_config(_record("zhipu", "glm-4.7-flash", "https://open.bigmodel.cn/api/paas/v4"))
        client = ModelRuntimeFactory(usage_recorder=None).build_direct_client(config)
        self.addCleanup(lambda: asyncio.run(client.aclose()))

        runner = contract_runner_for(config, client)

        self.assertGreater(runner.interval_seconds, 0)
        self.assertEqual(runner.max_rate_limit_retries, 1)

    def test_glm_tool_probe_sends_auto_over_http(self) -> None:
        async def exercise(base_url):
            config = resolve_model_config(_record("zhipu", "contract-model", base_url))
            client = ModelRuntimeFactory(usage_recorder=None).build_direct_client(config)
            try:
                return await ProviderContractRunner(client)._check_tools()
            finally:
                await client.aclose()

        with FakeProviderServer() as server:
            result = asyncio.run(exercise(server.base_url))
            self.assertEqual(result["arguments"], {"value": 2})
            self.assertEqual(server.last_request("contract-model")["tool_choice"], "auto")


async def _run_contract(
    base_url: str,
    model: str,
    timeout: float = 10.0,
    *,
    max_rate_limit_retries: int = 0,
    default_retry_seconds: float = 1,
    sleep=asyncio.sleep,
):
    config = resolve_model_config(_record("openai_compatible", model, base_url, timeout))
    client = ModelRuntimeFactory(usage_recorder=None).build_direct_client(config)
    try:
        return await ProviderContractRunner(
            client,
            max_rate_limit_retries=max_rate_limit_retries,
            default_retry_seconds=default_retry_seconds,
            sleep=sleep,
        ).run()
    finally:
        await client.aclose()


def _record(
    provider_kind: str,
    model: str,
    base_url: str,
    timeout: float = 10.0,
) -> dict:
    return {
        "id": "contract-id",
        "name": "Contract",
        "api_model": model,
        "api_url": base_url,
        "api_key": "secret-contract-key",
        "provider_kind": provider_kind,
        "api_mode": "chat_completions",
        "capabilities_json": {"chat": True, "stream": True, "tools": True},
        "request_options_json": {},
        "provider_options_json": {"timeout": timeout, "max_retries": 0},
    }


def _record_sleep(delays: list[float]):
    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    return sleep


if __name__ == "__main__":
    unittest.main()
