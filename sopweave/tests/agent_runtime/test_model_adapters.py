from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from agents import ModelSettings
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from agent_runtime.models.adapters import adapter_for
from agent_runtime.models.factory import ModelRuntimeFactory, resolve_model_config
from agent_runtime.models.types import ProviderKind, RuntimePurpose


class ProviderAdapterTests(unittest.TestCase):
    def test_provider_checks_disable_thinking_without_changing_project_chat(self):
        factory=ModelRuntimeFactory(usage_recorder=None)
        for kind,key in [('deepseek','thinking'),('zhipu','thinking'),('siliconflow','enable_thinking')]:
            config=resolve_model_config(_record(kind)|{'request_options_json':{'thinking_enabled':True}})
            probe=factory.build_model_settings(config,RuntimePurpose.PROVIDER_CHECK)
            chat=factory.build_model_settings(config,RuntimePurpose.AGENT)
            if key=='thinking':
                self.assertEqual(probe.extra_body[key],{'type':'disabled'})
                self.assertEqual(chat.extra_body[key],{'type':'enabled'})
            else:
                self.assertFalse(probe.extra_body[key])
                self.assertTrue(chat.extra_body[key])

    def test_siliconflow_explicit_output_budget_is_shared_and_user_override_wins(self):
        factory = ModelRuntimeFactory(usage_recorder=None)
        for configured, expected in [({}, 4096), ({"max_tokens": 2048}, 2048)]:
            record = _record("siliconflow") | {"request_options_json": configured}
            config = resolve_model_config(record)
            self.assertEqual(factory.build_model_settings(config).max_tokens, expected)
            direct = factory.build_direct_client(config)
            self.assertEqual(direct.request_args["max_tokens"], expected)
            asyncio.run(direct.aclose())

    def test_vendor_base_urls_are_normalized_without_endpoint_duplication(self) -> None:
        cases = [
            ("deepseek", "https://api.deepseek.com", "https://api.deepseek.com/v1/"),
            ("deepseek", "https://api.deepseek.com/v1/", "https://api.deepseek.com/v1/"),
            ("deepseek", "https://api.deepseek.com/v1/chat/completions", "https://api.deepseek.com/v1/"),
            ("zhipu", "https://open.bigmodel.cn/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4/"),
            ("zhipu", "https://open.bigmodel.cn/api/paas/v4/", "https://open.bigmodel.cn/api/paas/v4/"),
            ("dashscope", "https://dashscope.aliyuncs.com", "https://dashscope.aliyuncs.com/compatible-mode/v1/"),
            ("dashscope", "https://dashscope.aliyuncs.com/compatible-mode/v1/", "https://dashscope.aliyuncs.com/compatible-mode/v1/"),
            ("siliconflow", "https://api.siliconflow.cn", "https://api.siliconflow.cn/v1/"),
            ("siliconflow", "https://api.siliconflow.cn/v1/chat/completions", "https://api.siliconflow.cn/v1/"),
            ("openai_compatible", "https://proxy.example/v1", "https://proxy.example/v1/"),
            ("openai_compatible", "https://proxy.example/v1/chat/completions", "https://proxy.example/v1/"),
        ]

        for kind, source, expected in cases:
            with self.subTest(kind=kind, source=source):
                self.assertEqual(adapter_for(kind).normalize_base_url(source), expected)

    def test_integrated_apps_disable_thinking_with_vendor_specific_options(self) -> None:
        expected_extra = {
            "deepseek": {"thinking": {"type": "disabled"}},
            "zhipu": {"thinking": {"type": "disabled"}},
            "dashscope": {"enable_thinking": False},
            "siliconflow": {"enable_thinking": False},
            "openai_compatible": {},
        }

        for kind, expected in expected_extra.items():
            record = _record(kind)
            config = resolve_model_config(record)
            settings = ModelRuntimeFactory(usage_recorder=None).build_model_settings(
                config, RuntimePurpose.INTEGRATED_APP
            )
            self.assertIsInstance(settings, ModelSettings)
            self.assertEqual(settings.extra_body or {}, expected)
            self.assertTrue(settings.include_usage)

    def test_factory_binds_a_private_client_and_never_repr_exposes_the_key(self) -> None:
        config = resolve_model_config(_record("deepseek"))
        factory = ModelRuntimeFactory(usage_recorder=None)

        model = factory.build_agents_model(config)
        direct = factory.build_direct_client(config)

        self.assertIsInstance(model, OpenAIChatCompletionsModel)
        self.assertEqual(model.model, "vendor-model")
        self.assertEqual(str(model._client.base_url), "https://api.deepseek.com/v1/")
        self.assertNotIn("top-secret-key", repr(config))
        self.assertNotIn("top-secret-key", repr(model))
        self.assertNotIn("top-secret-key", repr(direct))

    def test_direct_client_only_sends_stream_options_for_streaming_requests(self) -> None:
        config = resolve_model_config(_record("openai_compatible"))
        direct = ModelRuntimeFactory(usage_recorder=None).build_direct_client(config)
        create = AsyncMock(side_effect=[MagicMock(), _empty_stream()])
        direct.client.chat.completions.create = create

        async def exercise() -> None:
            await direct.complete([{"role": "user", "content": "ping"}])
            async for _ in direct.stream([{"role": "user", "content": "ping"}]):
                pass

        asyncio.run(exercise())

        self.assertNotIn("stream_options", create.await_args_list[0].kwargs)
        self.assertEqual(
            create.await_args_list[1].kwargs["stream_options"],
            {"include_usage": True},
        )

    def test_resolved_config_rejects_non_chat_completions_and_bad_records(self) -> None:
        responses = _record("deepseek") | {"api_mode": "responses"}
        missing_url = _record("deepseek") | {"api_url": ""}

        with self.assertRaisesRegex(ValueError, "Chat Completions") as mode_error:
            resolve_model_config(responses)
        with self.assertRaisesRegex(ValueError, "API URL") as url_error:
            resolve_model_config(missing_url)

        self.assertNotIn("top-secret-key", str(mode_error.exception))
        self.assertNotIn("top-secret-key", str(url_error.exception))

    def test_custom_provider_supports_only_controlled_thinking_styles(self) -> None:
        expected = {
            "none": {},
            "thinking_object": {"thinking": {"type": "disabled"}},
            "enable_thinking": {"enable_thinking": False},
        }

        for style, extra_body in expected.items():
            with self.subTest(style=style):
                record = _record("openai_compatible")
                record["provider_options_json"] = {"thinking_style": style}
                config = resolve_model_config(record)
                settings = ModelRuntimeFactory(usage_recorder=None).build_model_settings(
                    config, RuntimePurpose.INTEGRATED_APP
                )
                self.assertEqual(settings.extra_body or {}, extra_body)

    def test_custom_provider_rejects_unknown_thinking_style(self) -> None:
        record = _record("openai_compatible")
        record["provider_options_json"] = {"thinking_style": "raw_json"}

        with self.assertRaisesRegex(ValueError, "思考参数"):
            ModelRuntimeFactory(usage_recorder=None).build_model_settings(
                resolve_model_config(record), RuntimePurpose.INTEGRATED_APP
            )

    def test_base_url_rejects_queries_fragments_credentials_and_bad_schemes(self) -> None:
        invalid_urls = [
            "ftp://proxy.example/v1",
            "https://proxy.example/v1?token=secret",
            "https://proxy.example/v1#fragment",
            "https://user:password@proxy.example/v1",
        ]

        for source in invalid_urls:
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, r"HTTP\(S\)"):
                    adapter_for("openai_compatible").normalize_base_url(source)


def _record(provider_kind: str) -> dict:
    base_urls = {
        "deepseek": "https://api.deepseek.com",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "dashscope": "https://dashscope.aliyuncs.com",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "openai_compatible": "https://proxy.example/v1",
    }
    return {
        "id": "model-id",
        "name": "Display name",
        "api_model": "vendor-model",
        "api_url": base_urls[provider_kind],
        "api_key": "top-secret-key",
        "provider_kind": ProviderKind(provider_kind).value,
        "api_mode": "chat_completions",
        "capabilities_json": {"chat": True, "stream": True, "tools": True},
        "request_options_json": {"temperature": 0.2, "max_tokens": 2048},
        "provider_options_json": {"timeout": 45},
    }


async def _empty_stream():
    if False:
        yield None


if __name__ == "__main__":
    unittest.main()
