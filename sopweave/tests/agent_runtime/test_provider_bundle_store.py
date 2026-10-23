from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from storage.db import _ensure_model_schema
from storage.model_store import ModelStore


class FailingModelStore(ModelStore):
    def _insert_model(self, provider: dict, data: dict) -> str:
        raise RuntimeError("injected model insert failure")


class ProviderBundleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "storage" / "schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        _ensure_model_schema(self.db)
        self.store = ModelStore(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_bundle_creates_provider_and_first_model_in_one_transaction(self) -> None:
        provider, model = self.store.create_provider_bundle(
            _provider(), _model(status="available")
        )

        self.assertEqual(model["provider_id"], provider["id"])
        self.assertEqual(provider["status"], "available")
        self.assertEqual(model["status"], "available")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM model_providers").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM models").fetchone()[0], 1)

    def test_unverified_bundle_is_saved_but_not_available_for_runtime(self) -> None:
        _, model = self.store.create_provider_bundle(
            _provider(status="unverified"), _model(status="unverified")
        )

        self.assertEqual(model["status"], "unverified")
        self.assertEqual(self.store.list_available_models(chat_only=True), [])

    def test_duplicate_provider_name_rolls_back_the_entire_bundle(self) -> None:
        self.store.create_provider_bundle(_provider(), _model())

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_provider_bundle(_provider(), _model(name="second"))

        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM model_providers").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM models").fetchone()[0], 1)

    def test_model_insert_failure_rolls_back_provider(self) -> None:
        store = FailingModelStore(self.db)

        with self.assertRaisesRegex(RuntimeError, "injected"):
            store.create_provider_bundle(_provider(), _model())

        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM model_providers").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM models").fetchone()[0], 0)

    def test_public_provider_redacts_secret_and_reports_presence(self) -> None:
        provider, _ = self.store.create_provider_bundle(_provider(), _model())

        public = self.store.public_provider(provider)

        self.assertNotIn("api_key", public)
        self.assertTrue(public["has_api_key"])
        self.assertNotIn("top-secret", repr(public))

    def test_secret_update_retains_replaces_and_explicitly_clears(self) -> None:
        provider, _ = self.store.create_provider_bundle(_provider(), _model())

        retained = self.store.update_provider_secret(provider["id"], None, False)
        replaced = self.store.update_provider_secret(provider["id"], "replacement", False)
        cleared = self.store.update_provider_secret(provider["id"], None, True)

        self.assertEqual(retained["api_key"], "top-secret")
        self.assertEqual(replaced["api_key"], "replacement")
        self.assertEqual(cleared["api_key"], "")
        child = self.store.get_model(cleared["models"][0]["id"])
        self.assertEqual(child["api_key"], "")

    def test_secret_update_rejects_replace_and_clear_together(self) -> None:
        provider, _ = self.store.create_provider_bundle(_provider(), _model())

        with self.assertRaisesRegex(ValueError, "同时"):
            self.store.update_provider_secret(provider["id"], "replacement", True)

    def test_bundle_rejects_unknown_status_without_writing(self) -> None:
        for provider_status, model_status in (("broken", "available"), ("available", "broken")):
            with self.subTest(provider_status=provider_status, model_status=model_status):
                with self.assertRaisesRegex(ValueError, "状态"):
                    self.store.create_provider_bundle(
                        _provider(status=provider_status),
                        _model(status=model_status),
                    )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM model_providers").fetchone()[0], 0)


def _provider(*, status: str = "available") -> dict:
    return {
        "name": "DeepSeek 官方",
        "api_url": "https://api.deepseek.com",
        "api_key": "top-secret",
        "provider_kind": "deepseek",
        "api_mode": "chat_completions",
        "options_json": {"thinking_style": "thinking_object"},
        "status": status,
    }


def _model(*, name: str = "deepseek-chat", status: str = "available") -> dict:
    return {
        "name": name,
        "api_model": name,
        "type": "chat",
        "capabilities_json": {"chat": True, "stream": True, "tools": True},
        "status": status,
    }


if __name__ == "__main__":
    unittest.main()
