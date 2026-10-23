"""App models application service."""
from __future__ import annotations
import asyncio
import logging
from typing import Any
from agent_runtime.models.factory import (
    DirectModelClient,
    ModelRuntimeFactory,
    resolve_model_config,
)
from storage.connections import get_connection as get_db
from storage.model_store import ModelStore
from services.model_usage import recorder_for_connection


logger = logging.getLogger(__name__)

def _chat_complete(client: DirectModelClient, messages: list[dict],
                   temperature: float = 0.2, max_tokens: int = 6000,
                   retries: int = 1,
                   thinking: bool | None = None,
                   reasoning_effort: str | None = None) -> str:
    """用共享供应商适配器完成一次无思考文本生成。"""
    del thinking

    async def execute() -> str:
        last_err: Exception | None = None
        try:
            for attempt in range(retries + 1):
                try:
                    overrides: dict[str, Any] = {
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    }
                    if reasoning_effort:
                        overrides["reasoning_effort"] = reasoning_effort
                    response = await client.complete(messages, **overrides)
                    message = response.choices[0].message
                    text = str(getattr(message, "content", None) or "")
                    if text.strip():
                        return text
                    last_err = ValueError("模型返回空内容")
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    logger.warning("LLM 报告生成第 %d 次失败: %s", attempt + 1, exc)
            raise last_err or ValueError("模型返回空内容")
        finally:
            await client.aclose()

    return asyncio.run(execute())


def _build_llm_client(model_id: str) -> DirectModelClient:
    """从稳定模型 ID 构建集成应用共用的直接客户端。"""
    try:
        record = ModelStore(get_db()).resolve_runtime_model(model_id)
        return ModelRuntimeFactory(usage_recorder=recorder_for_connection(get_db())).build_direct_client(resolve_model_config(record))
    except ValueError:
        raise
