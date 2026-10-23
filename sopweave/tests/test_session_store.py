"""Session timing lifecycle without touching application data."""
import sqlite3
import unittest
from pathlib import Path

from storage.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(Path("storage/schema.sql").read_text(encoding="utf-8"))
        self.store = SessionStore(self.db)

    def test_start_and_close_are_persisted_and_close_is_idempotent(self):
        session = self.store.start()
        self.assertEqual(session["status"], "open")
        self.assertIsNone(session["end_at"])
        closed = self.store.close(session["id"])
        self.assertEqual(closed["status"], "closed")
        self.assertIsNotNone(closed["end_at"])
        self.assertIsNone(self.store.close(session["id"]))

    def test_restart_ends_stale_session_at_last_heartbeat(self):
        previous = self.store.start()
        self.store.heartbeat(previous["id"], min_interval_sec=0)
        current = self.store.start()
        stale = self.db.execute(
            "SELECT * FROM app_sessions WHERE id = ?", (previous["id"],)
        ).fetchone()
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["end_at"], stale["last_seen_at"])
        self.assertEqual(current["status"], "open")

    def test_heartbeat_leaves_closed_session_unchanged(self):
        session = self.store.start()
        closed = self.store.close(session["id"])
        self.store.heartbeat(session["id"], min_interval_sec=0)
        last_seen = self.db.execute(
            "SELECT last_seen_at FROM app_sessions WHERE id = ?", (session["id"],)
        ).fetchone()[0]
        self.assertEqual(last_seen, closed["last_seen_at"])
