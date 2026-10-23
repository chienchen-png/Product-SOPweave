from __future__ import annotations

import json
import sqlite3
import unittest

from storage.db import _ensure_model_schema
from storage.model_store import ModelStore


def _legacy_database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE model_providers (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, api_url TEXT NOT NULL,
            api_key TEXT, api_key_url TEXT, description TEXT, is_preset INTEGER DEFAULT 0,
            status TEXT DEFAULT 'available', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE models (
            id TEXT PRIMARY KEY, provider_id TEXT, name TEXT NOT NULL, api_url TEXT NOT NULL,
            api_key TEXT, type TEXT NOT NULL, capabilities TEXT,
            context_window INTEGER DEFAULT 8192, is_default INTEGER DEFAULT 0,
            status TEXT DEFAULT 'unknown', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE deleted_presets (id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL);
        CREATE TABLE token_usage (id INTEGER PRIMARY KEY AUTOINCREMENT);
        CREATE TABLE project_data_changes (id INTEGER PRIMARY KEY AUTOINCREMENT);
        CREATE TABLE runs (id TEXT PRIMARY KEY, model_id TEXT, status TEXT);
        CREATE TABLE projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, work_dir TEXT NOT NULL,
            default_model TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO model_providers
            (id, name, api_url, api_key, status, created_at, updated_at)
            VALUES ('provider-1', 'Legacy', 'https://legacy.example/v1', 'secret',
                    'available', 'now', 'now');
        INSERT INTO models
            (id, provider_id, name, api_url, api_key, type, capabilities,
             status, created_at, updated_at)
            VALUES ('model-1', 'provider-1', 'legacy-api-model',
                    'https://legacy.example/v1', 'secret', 'chat', '对话,工具,视觉',
                    'available', 'now', 'now');
        """
    )
    return db


class ModelRuntimeSchemaTests(unittest.TestCase):
    def test_legacy_rows_migrate_idempotently_with_structured_defaults(self) -> None:
        db = _legacy_database()
        self.addCleanup(db.close)

        _ensure_model_schema(db)
        _ensure_model_schema(db)

        provider = dict(db.execute(
            "SELECT * FROM model_providers WHERE id='provider-1'"
        ).fetchone())
        model = dict(db.execute("SELECT * FROM models WHERE id='model-1'").fetchone())
        self.assertEqual(provider["provider_kind"], "openai_compatible")
        self.assertEqual(provider["api_mode"], "chat_completions")
        self.assertEqual(json.loads(provider["options_json"]), {})
        self.assertEqual(model["api_model"], "legacy-api-model")
        self.assertEqual(
            json.loads(model["capabilities_json"]),
            {"chat": True, "tools": True, "vision": True},
        )
        self.assertEqual(json.loads(model["request_options_json"]), {})

    def test_display_name_update_does_not_change_api_model(self) -> None:
        db = _legacy_database()
        self.addCleanup(db.close)
        _ensure_model_schema(db)
        store = ModelStore(db)

        updated = store.update_model("model-1", {
            "name": "财务分析模型",
            "type": "chat",
            "capabilities": "对话,工具",
            "context_window": 128000,
            "status": "available",
        })

        self.assertIsNotNone(updated)
        self.assertEqual(updated["name"], "财务分析模型")
        self.assertEqual(updated["api_model"], "legacy-api-model")

    def test_provider_kind_is_validated_and_runtime_record_is_normalized(self) -> None:
        db = _legacy_database()
        self.addCleanup(db.close)
        _ensure_model_schema(db)
        store = ModelStore(db)

        with self.assertRaises(ValueError):
            store.create_provider({
                "name": "Bad",
                "api_url": "https://bad.example/v1",
                "provider_kind": "unknown-vendor",
            })

        provider = store.create_provider({
            "name": "DeepSeek",
            "api_url": "https://api.deepseek.com",
            "provider_kind": "deepseek",
            "api_mode": "chat_completions",
            "options_json": {"timeout": 90},
        })
        model = store.create_model(provider["id"], {
            "name": "显示名称",
            "api_model": "deepseek-chat",
            "type": "chat",
            "capabilities_json": {"chat": True, "stream": True, "tools": True},
            "request_options_json": {"temperature": 0.1},
            "status": "available",
        })

        resolved = store.resolve_runtime_model(model["id"])
        self.assertEqual(resolved["provider_kind"], "deepseek")
        self.assertEqual(resolved["api_mode"], "chat_completions")
        self.assertEqual(resolved["api_model"], "deepseek-chat")
        self.assertEqual(resolved["capabilities_json"]["tools"], True)
        self.assertEqual(resolved["request_options_json"], {"temperature": 0.1})
        self.assertEqual(resolved["provider_options_json"], {"timeout": 90})


if __name__ == "__main__":
    unittest.main()
