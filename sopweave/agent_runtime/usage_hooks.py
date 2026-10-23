"""Public Agents SDK hooks, scoped to one Runner execution."""

import asyncio
import time
import uuid
from agents import RunHooks


class UsageRunHooks(RunHooks):
    def __init__(self, recorder, identity, *, run_id="", project_id=""):
        self.recorder = recorder
        self.identity = identity
        self.run_id = run_id
        self.project_id = project_id
        self.execution_id = uuid.uuid4().hex
        self.call_id = None
        self.started = 0.0

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        if self.call_id:
            await self._finish("failed")
        self.started = time.monotonic()
        self.call_id = await asyncio.to_thread(
            self.recorder.begin,
            self.identity,
            run_id=self.run_id,
            execution_id=self.execution_id,
            project_id=self.project_id,
        )

    async def on_llm_end(self, context, agent, response):
        await self._finish(
            "completed",
            getattr(response, "raw_usage", None),
            getattr(response, "request_id", None),
        )

    async def _finish(self, state, raw=None, request_id=None):
        call_id, self.call_id = self.call_id, None
        if call_id:
            await asyncio.to_thread(
                self.recorder.finish,
                call_id,
                state=state,
                raw_usage=raw,
                provider_request_id=request_id,
                duration_ms=max(0, round((time.monotonic() - self.started) * 1000)),
            )

    async def close(self, state="interrupted"):
        await self._finish(state)
        await asyncio.to_thread(
            self.recorder.interrupt_execution, self.execution_id, state
        )
