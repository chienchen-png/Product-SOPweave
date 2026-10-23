from __future__ import annotations

import json
import inspect
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from flask import Flask

from services import app_models as apps
from routes.models import models_bp
from routes.projects import projects_bp
from storage.db import _ensure_model_schema
from storage.model_store import ModelStore
from tests.agent_runtime.fake_provider import FakeProviderServer


class ModelRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        _ensure_model_schema(self.db)
        self.store = ModelStore(self.db)
        self.provider = self.store.create_provider({
            "name": "Provider",
            "api_url": "https://provider.example/v1",
            "api_key": "secret",
        })
        self.first = self.store.create_model(self.provider["id"], {
            "name": "first-model", "type": "chat", "is_default": True,
        })
        self.second = self.store.create_model(self.provider["id"], {
            "name": "second-model", "type": "chat", "is_default": False,
        })
        self.app = Flask(__name__)
        self.app.register_blueprint(models_bp)
        self.app.register_blueprint(projects_bp)
        self.db_patch = patch("routes.models.get_db", return_value=self.db)
        self.project_db_patch = patch("routes.projects.get_db", return_value=self.db)
        self.db_patch.start()
        self.project_db_patch.start()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.project_db_patch.stop()
        self.db_patch.stop()
        self.db.close()

    def test_default_endpoint_accepts_no_full_model_payload(self) -> None:
        response = self.client.patch(f"/api/models/{self.second['id']}/default", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get_model(self.first["id"])["is_default"], 0)
        self.assertEqual(self.store.get_model(self.second["id"])["is_default"], 1)

    def test_active_run_locks_model_and_provider_but_allows_default_change(self):
        original = self.store.get_model(self.first["id"])
        for state in ("pending", "starting", "running", "waiting", "resuming"):
            with self.subTest(state=state):
                self.db.execute("DELETE FROM runs WHERE id = 'lock-test'")
                self.db.execute(
                    "INSERT INTO runs(id,model_id,status,created_at,updated_at) VALUES (?,?,?,?,?)",
                    ("lock-test", self.first["id"], state, "now", "now"),
                )
                self.db.commit()
                requests = [
                    ("put", f"/api/models/{self.first['id']}", {"name":"renamed", "type":"chat"}),
                    ("delete", f"/api/models/{self.first['id']}", None),
                    ("put", f"/api/models/providers/{self.provider['id']}", {
                        "name":"Provider", "api_url":"https://provider.example/v1", "api_key":"changed",
                    }),
                    ("delete", f"/api/models/providers/{self.provider['id']}", None),
                ]
                for method, url, body in requests:
                    response = getattr(self.client, method)(url, json=body)
                    self.assertEqual(response.status_code, 409, (state, url, response.get_json()))
                    self.assertEqual(response.get_json()["code"], "model_in_use")
                self.assertEqual(self.store.get_model(self.first['id']), original)
                self.assertEqual(self.store.get_provider(self.provider['id'])["api_key"], "secret")
                with self.assertRaisesRegex(ValueError, "运行中或等待"):
                    self.store.update_provider_secret(self.provider['id'], "replacement", False)
                self.assertEqual(self.client.patch(f"/api/models/{self.second['id']}/default").status_code, 200)
                self.store.set_default_model(self.first['id'])

    def test_terminal_run_releases_lock_and_unrelated_model_remains_editable(self):
        for state in ("completed", "failed", "stopped", "cancelled"):
            with self.subTest(state=state):
                self.db.execute("DELETE FROM runs WHERE id = 'lock-test'")
                self.db.execute(
                    "INSERT INTO runs(id,model_id,status,created_at,updated_at) VALUES (?,?,?,?,?)",
                    ("lock-test", self.first['id'], state, "now", "now"),
                )
                self.db.commit()
                response = self.client.put(f"/api/models/{self.first['id']}", json={"name":"first-model", "type":"chat"})
                self.assertEqual(response.status_code, 200)
        self.db.execute("UPDATE runs SET status='waiting' WHERE id='lock-test'")
        self.db.commit()
        response = self.client.put(f"/api/models/{self.second['id']}", json={"name":"second-renamed", "type":"chat"})
        self.assertEqual(response.status_code, 200)

    def test_reverification_cannot_invalidate_a_waiting_runs_model(self):
        self.db.execute(
            "INSERT INTO runs(id,model_id,status,created_at,updated_at) VALUES (?,?,?,?,?)",
            ('verification-lock', self.first['id'], 'waiting', 'now', 'now'),
        )
        self.db.commit()
        with patch('routes.models._run_agent_contract', new_callable=AsyncMock) as probe:
            probe.return_value.to_dict = MagicMock(return_value={'passed':False, 'checks':{}})
            response = self.client.post('/api/models/test', json={
                'mode':'agent_contract', 'model_id':self.first['id'],
            })
        self.assertEqual(response.status_code, 409)
        probe.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, '运行中或等待'):
            self.store.apply_model_verification(self.first['id'], False)

    def test_update_model_response_does_not_expose_saved_credentials(self):
        response = self.client.put(f"/api/models/{self.first['id']}", json={"name":"display-name", "type":"chat"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("api_key", response.get_json()["data"])

    def test_stale_compatibility_result_does_not_validate_changed_configuration(self):
        async def probe(_record):
            self.store.update_provider(self.provider['id'], {
                'name':'Provider', 'api_url':'https://changed.example/v1',
            })
            self.store.invalidate_provider(self.provider['id'])
            result = MagicMock()
            result.to_dict.return_value = {'passed':True, 'checks':{}}
            return result
        with patch('routes.models._run_agent_contract',side_effect=probe):
            response = self.client.post('/api/models/test',json={
                'mode':'agent_contract','model_id':self.first['id'],
            })
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.get_json()['code'],'model_configuration_changed')
        self.assertEqual(self.store.get_model(self.first['id'])['status'],'unverified')
        self.assertEqual(self.store.list_capability_checks(self.first['id']),[])

    def test_provider_connection_changes_invalidate_before_store_commit(self):
        updated = self.store.update_provider(self.provider['id'],{
            'name':'Provider','api_url':'https://changed.example/v1','api_key':'replacement',
        })
        self.assertEqual(updated['status'],'unverified')
        self.assertTrue(all(m['status']=='unverified' for m in updated['models']))
        self.assertFalse(self.store.get_available_model(self.first['id']))

    def test_connection_test_forwards_the_real_model_name(self) -> None:
        response_payload = MagicMock()
        response_payload.model = "real-model"
        response_payload.usage = MagicMock(model_dump=lambda: {})
        direct = MagicMock()
        direct.complete = AsyncMock(return_value=response_payload)
        direct.aclose = AsyncMock()
        with patch(
            "routes.models.ModelRuntimeFactory.build_direct_client",
            return_value=direct,
        ) as build_direct:
            response = self.client.post("/api/models/test", json={
                "api_url": "https://provider.example/v1",
                "api_key": "secret",
                "model": "real-model",
                "provider_kind": "openai_compatible",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(direct.complete.await_args.kwargs["max_tokens"], 8)
        self.assertEqual(build_direct.call_args.args[0].api_model, "real-model")
        source = inspect.getsource(__import__("routes.models", fromlist=["test_connection"]))
        self.assertNotIn("urllib", source)
        self.assertNotIn("_build_chat_url", source)

    def test_agent_contract_mode_runs_five_checks_and_persists_them(self) -> None:
        with FakeProviderServer() as server:
            self.db.execute(
                "UPDATE model_providers SET api_url = ? WHERE id = ?",
                (server.base_url, self.provider["id"]),
            )
            self.db.execute(
                "UPDATE models SET api_model = ? WHERE id = ?",
                ("contract-model", self.first["id"]),
            )
            self.db.commit()

            response = self.client.post("/api/models/test", json={
                "mode": "agent_contract",
                "model_id": self.first["id"],
            })

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["data"]
        self.assertTrue(result["passed"], result)
        self.assertEqual(set(result["checks"]), {
            "text", "stream", "tools", "tool_resume", "usage",
        })
        self.assertEqual(len(self.store.list_capability_checks(self.first["id"])), 5)

    def test_project_update_rejects_an_invalid_model_reference(self) -> None:
        self.db.execute(
            """INSERT INTO projects
               (id, name, work_dir, work_mode, created_at, updated_at)
               VALUES ('p1', 'Project', '.', 'manual', 'now', 'now')"""
        )
        self.db.commit()

        response = self.client.put("/api/projects/p1", json={"default_model": "deleted"})

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.db.execute(
            "SELECT default_model FROM projects WHERE id = 'p1'"
        ).fetchone()["default_model"])

    def test_integrated_apps_use_the_same_strict_model_resolver(self) -> None:
        with self.app.app_context(), patch("services.app_models.get_db", return_value=self.db):
            client = apps._build_llm_client(self.second["id"])
            self.assertIsNotNone(client)
            self.assertEqual(client.model, "second-model")
            with self.assertRaises(ValueError):
                apps._build_llm_client("deleted-model")

    def test_configuration_payloads_preserve_vendor_runtime_fields(self) -> None:
        provider, provider_error = __import__(
            "routes.models", fromlist=["_provider_payload"]
        )._provider_payload({
            "name": "DeepSeek",
            "api_url": "https://api.deepseek.com",
            "provider_kind": "deepseek",
            "api_mode": "chat_completions",
            "options_json": {"timeout": 60},
        })
        model, model_error = __import__(
            "routes.models", fromlist=["_model_payload"]
        )._model_payload({
            "name": "显示名称",
            "api_model": "deepseek-chat",
            "capabilities_json": {"tools": True},
            "request_options_json": {"temperature": 0.2},
        })

        self.assertIsNone(provider_error)
        self.assertEqual(provider["provider_kind"], "deepseek")
        self.assertEqual(provider["options_json"], {"timeout": 60})
        self.assertIsNone(model_error)
        self.assertEqual(model["api_model"], "deepseek-chat")
        self.assertEqual(model["capabilities_json"], {"tools": True})
        self.assertEqual(model["request_options_json"], {"temperature": 0.2})

        invalid, message = __import__(
            "routes.models", fromlist=["_provider_payload"]
        )._provider_payload({
            "name": "Unknown",
            "api_url": "https://unknown.example/v1",
            "provider_kind": "unknown",
        })
        self.assertIsNone(invalid)
        self.assertEqual(message, "供应商类型无效")


if __name__ == "__main__":
    unittest.main()
