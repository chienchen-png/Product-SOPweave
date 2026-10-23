"""Explicit public schemas and operation ownership shared by tool groups."""

from __future__ import annotations

import asyncio
import inspect
import threading
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from agent_runtime.context import WorkbenchRunContext
from storage.connections import connection_scope
from storage.operations import operation_scope
from .catalog import ToolSpec


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


def typed_tool(
    name: str,
    description: str,
    model: type[BaseModel],
    function: Callable,
    *,
    group: str,
    risk: str = "read",
    timeout: float = 120,
) -> ToolSpec:
    async def invoke(context, arguments):
        from services.skills.policies import enforce_operation
        enforce_operation(context,name,risk,arguments)
        if inspect.iscoroutinefunction(function):
            return await function(context, model.model_validate(arguments))
        return await database_call(
            context, lambda: function(context, model.model_validate(arguments))
        )

    return ToolSpec(name, description, group, model, invoke, timeout=timeout, risk=risk)


async def database_call(context: WorkbenchRunContext, operation: Callable[[], Any]):
    cancelled = threading.Event()

    def execute():
        with operation_scope(cancelled):
            if context.database_factory is None:
                return operation()
            with connection_scope(context.database_factory) as db:
                db.set_progress_handler(lambda: int(cancelled.is_set()), 1000)
                result = operation()
                if isinstance(result, dict) and result.get("success") is False:
                    db.rollback()
                return result

    task = asyncio.create_task(asyncio.to_thread(execute))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        # Wait for cooperative cleanup before advertising cancellation.
        await asyncio.gather(task, return_exceptions=True)
        raise
