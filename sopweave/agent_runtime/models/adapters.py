from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .types import ProviderKind, ResolvedModelConfig, RuntimePurpose


def _clean_openai_base_url(value: str) -> tuple[str, str, str]:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("API URL 必须是有效的 HTTP(S) 地址")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")].rstrip("/")
    return parsed.scheme, parsed.netloc, path


@dataclass(frozen=True)
class ProviderAdapter:
    kind: ProviderKind
    default_path: str = "/v1"
    default_max_tokens: int | None = None

    def named_tool_choice(self, name: str) -> str:
        """Select a function using the provider's documented tool-choice mode."""
        return name

    def normalize_base_url(self, value: str) -> str:
        scheme, netloc, path = _clean_openai_base_url(value)
        normalized_path = path or self.default_path
        return urlunsplit((scheme, netloc, normalized_path.rstrip("/") + "/", "", ""))

    def extra_body(
        self, config: ResolvedModelConfig, purpose: RuntimePurpose
    ) -> dict:
        configured = config.request_options.get("extra_body")
        body = dict(configured) if isinstance(configured, dict) else {}
        if self.kind is not ProviderKind.OPENAI_COMPATIBLE:
            return body
        style = str(config.provider_options.get("thinking_style") or "none")
        thinking = _thinking_enabled(config, purpose)
        if style == "none":
            return body
        if style == "thinking_object":
            if thinking is not None:
                body["thinking"] = {"type": "enabled" if thinking else "disabled"}
            return body
        if style == "enable_thinking":
            if thinking is not None:
                body["enable_thinking"] = thinking
            return body
        raise ValueError(f"不支持的思考参数兼容方式：{style}")


class ThinkingObjectAdapter(ProviderAdapter):
    def extra_body(self, config: ResolvedModelConfig, purpose: RuntimePurpose) -> dict:
        body = super().extra_body(config, purpose)
        thinking = _thinking_enabled(config, purpose)
        if thinking is not None:
            body["thinking"] = {"type": "enabled" if thinking else "disabled"}
        return body


class DeepSeekAdapter(ThinkingObjectAdapter):
    pass


class ZhipuAdapter(ThinkingObjectAdapter):
    def named_tool_choice(self, name: str) -> str:
        # GLM's Chat Completions contract currently only documents auto.
        return "auto"


class EnableThinkingAdapter(ProviderAdapter):
    def extra_body(self, config: ResolvedModelConfig, purpose: RuntimePurpose) -> dict:
        body = super().extra_body(config, purpose)
        thinking = _thinking_enabled(config, purpose)
        if thinking is not None:
            body["enable_thinking"] = thinking
        return body


class DashScopeAdapter(EnableThinkingAdapter):
    pass


class SiliconFlowAdapter(EnableThinkingAdapter):
    pass


def _thinking_enabled(
    config: ResolvedModelConfig, purpose: RuntimePurpose
) -> bool | None:
    if purpose in {RuntimePurpose.INTEGRATED_APP, RuntimePurpose.PROVIDER_CHECK}:
        return False
    configured = config.request_options.get("thinking_enabled")
    return configured if isinstance(configured, bool) else None


_ADAPTERS = {
    ProviderKind.DEEPSEEK: DeepSeekAdapter(ProviderKind.DEEPSEEK, "/v1"),
    ProviderKind.ZHIPU: ZhipuAdapter(ProviderKind.ZHIPU, "/api/paas/v4"),
    ProviderKind.DASHSCOPE: DashScopeAdapter(
        ProviderKind.DASHSCOPE, "/compatible-mode/v1"
    ),
    ProviderKind.SILICONFLOW: SiliconFlowAdapter(
        ProviderKind.SILICONFLOW, "/v1", default_max_tokens=4096
    ),
    ProviderKind.OPENAI_COMPATIBLE: ProviderAdapter(
        ProviderKind.OPENAI_COMPATIBLE, "/v1"
    ),
}


def adapter_for(kind: ProviderKind | str) -> ProviderAdapter:
    try:
        provider_kind = kind if isinstance(kind, ProviderKind) else ProviderKind(kind)
    except ValueError as exc:
        raise ValueError(f"不支持的供应商类型：{kind}") from exc
    return _ADAPTERS[provider_kind]
