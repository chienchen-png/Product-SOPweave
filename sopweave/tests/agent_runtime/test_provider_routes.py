from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from routes.models import models_bp
from storage.db import _ensure_model_schema
from storage.model_store import ModelStore
from tests.agent_runtime.fake_provider import FakeProviderServer


class ProviderRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "storage" / "schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        _ensure_model_schema(self.db)
        self.store = ModelStore(self.db)
        self.app = Flask(__name__)
        self.app.register_blueprint(models_bp)
        self.db_patch = patch("routes.models.get_db", return_value=self.db)
        self.db_patch.start()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.db.close()

    def test_template_route_returns_exactly_four_supported_choices(self) -> None:
        response = self.client.get("/api/models/provider-templates")

        self.assertEqual(response.status_code, 200)
        templates = response.get_json()["data"]
        self.assertEqual([item["template_id"] for item in templates], [
            "deepseek", "zhipu", "siliconflow", "custom",
        ])
        self.assertNotIn("dashscope", repr(templates))
        self.assertNotIn("responses", repr(templates))

    def test_official_template_rejects_locked_field_override(self) -> None:
        response = self.client.post("/api/models/provider-bundles", json={
            "template_id": "deepseek",
            "api_key": "secret",
            "api_model": "deepseek-chat",
            "base_url": "https://proxy.example/v1",
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "official_field_locked")
        self.assertEqual(self._count("model_providers"), 0)

    def test_custom_bundle_rejects_missing_and_unsupported_fields(self) -> None:
        valid = _custom_payload("https://proxy.example/v1")
        cases = [
            ({**valid, "provider_name": ""}, "provider_name_required"),
            ({**valid, "base_url": "ftp://proxy.example/v1"}, "invalid_base_url"),
            ({**valid, "api_mode": "responses"}, "unsupported_api_mode"),
            ({**valid, "auth_mode": "basic"}, "unsupported_auth_mode"),
            ({**valid, "thinking_style": "raw_json"}, "unsupported_thinking_style"),
        ]

        for body, code in cases:
            with self.subTest(code=code):
                response = self.client.post("/api/models/provider-bundles", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["code"], code)
        self.assertEqual(self._count("model_providers"), 0)

    def test_passing_contract_creates_available_atomic_bundle(self) -> None:
        with FakeProviderServer() as server:
            response = self.client.post(
                "/api/models/provider-bundles",
                json=_custom_payload(server.base_url),
            )

        self.assertEqual(response.status_code, 201)
        result = response.get_json()["data"]
        self.assertTrue(result["verification"]["passed"], result["verification"])
        self.assertEqual(result["provider"]["status"], "available")
        self.assertEqual(result["model"]["status"], "available")
        self.assertNotIn("api_key", result["provider"])
        self.assertNotIn("api_key", result["model"])
        self.assertEqual(self._count("model_providers"), 1)
        self.assertEqual(self._count("models"), 1)

    def test_failed_contract_saves_unverified_and_excludes_picker(self) -> None:
        with FakeProviderServer() as server:
            body = _custom_payload(server.base_url) | {"api_model": "error-401"}
            response = self.client.post("/api/models/provider-bundles", json=body)

        self.assertEqual(response.status_code, 201)
        result = response.get_json()["data"]
        self.assertFalse(result["verification"]["passed"])
        self.assertEqual(result["model"]["status"], "unverified")
        self.assertEqual(self.store.list_available_models(chat_only=True), [])

    def test_duplicate_name_returns_409_without_half_bundle(self) -> None:
        with FakeProviderServer() as server:
            body = _custom_payload(server.base_url)
            first = self.client.post("/api/models/provider-bundles", json=body)
            second = self.client.post("/api/models/provider-bundles", json=body)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["code"], "provider_name_conflict")
        self.assertEqual(self._count("model_providers"), 1)
        self.assertEqual(self._count("models"), 1)

    def test_provider_list_redacts_provider_and_child_model_secrets(self) -> None:
        provider, _ = self.store.create_provider_bundle(
            {
                "name": "Secret Provider",
                "api_url": "https://proxy.example/v1",
                "api_key": "never-return-this",
                "status": "unverified",
            },
            {"name": "model", "api_model": "model", "status": "unverified"},
        )

        response = self.client.get("/api/models/providers")

        serialized = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("never-return-this", serialized)
        public = response.get_json()["data"][0]
        self.assertTrue(public["has_api_key"])
        self.assertNotIn("api_key", public)
        self.assertNotIn("api_key", public["models"][0])
        self.assertEqual(public["id"], provider["id"])

    def test_update_retains_replaces_and_explicitly_clears_secret(self) -> None:
        provider = self.store.create_provider({
            "name": "Mutable",
            "api_url": "https://proxy.example/v1",
            "api_key": "original",
        })
        base = {
            "name": "Mutable",
            "api_url": "https://proxy.example/v1",
            "provider_kind": "openai_compatible",
            "api_mode": "chat_completions",
        }

        retained = self.client.put(f"/api/models/providers/{provider['id']}", json=base)
        self.assertTrue(retained.get_json()["data"]["has_api_key"])
        self.assertEqual(self.store.get_provider(provider["id"])["api_key"], "original")

        replaced = self.client.put(
            f"/api/models/providers/{provider['id']}",
            json={**base, "api_key": "replacement"},
        )
        self.assertTrue(replaced.get_json()["data"]["has_api_key"])
        self.assertEqual(self.store.get_provider(provider["id"])["api_key"], "replacement")

        cleared = self.client.put(
            f"/api/models/providers/{provider['id']}",
            json={**base, "clear_api_key": True},
        )
        self.assertFalse(cleared.get_json()["data"]["has_api_key"])
        self.assertEqual(self.store.get_provider(provider["id"])["api_key"], "")

    def test_legacy_provider_create_cannot_bypass_official_templates(self) -> None:
        response = self.client.post("/api/models/providers", json={
            "name": "Fake DeepSeek",
            "api_url": "https://proxy.example/v1",
            "provider_kind": "deepseek",
            "api_mode": "chat_completions",
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "use_provider_bundle")

    def test_official_provider_edit_rejects_locked_name_or_url(self) -> None:
        provider = self.store.create_provider({
            "name": "DeepSeek 官方",
            "api_url": "https://api.deepseek.com",
            "api_key": "secret",
            "provider_kind": "deepseek",
        })

        response = self.client.put(f"/api/models/providers/{provider['id']}", json={
            "name": "Proxy",
            "api_url": "https://proxy.example/v1",
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "official_field_locked")

    def test_official_provider_timeout_is_editable_but_protocol_style_is_locked(self) -> None:
        provider = self.store.create_provider({
            "name": "SiliconFlow",
            "api_url": "https://api.siliconflow.cn/v1",
            "api_key": "secret",
            "provider_kind": "siliconflow",
            "options_json": {
                "thinking_style": "enable_thinking",
                "timeout": 30,
                "max_retries": 0,
            },
            "status": "available",
        })
        updated = self.client.put(
            f"/api/models/providers/{provider['id']}",
            json={
                "name": provider["name"],
                "api_url": provider["api_url"],
                "options_json": {
                    "thinking_style": "enable_thinking",
                    "timeout": 120,
                    "max_retries": 1,
                },
            },
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertEqual(updated.get_json()["data"]["options_json"]["timeout"], 120)
        self.assertEqual(updated.get_json()["data"]["options_json"]["max_retries"], 1)
        self.assertEqual(updated.get_json()["data"]["status"], "available")

        rejected = self.client.put(
            f"/api/models/providers/{provider['id']}",
            json={
                "name": provider["name"],
                "api_url": provider["api_url"],
                "options_json": {
                    "thinking_style": "none",
                    "timeout": 120,
                    "max_retries": 1,
                },
            },
        )
        self.assertEqual(rejected.status_code, 400, rejected.get_json())
        self.assertEqual(rejected.get_json()["code"], "official_field_locked")

    def test_added_model_runs_contract_and_failed_model_stays_unverified(self) -> None:
        with FakeProviderServer() as server:
            provider = self.store.create_provider({
                "name": "Model Add Provider",
                "api_url": server.base_url,
                "api_key": "secret",
                "status": "unverified",
            })
            passed = self.client.post(
                f"/api/models/providers/{provider['id']}/models",
                json={"name": "Contract", "api_model": "contract-model", "type": "chat"},
            )
            failed = self.client.post(
                f"/api/models/providers/{provider['id']}/models",
                json={"name": "Failed", "api_model": "error-401", "type": "chat"},
            )

        self.assertEqual(passed.status_code, 201)
        self.assertEqual(passed.get_json()["data"]["status"], "available")
        self.assertEqual(failed.status_code, 201)
        self.assertEqual(failed.get_json()["data"]["status"], "unverified")
        self.assertEqual(len(self.store.list_capability_checks(passed.get_json()["data"]["id"])), 5)

    def test_connection_change_invalidates_models_until_reverified(self) -> None:
        provider = self.store.create_provider({
            "name": "Mutable Endpoint",
            "api_url": "https://old.example/v1",
            "api_key": "secret",
        })
        model = self.store.create_model(provider["id"], {
            "name": "Contract", "api_model": "contract-model", "type": "chat",
        })
        with FakeProviderServer() as server:
            updated = self.client.put(f"/api/models/providers/{provider['id']}", json={
                "name": provider["name"],
                "api_url": server.base_url,
            })
            self.assertEqual(updated.get_json()["data"]["status"], "unverified")
            self.assertEqual(self.store.get_model(model["id"])["status"], "unverified")

            verified = self.client.post("/api/models/test", json={
                "mode": "agent_contract",
                "model_id": model["id"],
            })

        self.assertTrue(verified.get_json()["data"]["passed"])
        self.assertEqual(self.store.get_model(model["id"])["status"], "available")
        self.assertEqual(self.store.get_provider(provider["id"])["status"], "available")

    def test_editing_unverified_model_does_not_make_it_available(self) -> None:
        provider = self.store.create_provider({
            "name": "Unverified", "api_url": "https://proxy.example/v1",
            "status": "unverified",
        })
        model = self.store.create_model(provider["id"], {
            "name": "Before", "api_model": "vendor-model", "type": "chat",
            "status": "unverified",
        })

        response = self.client.put(f"/api/models/{model['id']}", json={
            "name": "After", "type": "chat",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "unverified")

    def test_legacy_updates_cannot_promote_unverified_records(self) -> None:
        provider = self.store.create_provider({
            "name": "Still Unverified",
            "api_url": "https://proxy.example/v1",
            "status": "unverified",
        })
        model = self.store.create_model(provider["id"], {
            "name": "Still Unverified Model",
            "api_model": "vendor-model",
            "type": "chat",
            "status": "unverified",
        })

        provider_response = self.client.put(
            f"/api/models/providers/{provider['id']}",
            json={
                "name": provider["name"],
                "api_url": provider["api_url"],
                "status": "available",
            },
        )
        model_response = self.client.put(
            f"/api/models/{model['id']}",
            json={
                "name": model["name"],
                "api_model": model["api_model"],
                "type": "chat",
                "status": "available",
            },
        )

        self.assertEqual(provider_response.status_code, 200)
        self.assertEqual(provider_response.get_json()["data"]["status"], "unverified")
        self.assertEqual(model_response.status_code, 200)
        self.assertEqual(model_response.get_json()["data"]["status"], "unverified")

    def _count(self, table: str) -> int:
        return int(self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _custom_payload(base_url: str) -> dict:
    return {
        "template_id": "custom",
        "provider_name": "Custom Provider",
        "base_url": base_url,
        "api_key": "secret",
        "api_model": "contract-model",
        "api_mode": "chat_completions",
        "auth_mode": "bearer",
        "thinking_style": "none",
        # Success contracts test protocol behavior; timing failures have dedicated
        # 50 ms fixtures. Leave room for busy Windows CI workers here.
        "timeout": 10,
        "max_retries": 0,
    }


if __name__ == "__main__":
    unittest.main()
