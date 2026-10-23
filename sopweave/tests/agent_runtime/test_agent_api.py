from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from flask import Flask, jsonify

from agent_runtime.api import create_agent_api
from agent_runtime.events import WorkbenchEvent


class _Service:
    def __init__(self) -> None:
        self.cancelled = []

    async def start(self, request):
        yield _event(request.run_id, 1, "run.created", {"ok": True})
        yield _event(request.run_id, 2, "message.delta", {"delta": "你好"})
        yield _event(request.run_id, 3, "run.completed", {"output": "你好"})

    async def resume(self, run_id, command):
        yield _event(run_id, 4, "run.resumed", {"action": command.action})
        yield _event(run_id, 5, "run.completed", {})

    def cancel(self, run_id):
        self.cancelled.append(run_id)

    def is_active(self, _run_id):
        return False


class AgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "api.sqlite3"
        db = sqlite3.connect(self.db_path)
        schema = Path(__file__).resolve().parents[2] / "storage" / "schema.sql"
        db.executescript(schema.read_text(encoding="utf-8"))
        db.execute(
            """INSERT INTO runs
               (id, status, created_at, updated_at) VALUES ('saved-run','completed','now','now')"""
        )
        for seq, event_type, payload in (
            (1, "run.created", {"n": 1}),
            (2, "message.delta", {"delta": "A"}),
            (3, "tool.completed", {"tool_id": "call-1"}),
        ):
            db.execute(
                """INSERT INTO run_events
                   (run_id, seq, event_type, payload_json, created_at)
                   VALUES ('saved-run', ?, ?, ?, 'now')""",
                (seq, event_type, json.dumps(payload)),
            )
        db.commit()
        db.close()
        flask_app = Flask(__name__)

        @flask_app.get("/api/options")
        def options():
            return jsonify({"success": True, "data": {"models": [], "agents": []}})

        self.service = _Service()
        self.client = TestClient(create_agent_api(
            run_service=self.service,
            flask_app=flask_app,
            db_path=self.db_path,
        ))

    def test_post_stream_has_sse_headers_and_complete_frames(self) -> None:
        response = self.client.post("/api/agent/runs", json={
            "run_id": "run-stream",
            "prompt": "你好",
            "project_id": "p1",
            "conversation_id": "c1",
            "agent_id": "general-agent",
            "model_id": "m1",
            "work_dir": str(self.root),
            "work_mode": "manual",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertIn("id: 1\nevent: run.created\ndata:", response.text)
        self.assertIn("id: 2\nevent: message.delta\ndata:", response.text)
        self.assertIn("你好", response.text)

    def test_flask_routes_remain_available_through_the_root_mount(self) -> None:
        response = self.client.get("/api/options")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    def test_replay_after_seq_returns_each_persisted_event_once(self) -> None:
        response = self.client.get("/api/agent/runs/saved-run/events?after_seq=1")

        self.assertEqual(response.status_code, 200)
        events = response.json()["data"]["events"]
        self.assertEqual([event["seq"] for event in events], [2, 3])
        self.assertEqual(events[1]["payload"]["tool_id"], "call-1")
        self.assertEqual(len({event["seq"] for event in events}), len(events))

    def test_status_resume_and_cancel_endpoints(self) -> None:
        status = self.client.get("/api/agent/runs/saved-run")
        resumed = self.client.post(
            "/api/agent/runs/saved-run/resume",
            json={'action':'respond','interaction_id':'test','version':1,'answers':[]},
        )
        cancelled = self.client.post("/api/agent/runs/saved-run/cancel")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["data"]["run"]["status"], "completed")
        self.assertIn("event: run.resumed", resumed.text)
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.service.cancelled, ["saved-run"])


def _event(run_id: str, seq: int, event_type: str, payload: dict) -> WorkbenchEvent:
    return WorkbenchEvent(run_id, seq, event_type, payload, "now")


if __name__ == "__main__":
    unittest.main()
