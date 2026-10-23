from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from pydantic import SecretStr


class ProviderKind(StrEnum):
    DEEPSEEK = "deepseek"
    ZHIPU = "zhipu"
    SILICONFLOW = "siliconflow"
    DASHSCOPE = "dashscope"
    OPENAI_COMPATIBLE = "openai_compatible"


class ApiMode(StrEnum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"


class RuntimePurpose(StrEnum):
    AGENT = "agent"
    INTEGRATED_APP = "integrated_app"
    PROVIDER_CHECK = "provider_check"


@dataclass(frozen=True)
class ResolvedModelConfig:
    model_id: str
    display_name: str
    api_model: str
    base_url: str
    api_key: SecretStr = field(repr=False)
    provider_id: str = ""
    provider_name: str = ""
    provider_kind: ProviderKind = ProviderKind.OPENAI_COMPATIBLE
    api_mode: ApiMode = ApiMode.CHAT_COMPLETIONS
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    request_options: Mapping[str, Any] = field(default_factory=dict)
    provider_options: Mapping[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            "ResolvedModelConfig("
            f"model_id={self.model_id!r}, display_name={self.display_name!r}, "
            f"api_model={self.api_model!r}, base_url={self.base_url!r}, "
            f"api_key=SecretStr('**********'), provider_kind={self.provider_kind!r}, "
            f"api_mode={self.api_mode!r})"
        )
