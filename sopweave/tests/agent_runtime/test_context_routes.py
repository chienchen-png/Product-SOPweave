from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from routes.context import context_bp
from routes.workspace import workspace_bp
from storage.db import _ensure_model_schema


class ContextRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "storage" / "schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        _ensure_model_schema(self.db)
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
            "VALUES ('m1', 'c1', 'user', '一二三四', 'now')"
        )
        self.db.execute(
            "INSERT INTO agent_session_items (session_id, seq, item_json, created_at) "
            "VALUES ('c1:general-agent:model-1', 1, ?, 'now')",
            ('{"content":"一二三四五六七八"}',),
        )
        self.db.commit()
        app = Flask(__name__)
        app.register_blueprint(context_bp)
        self.client = app.test_client()
        self.db_patch = patch("routes.context.get_db", return_value=self.db)
        self.db_patch.start()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.db.close()

    def test_stats_use_persisted_sdk_session_without_legacy_memory_state(self) -> None:
        response = self.client.get("/api/context/stats?project_id=p1")

        data = response.get_json()["data"]
        self.assertEqual(response.status_code, 200)
        self.assertGreater(data["current_tokens"], 0)
        self.assertEqual(data["message_count"], 1)
        self.assertEqual(data["context_window"], 200000)

    def test_stats_use_latest_sdk_run_when_legacy_conversation_row_is_absent(self) -> None:
        self.db.execute("DELETE FROM messages")
        self.db.execute("DELETE FROM conversations")
        self.db.execute(
            "INSERT INTO runs (id, project_id, conversation_id, agent_id, model_id, "
            "title, status, created_at, updated_at) VALUES "
            "('r1', 'p1', 'p1', 'general-agent', 'model-1', 'SDK run', "
            "'completed', 'now', 'later')"
        )
        self.db.execute(
            "INSERT INTO agent_session_items (session_id, seq, item_json, created_at) "
            "VALUES ('p1:general-agent:model-1', 1, ?, 'later')",
            ('{"content":"abcdefghij"}',),
        )
        self.db.commit()

        response = self.client.get("/api/context/stats?project_id=p1")

        data = response.get_json()["data"]
        self.assertEqual(response.status_code, 200)
        self.assertGreater(data["current_tokens"], 0)
        self.assertEqual(data["message_count"], 1)
        self.assertEqual(data["context_window_source"], "default")

    def test_percentage_uses_effective_budget_and_partitions_exclude_tool_entries(self) -> None:
        self.db.execute("DELETE FROM agent_session_items")
        payloads = [
            {
                "type": "message",
                "role": "user",
                "content": "@file:a.txt @folder:reports " + ("x" * 92900),
            },
            {"type": "reasoning", "summary": "r" * 5000},
            {"type": "function_call_output", "output": "tool" * 1000},
        ]
        for seq, payload in enumerate(payloads, 1):
            self.db.execute(
                "INSERT INTO agent_session_items(session_id,seq,item_json,created_at) "
                "VALUES('c1:general-agent:model-1',?,?,'now')",
                (seq, json.dumps(payload, ensure_ascii=False)),
            )
        self.db.commit()

        with patch(
            "routes.context._resolve_window_for_project",
            return_value=(100000, "configured"),
        ):
            response = self.client.get("/api/context/stats?project_id=p1")

        data = response.get_json()["data"]
        self.assertEqual(data["context_window"], 100000)
        self.assertEqual(data["effective_budget"], 85000)
        self.assertEqual(data["usage_pct"], 100)
        self.assertEqual(data["message_count"], 1)
        self.assertEqual(data["tool_result_count"], 1)
        self.assertEqual(data["context_files_count"], 2)
        self.assertGreater(data["partitions"]["reasoning"], 0)
        self.assertGreater(data["partitions"]["tools"], 0)

    def test_stats_do_not_mix_other_model_sessions_and_prefer_request_snapshot(self):
        self.db.execute("INSERT INTO agent_session_items(session_id,seq,item_json,created_at) VALUES('c1:general-agent:other',1,?,'before')",
                        (json.dumps({'role':'user','content':'x'*200000}),))
        self.db.execute("INSERT INTO runs(id,project_id,conversation_id,agent_id,model_id,status,created_at,updated_at) VALUES('snapshot','p1','c1','general-agent','model-1','completed','now','later')")
        snapshot={'context_window':200000,'current_tokens':500,'response_reserve':30000,
                  'effective_budget':170000,'omitted_items':2,'message_count':3}
        self.db.execute("INSERT INTO run_events(run_id,seq,event_type,payload_json,created_at) VALUES('snapshot',1,'context.updated',?,'later')",(json.dumps(snapshot),))
        self.db.commit()
        data=self.client.get('/api/context/stats?project_id=p1').get_json()['data']
        self.assertEqual(data['current_tokens'],500)
        self.assertEqual(data['omitted_items'],2)
        self.assertEqual(data['estimate_scope'],'last_request')

    def test_sdk_conversation_takes_priority_over_stale_legacy_history(self):
        self.db.execute("INSERT INTO runs(id,project_id,conversation_id,agent_id,model_id,status,created_at,updated_at) VALUES('new','p1','sdk-current','general-agent','model-1','completed','now','later')")
        self.db.execute("INSERT INTO agent_session_items(session_id,seq,item_json,created_at) VALUES('sdk-current:general-agent:model-1',1,?,'later')",
                        (json.dumps({'role':'user','content':'current'*100}),))
        self.db.commit()
        data=self.client.get('/api/context/stats?project_id=p1').get_json()['data']
        self.assertGreater(data['current_tokens'],800)

    def test_reset_rejects_active_run_and_preserves_other_project_and_usage(self) -> None:
        self.db.execute(
            "INSERT INTO projects (id,name,work_dir,work_mode,created_at,updated_at) "
            "VALUES ('p2','Other','.', 'manual','now','now')"
        )
        self.db.execute(
            "INSERT INTO runs (id,project_id,conversation_id,status,created_at,updated_at) "
            "VALUES ('r1','p1','c1','running','now','now')"
        )
        self.db.execute(
            "INSERT INTO runs (id,project_id,conversation_id,status,created_at,updated_at) "
            "VALUES ('r2','p2','c2','completed','now','now')"
        )
        self.db.execute(
            "INSERT INTO agent_session_items(session_id,seq,item_json,created_at) "
            "VALUES ('c2:general-agent:model-1',1,'{}','now')"
        )
        self.db.execute(
            "INSERT INTO token_usage(project_id,total_tokens,created_at) "
            "VALUES ('p1',123,'now')"
        )
        self.db.commit()
        app = Flask(__name__)
        app.register_blueprint(workspace_bp)
        with patch("routes.workspace.get_db", return_value=self.db):
            response = app.test_client().post(
                "/api/workspace/context/reset", json={"project_id": "p1"}
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.db.execute("SELECT status FROM runs WHERE id='r1'").fetchone()[0],
            "running",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM agent_session_items WHERE session_id='c1:general-agent:model-1'"
            ).fetchone()[0],
            1,
        )

        self.db.execute("UPDATE runs SET status='waiting' WHERE id='r1'")
        self.db.commit()
        with patch("routes.workspace.get_db", return_value=self.db):
            waiting_response = app.test_client().post(
                "/api/workspace/context/reset", json={"project_id": "p1"}
            )
        self.assertEqual(waiting_response.status_code, 409)
        self.assertEqual(
            self.db.execute("SELECT status FROM runs WHERE id='r1'").fetchone()[0],
            "waiting",
        )

        self.db.execute("UPDATE runs SET status='completed' WHERE id='r1'")
        self.db.commit()
        with patch("routes.workspace.get_db", return_value=self.db):
            response = app.test_client().post(
                "/api/workspace/context/reset", json={"project_id": "p1"}
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM runs WHERE project_id='p1'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM agent_session_items WHERE session_id LIKE 'c1:%'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM runs WHERE project_id='p2'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM agent_session_items WHERE session_id LIKE 'c2:%'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT total_tokens FROM token_usage WHERE project_id='p1'"
            ).fetchone()[0],
            123,
        )


if __name__ == "__main__":
    unittest.main()
