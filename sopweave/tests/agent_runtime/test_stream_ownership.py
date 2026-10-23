from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from agent_runtime.api import _stream_events
from agent_runtime.events import WorkbenchEvent
from agent_runtime.service import RunService, stream_owner


class StreamOwnershipTests(unittest.TestCase):
    def test_duplicate_stream_does_not_cancel_execution_owner(self):
        class Request:
            async def is_disconnected(self):
                return False

        class Service:
            cancel_stream = RunService.cancel_stream

            def __init__(self):
                self._active = set()
                self._stream_owners = {}
                self.cancelled = []

            def is_active(self, run_id):
                return run_id in self._active

            def cancel(self, run_id):
                self.cancelled.append(run_id)
                self._active.discard(run_id)

            def _connect(self):
                return SimpleNamespace(
                    close=lambda: None,
                    execute=lambda *_: SimpleNamespace(
                        fetchone=lambda: {"status": "running"}
                    ),
                )

        async def scenario():
            service = Service()

            async def execution():
                service._active.add("run")
                service._stream_owners["run"] = stream_owner.get()
                yield WorkbenchEvent("run", 1, "run.resumed", {}, "now")
                await asyncio.Event().wait()

            async def duplicate():
                yield WorkbenchEvent("run", 2, "interaction.resolved", {}, "now")

            first = _stream_events(
                execution(), request=Request(), service=service, run_id="run"
            )
            await anext(first)
            second = _stream_events(
                duplicate(), request=Request(), service=service, run_id="run"
            )
            self.assertEqual(len([event async for event in second]), 1)
            self.assertEqual(service.cancelled, [])
            self.assertTrue(service.is_active("run"))
            await first.aclose()
            self.assertEqual(service.cancelled, ["run"])

        asyncio.run(scenario())
