from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping, Sequence
import asyncio
import time

from agents import ModelSettings
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from pydantic import SecretStr

from .adapters import adapter_for
from .types import ApiMode, ProviderKind, ResolvedModelConfig, RuntimePurpose
from .usage import UsageIdentity

_MODEL_SETTING_FIELDS = {
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "tool_choice",
    "parallel_tool_calls",
    "max_tokens",
    "timeout",
}
_CONTROL_FIELDS = {"extra_body", "thinking_enabled"}


def _mapping(value: object, field_name: str) -> dict:
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError(f"{field_name} 必须是对象")


def resolve_model_config(record: Mapping[str, Any]) -> ResolvedModelConfig:
    try:
        provider_kind = ProviderKind(
            str(record.get("provider_kind") or ProviderKind.OPENAI_COMPATIBLE.value)
        )
        api_mode = ApiMode(
            str(record.get("api_mode") or ApiMode.CHAT_COMPLETIONS.value)
        )
    except ValueError as exc:
        raise ValueError("模型供应商或 API 模式无效") from exc
    if api_mode is not ApiMode.CHAT_COMPLETIONS:
        raise ValueError("当前运行时只支持 Chat Completions 协议")

    model_id = str(record.get("id") or "").strip()
    api_model = str(record.get("api_model") or record.get("name") or "").strip()
    api_url = str(record.get("api_url") or "").strip()
    if not model_id:
        raise ValueError("模型记录缺少稳定 ID")
    if not api_model:
        raise ValueError("模型记录缺少 API 模型标识")
    if not api_url:
        raise ValueError("模型记录缺少 API URL")

    adapter = adapter_for(provider_kind)
    return ResolvedModelConfig(
        model_id=model_id,
        display_name=str(record.get("name") or api_model),
        api_model=api_model,
        base_url=adapter.normalize_base_url(api_url),
        api_key=SecretStr(str(record.get("api_key") or "")),
        provider_id=str(record.get("provider_id") or ""),
        provider_name=str(record.get("provider_name") or "自定义供应商"),
        provider_kind=provider_kind,
        api_mode=api_mode,
        capabilities={
            str(key): bool(value)
            for key, value in _mapping(
                record.get("capabilities_json"), "capabilities_json"
            ).items()
        },
        request_options=_mapping(
            record.get("request_options_json"), "request_options_json"
        ),
        provider_options=_mapping(
            record.get("provider_options_json"), "provider_options_json"
        ),
    )


@dataclass
class DirectModelClient:
    client: AsyncOpenAI = field(repr=False)
    model: str
    request_args: dict[str, Any] = field(default_factory=dict)
    include_usage: bool = True
    provider_kind: ProviderKind = ProviderKind.OPENAI_COMPATIBLE
    usage_recorder: Any = field(default=None, repr=False)
    usage_identity: UsageIdentity | None = None

    async def _begin_usage(self):
        if self.usage_recorder and self.usage_identity:
            return await asyncio.to_thread(
                self.usage_recorder.begin, self.usage_identity
            )
        return None

    async def _finish_usage(self, call_id, state, raw, started, request_id=None):
        if call_id:
            await asyncio.to_thread(
                self.usage_recorder.finish,
                call_id,
                state=state,
                raw_usage=raw,
                provider_request_id=request_id,
                duration_ms=max(0, round((time.monotonic() - started) * 1000)),
            )

    def named_tool_choice(self, name: str) -> str | dict[str, Any]:
        choice = adapter_for(self.provider_kind).named_tool_choice(name)
        if choice == "auto":
            return choice
        return {"type": "function", "function": {"name": choice}}

    async def complete(
        self, messages: Sequence[Mapping[str, Any]], **overrides: Any
    ) -> Any:
        arguments = {**self.request_args, **overrides}
        call_id = await self._begin_usage()
        started = time.monotonic()
        response = None
        state = "failed"
        try:
            response = await self.client.chat.completions.create(
                model=self.model, messages=list(messages), stream=False, **arguments
            )
            state = "completed"
            return response
        except asyncio.CancelledError:
            state = "cancelled"
            raise
        finally:
            await self._finish_usage(
                call_id,
                state,
                getattr(response, "usage", None),
                started,
                getattr(response, "_request_id", None),
            )

    async def stream(
        self, messages: Sequence[Mapping[str, Any]], **overrides: Any
    ) -> AsyncIterator[Any]:
        arguments = {**self.request_args, **overrides}
        if self.include_usage:
            arguments.setdefault("stream_options", {"include_usage": True})
        call_id = await self._begin_usage()
        started = time.monotonic()
        response = None
        raw = None
        state = "failed"
        try:
            response = await self.client.chat.completions.create(
                model=self.model, messages=list(messages), stream=True, **arguments
            )
            async for chunk in response:
                if getattr(chunk, "usage", None) is not None:
                    raw = chunk.usage
                yield chunk
            state = "completed"
        except (asyncio.CancelledError, GeneratorExit):
            state = "cancelled"
            raise
        finally:
            try:
                if response is not None:
                    close = getattr(response, "close", None) or getattr(
                        response, "aclose", None
                    )
                    if close:
                        await close()
            finally:
                await self._finish_usage(call_id, state, raw, started)

    async def aclose(self) -> None:
        await self.client.close()

    def __repr__(self) -> str:
        return f"DirectModelClient(model={self.model!r})"


class ModelRuntimeFactory:
    def __init__(self, *, usage_recorder):
        self.usage_recorder = usage_recorder

    def _client(self, config: ResolvedModelConfig) -> AsyncOpenAI:
        timeout = config.provider_options.get("timeout")
        client_args: dict[str, Any] = {
            "api_key": config.api_key.get_secret_value() or "not-configured",
            "base_url": config.base_url,
        }
        if isinstance(timeout, (int, float)) and timeout > 0:
            client_args["timeout"] = float(timeout)
        max_retries = config.provider_options.get("max_retries")
        if isinstance(max_retries, int) and max_retries >= 0:
            client_args["max_retries"] = max_retries
        return AsyncOpenAI(**client_args)

    def build_agents_model(
        self, config: ResolvedModelConfig
    ) -> OpenAIChatCompletionsModel:
        return OpenAIChatCompletionsModel(
            model=config.api_model,
            openai_client=self._client(config),
        )

    def build_model_settings(
        self,
        config: ResolvedModelConfig,
        purpose: RuntimePurpose = RuntimePurpose.AGENT,
    ) -> ModelSettings:
        adapter = adapter_for(config.provider_kind)
        merged = {**config.provider_options, **config.request_options}
        settings = {
            key: value for key, value in merged.items() if key in _MODEL_SETTING_FIELDS
        }
        if adapter.default_max_tokens is not None:
            settings.setdefault("max_tokens", adapter.default_max_tokens)
        settings["include_usage"] = True
        settings["preserve_raw_usage"] = True
        extra_body = adapter.extra_body(config, purpose)
        if extra_body:
            settings["extra_body"] = extra_body
        return ModelSettings(**settings)

    def build_direct_client(
        self,
        config: ResolvedModelConfig,
        purpose: RuntimePurpose = RuntimePurpose.INTEGRATED_APP,
    ) -> DirectModelClient:
        settings = self.build_model_settings(config, purpose)
        request_args = {
            key: value for key in _MODEL_SETTING_FIELDS
            if (value := getattr(settings, key, None)) is not None
        }
        if settings.extra_body:
            request_args["extra_body"] = dict(settings.extra_body)
        return DirectModelClient(
            client=self._client(config),
            model=config.api_model,
            request_args=request_args,
            include_usage=bool(settings.include_usage),
            provider_kind=config.provider_kind,
            usage_recorder=self.usage_recorder,
            usage_identity=UsageIdentity.from_config(config),
        )
