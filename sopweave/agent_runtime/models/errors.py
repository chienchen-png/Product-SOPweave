from __future__ import annotations

import json
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
)

_CONTEXT_LIMIT_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "too many tokens",
    "input is too long",
    "prompt is too long",
)


def classify_provider_error(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return "authentication_error"
    if isinstance(exc, RateLimitError):
        return "rate_limit_error"
    if isinstance(exc, APITimeoutError):
        return "timeout"
    if isinstance(exc, APIConnectionError):
        return "connection_error"
    if isinstance(exc, NotFoundError):
        return "model_not_found"
    if isinstance(exc, BadRequestError):
        if any(marker in _error_text(exc) for marker in _CONTEXT_LIMIT_MARKERS):
            return "context_length_exceeded"
        return "invalid_request"
    return "invalid_response"


def provider_error_message(exc: Exception) -> str:
    code = classify_provider_error(exc)
    return {
        "authentication_error": "模型认证失败",
        "rate_limit_error": "模型请求频率受限",
        "timeout": "模型请求超时",
        "connection_error": "无法连接模型供应商",
        "model_not_found": "模型或接口不存在，请检查 API 模型标识和供应商地址",
        "context_length_exceeded": "输入超过模型上下文上限，请清理上下文或缩短附件后重试",
        "invalid_request": "模型供应商拒绝了请求参数",
    }.get(code, "智能体运行失败")


def _error_text(exc: BadRequestError) -> str:
    body: Any = getattr(exc, "body", None)
    try:
        serialized = json.dumps(body, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = str(body or "")
    return f"{serialized} {exc}".lower()
