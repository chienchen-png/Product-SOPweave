from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .types import ApiMode, ProviderKind


@dataclass(frozen=True)
class ProviderTemplate:
    template_id: str
    display_name: str
    provider_kind: ProviderKind
    base_url: str | None
    api_mode: ApiMode
    auth_mode: str
    thinking_style: str
    is_custom: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["provider_kind"] = self.provider_kind.value
        result["api_mode"] = self.api_mode.value
        return result


_TEMPLATES = (
    ProviderTemplate(
        template_id="deepseek",
        display_name="DeepSeek 官方",
        provider_kind=ProviderKind.DEEPSEEK,
        base_url="https://api.deepseek.com",
        api_mode=ApiMode.CHAT_COMPLETIONS,
        auth_mode="bearer",
        thinking_style="thinking_object",
    ),
    ProviderTemplate(
        template_id="zhipu",
        display_name="智谱 GLM 官方",
        provider_kind=ProviderKind.ZHIPU,
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_mode=ApiMode.CHAT_COMPLETIONS,
        auth_mode="bearer",
        thinking_style="thinking_object",
    ),
    ProviderTemplate(
        template_id="siliconflow",
        display_name="硅基流动",
        provider_kind=ProviderKind.SILICONFLOW,
        base_url="https://api.siliconflow.cn/v1",
        api_mode=ApiMode.CHAT_COMPLETIONS,
        auth_mode="bearer",
        thinking_style="enable_thinking",
    ),
    ProviderTemplate(
        template_id="custom",
        display_name="自定义供应商",
        provider_kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url=None,
        api_mode=ApiMode.CHAT_COMPLETIONS,
        auth_mode="bearer",
        thinking_style="none",
        is_custom=True,
    ),
)

_BY_ID = {template.template_id: template for template in _TEMPLATES}


def list_provider_templates() -> tuple[ProviderTemplate, ...]:
    return _TEMPLATES


def get_provider_template(template_id: str) -> ProviderTemplate:
    try:
        return _BY_ID[str(template_id or "").strip()]
    except KeyError as exc:
        raise ValueError(f"不支持的供应商模板：{template_id}") from exc
