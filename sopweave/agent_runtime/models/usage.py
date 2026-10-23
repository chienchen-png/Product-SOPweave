"""Provider-reported usage, preserving missing values and identity snapshots."""

from dataclasses import dataclass
from collections.abc import Mapping
import json
import hashlib


@dataclass(frozen=True)
class UsageIdentity:
    provider_id: str
    provider_name: str
    provider_kind: str
    model_id: str
    model_name: str
    api_model: str

    @classmethod
    def from_config(cls, config):
        provider_id = config.provider_id
        provider_name = config.provider_name
        model_id = config.model_id
        if not provider_id:
            from .templates import list_provider_templates

            fingerprint = hashlib.sha256(
                f"{config.provider_kind}:{config.base_url}".encode()
            ).hexdigest()[:24]
            provider_id = f"unsaved:{fingerprint}"
            provider_name = next(
                (
                    template.display_name
                    for template in list_provider_templates()
                    if template.provider_kind == config.provider_kind
                ),
                "自定义供应商",
            )
            if model_id in {"connection-test", "provider-bundle-check"}:
                model_id = (
                    "probe:"
                    + hashlib.sha256(
                        f"{provider_id}:{config.api_model}".encode()
                    ).hexdigest()[:24]
                )
        return cls(
            provider_id,
            provider_name,
            str(config.provider_kind),
            model_id,
            config.display_name,
            config.api_model,
        )


@dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    state: str = "missing"
    raw_json: str = "{}"


def _count(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def normalize_usage(raw, provider_kind="openai_compatible") -> NormalizedUsage:
    """Normalize only explicit provider numbers; never infer from generated text."""
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json", exclude_unset=True)
    if not isinstance(raw, Mapping):
        return NormalizedUsage()
    incoming = _count(raw.get("prompt_tokens", raw.get("input_tokens")))
    outgoing = _count(raw.get("completion_tokens", raw.get("output_tokens")))
    total = _count(raw.get("total_tokens"))
    state = "reported" if total is not None else "missing"
    if total is None and incoming is not None and outgoing is not None:
        total = _count(incoming + outgoing)
        state = "derived" if total is not None else "missing"
    details = raw.get("completion_tokens_details", raw.get("output_tokens_details"))
    reasoning = (
        _count(details.get("reasoning_tokens"))
        if isinstance(details, Mapping)
        else None
    )
    safe = {}
    for key in (
        "prompt_tokens",
        "input_tokens",
        "completion_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    ):
        if _count(raw.get(key)) is not None:
            safe[key] = raw[key]
    for key in (
        "prompt_tokens_details",
        "input_tokens_details",
        "completion_tokens_details",
        "output_tokens_details",
    ):
        value = raw.get(key)
        if isinstance(value, Mapping):
            safe[key] = {
                name: value[name]
                for name in (
                    "cached_tokens",
                    "cache_write_tokens",
                    "reasoning_tokens",
                    "audio_tokens",
                    "text_tokens",
                    "image_tokens",
                    "accepted_prediction_tokens",
                    "rejected_prediction_tokens",
                )
                if _count(value.get(name)) is not None
            }
    return NormalizedUsage(
        incoming,
        outgoing,
        total,
        reasoning,
        state,
        json.dumps(safe, separators=(",", ":")),
    )
