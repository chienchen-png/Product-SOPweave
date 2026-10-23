from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_runtime.checkpoints import (
    CheckpointConflictError,
    CheckpointStore,
)
from agent_runtime.session import WorkbenchSession
from storage.db import _ensure_agent_runtime_schema


class WorkbenchSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        _ensure_agent_runtime_schema(self.db)
        self.session = WorkbenchSession("session-1", db=self.db)
        self.addCleanup(self.db.close)

    def test_items_round_trip_in_order_with_chinese_and_tool_fields(self) -> None:
        items = [
            {"role": "user", "content": "核对这两个表"},
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "query_table",
                "arguments": '{"file":"总账.xlsx"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": {"success": True, "rows": [{"金额": 12.5}]},
            },
        ]

        asyncio.run(self.session.add_items(items))

        self.assertEqual(asyncio.run(self.session.get_items()), items)
        self.assertEqual(asyncio.run(self.session.get_items(limit=2)), items[-2:])
        self.assertEqual(asyncio.run(self.session.get_items(limit=0)), [])

    def test_pop_and_clear_preserve_session_boundaries(self) -> None:
        other = WorkbenchSession("session-2", db=self.db)
        asyncio.run(self.session.add_items([{"role": "user", "content": "one"}]))
        asyncio.run(self.session.add_items([{"role": "assistant", "content": "two"}]))
        asyncio.run(other.add_items([{"role": "user", "content": "other"}]))

        self.assertEqual(
            asyncio.run(self.session.pop_item()),
            {"role": "assistant", "content": "two"},
        )
        asyncio.run(self.session.clear_session())

        self.assertEqual(asyncio.run(self.session.get_items()), [])
        self.assertEqual(
            asyncio.run(other.get_items()),
            [{"role": "user", "content": "other"}],
        )
        self.assertIsNone(asyncio.run(self.session.pop_item()))


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "checkpoints.sqlite3"
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        _ensure_agent_runtime_schema(db)
        db.close()
        self.store = CheckpointStore(self.path)

    def test_checkpoint_round_trip_and_exclusive_acquire(self) -> None:
        self.store.save("run-1", "{\"state\":1}", sdk_version="0.23.1")

        saved = self.store.load("run-1")
        lease = self.store.acquire("run-1")

        self.assertEqual(saved.checkpoint_json, "{\"state\":1}")
        self.assertEqual(saved.sdk_version, "0.23.1")
        self.assertEqual(lease.checkpoint_json, saved.checkpoint_json)
        with self.assertRaises(CheckpointConflictError):
            CheckpointStore(self.path).acquire("run-1")

    def test_failed_resume_can_release_and_retry(self) -> None:
        self.store.save("run-2", "state", sdk_version="0.23.1")
        first = self.store.acquire("run-2")

        self.store.release("run-2", first.lease_token)
        second = self.store.acquire("run-2")

        self.assertNotEqual(first.lease_token, second.lease_token)

    def test_completed_checkpoint_cannot_be_resumed(self) -> None:
        self.store.save("run-3", "state", sdk_version="0.23.1")
        lease = self.store.acquire("run-3")
        self.store.complete("run-3", lease.lease_token)

        self.assertEqual(self.store.load("run-3").status, "completed")
        with self.assertRaises(CheckpointConflictError):
            self.store.acquire("run-3")

    def test_concurrent_acquire_has_exactly_one_winner(self) -> None:
        self.store.save("run-4", "state", sdk_version="0.23.1")
        barrier = threading.Barrier(2)

        def acquire():
            barrier.wait(timeout=2)
            try:
                return CheckpointStore(self.path).acquire("run-4").lease_token
            except CheckpointConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: acquire(), range(2)))

        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(sum(value != "conflict" for value in results), 1)


if __name__ == "__main__":
    unittest.main()
