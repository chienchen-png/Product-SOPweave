from __future__ import annotations

import json
from dataclasses import dataclass, field
import asyncio
from typing import Any, Awaitable, Callable

from openai import RateLimitError

from .factory import DirectModelClient
from .errors import classify_provider_error
from .types import ProviderKind, ResolvedModelConfig


@dataclass(frozen=True)
class ContractCheck:
    status: str
    detail: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class ProviderContractResult:
    checks: dict[str, ContractCheck]

    @property
    def passed(self) -> bool:
        return all(check.status == "passed" for check in self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": {name: check.to_dict() for name, check in self.checks.items()},
        }


class ProviderContractRunner:
    def __init__(
        self,
        client: DirectModelClient,
        *,
        interval_seconds: float = 0,
        max_rate_limit_retries: int = 0,
        default_retry_seconds: float = 1,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.interval_seconds = max(float(interval_seconds), 0)
        self.max_rate_limit_retries = max(int(max_rate_limit_retries), 0)
        self.default_retry_seconds = max(float(default_retry_seconds), 0)
        self.sleep = sleep

    async def run(self) -> ProviderContractResult:
        checks: dict[str, ContractCheck] = {}
        operations: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]] = [
            ("text", self._check_text),
            ("stream", self._check_stream),
            ("tools", self._check_tools),
            ("tool_resume", self._check_tool_resume),
            ("usage", self._check_usage),
        ]
        for index, (name, operation) in enumerate(operations):
            try:
                if index and self.interval_seconds:
                    await self.sleep(self.interval_seconds)
                checks[name] = ContractCheck(
                    status="passed", detail=await self._run_operation(operation)
                )
            except Exception as exc:  # noqa: BLE001 - compatibility probe records each failure
                checks[name] = ContractCheck(
                    status="failed",
                    detail={"exception_type": type(exc).__name__},
                    error_code=_error_code(exc),
                )
        return ProviderContractResult(checks=checks)

    async def _run_operation(
        self, operation: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        attempts = 0
        while True:
            try:
                return await operation()
            except RateLimitError as exc:
                if attempts >= self.max_rate_limit_retries:
                    raise
                attempts += 1
                await self.sleep(_retry_after_seconds(exc, self.default_retry_seconds))

    async def _check_text(self) -> dict[str, Any]:
        response = await self.client.complete([
            {"role": "user", "content": "contract:text"},
        ], max_tokens=16)
        text = response.choices[0].message.content or ""
        if not text:
            raise ValueError("empty_text_response")
        return {"received": True}

    async def _check_stream(self) -> dict[str, Any]:
        parts: list[str] = []
        async for chunk in self.client.stream([
            {"role": "user", "content": "contract:stream"},
        ], max_tokens=16):
            for choice in chunk.choices:
                if choice.delta.content:
                    parts.append(choice.delta.content)
        if not parts:
            raise ValueError("empty_stream_response")
        return {"chunks": len(parts)}

    async def _check_tools(self) -> dict[str, Any]:
        name = ""
        call_id = ""
        argument_parts: list[str] = []
        async for chunk in self.client.stream(
            [{
                "role": "user",
                "content": (
                    "contract:tool\n"
                    "Call contract_tool exactly once with value=2. "
                    "Do not answer with plain text."
                ),
            }],
            tools=[{
                "type": "function",
                "function": {
                    "name": "contract_tool",
                    "description": "Return a contract value.",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                },
            }],
            tool_choice=self.client.named_tool_choice("contract_tool"),
        ):
            for choice in chunk.choices:
                for tool_call in choice.delta.tool_calls or []:
                    call_id = tool_call.id or call_id
                    if tool_call.function:
                        name = tool_call.function.name or name
                        argument_parts.append(tool_call.function.arguments or "")
        arguments = json.loads("".join(argument_parts))
        if name != "contract_tool" or not call_id:
            raise ValueError("invalid_tool_call")
        return {"name": name, "call_id": call_id, "arguments": arguments}

    async def _check_tool_resume(self) -> dict[str, Any]:
        response = await self.client.complete([
            {"role": "user", "content": "contract:tool-resume"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-contract",
                    "type": "function",
                    "function": {"name": "contract_tool", "arguments": "{\"value\":2}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call-contract", "content": "2"},
        ], max_tokens=16)
        text = response.choices[0].message.content or ""
        if not text:
            raise ValueError("empty_tool_resume_response")
        return {"received": True}

    async def _check_usage(self) -> dict[str, Any]:
        total_tokens: int | None = None
        async for chunk in self.client.stream([
            {"role": "user", "content": "contract:usage"},
        ], max_tokens=16):
            if chunk.usage is not None:
                total_tokens = chunk.usage.total_tokens
        if total_tokens is None:
            raise ValueError("missing_stream_usage")
        return {"total_tokens": total_tokens}


def _error_code(exc: Exception) -> str:
    return classify_provider_error(exc)


def _retry_after_seconds(exc: RateLimitError, default: float) -> float:
    raw = None
    response = getattr(exc, "response", None)
    if response is not None:
        raw = response.headers.get("Retry-After")
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        parsed = default
    if parsed < 0:
        parsed = default
    return min(parsed, 30.0)


def contract_runner_for(
    config: ResolvedModelConfig, client: DirectModelClient
) -> ProviderContractRunner:
    if config.provider_kind is ProviderKind.ZHIPU:
        return ProviderContractRunner(
            client,
            interval_seconds=0.25,
            max_rate_limit_retries=1,
            default_retry_seconds=1,
        )
    return ProviderContractRunner(client)
