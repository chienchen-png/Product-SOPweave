from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from routes.workspace import workspace_bp
from storage.db import _ensure_model_schema
from storage.run_store import RunStore


class WorkspaceCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "storage" / "schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        _ensure_model_schema(self.db)
        self.app = Flask(__name__)
        self.app.register_blueprint(workspace_bp)
        self.client = self.app.test_client()
        self.db_patch = patch("routes.workspace.get_db", return_value=self.db)
        self.db_patch.start()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.db.close()

    def _conversation(self) -> None:
        self.db.execute(
            "INSERT INTO projects (id, name, work_dir, work_mode, created_at, updated_at) "
            "VALUES ('p1', 'Project', '.', 'manual', 'now', 'now')"
        )
        self.db.execute(
            "INSERT INTO conversations (id, project_id, title, created_at, updated_at) "
            "VALUES ('c1', 'p1', 'History', 'now', 'now')"
        )
        self.db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES ('m1', 'c1', 'user', '核对数据', 'now')"
        )
        self.db.commit()

    def test_history_reconstructs_canonical_v2_events(self) -> None:
        self._conversation()
        store = RunStore(self.db)
        store.create_run(
            "r1",
            project_id="p1",
            conversation_id="c1",
            user_message_id="m1",
            title="fallback",
        )
        store.insert_event("r1", 1, "message.delta", {"delta": "核对"})
        store.insert_event("r1", 2, "message.delta", {"delta": "完成"})
        store.update_run_status("r1", "completed")

        response = self.client.get("/api/workspace/history?project_id=p1")

        run = response.get_json()["data"]["runs"][0]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(run["user_prompt"], "核对数据")
        self.assertEqual(run["assistant_text"], "核对完成")

    def test_failed_history_preserves_error_separately_from_partial_output(self):
        self._conversation()
        store = RunStore(self.db)
        store.create_run("failed", project_id="p1", conversation_id="c1")
        store.insert_event("failed", 1, "message.delta", {"delta": "开始检查"})
        store.insert_event("failed", 2, "run.failed", {"message": "模型认证失败"})
        store.update_run_status("failed", "failed", error_message="模型认证失败")
        run = self.client.get("/api/workspace/history?project_id=p1").get_json()[
            "data"
        ]["runs"][0]
        self.assertEqual(run["assistant_text"], "开始检查")
        self.assertEqual(run["error_message"], "模型认证失败")

    def test_reset_removes_v2_sessions_checkpoints_and_history(self) -> None:
        self._conversation()
        store = RunStore(self.db)
        store.create_run("r1", project_id="p1", conversation_id="c1")
        store.update_run_status("r1", "stopped")
        store.insert_event("r1", 1, "message.delta", {"delta": "done"})
        self.db.execute(
            "INSERT INTO agent_session_items (session_id, seq, item_json, created_at) "
            "VALUES ('c1:general-agent:model-1', 1, '{}', 'now')"
        )
        self.db.execute(
            "INSERT INTO agent_run_checkpoints "
            "(run_id, checkpoint_json, sdk_version, status, updated_at) "
            "VALUES ('r1', '{}', 'test', 'ready', 'now')"
        )
        self.db.commit()

        response = self.client.post(
            "/api/workspace/context/reset", json={"project_id": "p1"}
        )

        self.assertEqual(response.status_code, 200)
        for table in (
            "messages",
            "conversations",
            "runs",
            "run_events",
            "agent_session_items",
            "agent_run_checkpoints",
        ):
            count = self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0, table)

    def test_reset_rejects_live_run_without_removing_history(self):
        self._conversation()
        RunStore(self.db).create_run("live", project_id="p1", conversation_id="c1")
        response = self.client.post(
            "/api/workspace/context/reset", json={"project_id": "p1"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1
        )

    def test_reset_rejects_waiting_run_until_it_is_stopped(self):
        self._conversation()
        store = RunStore(self.db)
        store.create_run("waiting", project_id="p1", conversation_id="c1")
        store.update_run_status("waiting", "waiting")
        response = self.client.post("/api/workspace/context/reset", json={"project_id": "p1"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
        store.update_run_status("waiting", "stopped")
        response = self.client.post("/api/workspace/context/reset", json={"project_id": "p1"})
        self.assertEqual(response.status_code, 200)

    def test_reset_rejects_malformed_payload_without_touching_history(self):
        self._conversation()
        for body in (["p1"], "p1", 1, {"project_id": ["p1"]}, {}):
            with self.subTest(body=body):
                response = self.client.post("/api/workspace/context/reset", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.db.execute("select count(*) from messages").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
