from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from services import app_models as apps
from storage.db import _ensure_model_schema
from storage.model_store import ModelStore


class IntegratedAppModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "storage" / "schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        _ensure_model_schema(self.db)
        self.store = ModelStore(self.db)
        self.addCleanup(self.db.close)

    def _add(self, kind: str, api_model: str):
        provider = self.store.create_provider({
            "name": kind,
            "api_url": f"https://{kind}.example",
            "api_key": "secret",
            "provider_kind": kind,
            "api_mode": "chat_completions",
        })
        return self.store.create_model(provider["id"], {
            "name": f"显示名称-{kind}",
            "api_model": api_model,
            "type": "chat",
        })

    def test_three_vendors_use_api_model_and_disable_thinking(self) -> None:
        cases = (
            ("deepseek", "deepseek-chat", "thinking"),
            ("zhipu", "glm-4.5", "thinking"),
            ("siliconflow", "Qwen/Qwen3-8B", "enable_thinking"),
        )
        with patch("services.app_models.get_db", return_value=self.db):
            for kind, api_model, flag in cases:
                with self.subTest(kind=kind):
                    model = self._add(kind, api_model)
                    client = apps._build_llm_client(model["id"])
                    self.assertEqual(client.model, api_model)
                    if flag == "thinking":
                        self.assertEqual(client.request_args["extra_body"][flag]["type"], "disabled")
                    else:
                        self.assertFalse(client.request_args["extra_body"][flag])

    def test_deleted_or_disabled_model_is_rejected_strictly(self) -> None:
        model = self._add("deepseek", "deepseek-chat")
        self.store.update_model(model["id"], {
            "name": model["name"], "api_model": model["api_model"],
            "type": "chat", "status": "disabled",
        })
        with patch("services.app_models.get_db", return_value=self.db):
            with self.assertRaisesRegex(ValueError, "不可用或已删除"):
                apps._build_llm_client(model["id"])
            with self.assertRaisesRegex(ValueError, "不可用或已删除"):
                apps._build_llm_client("deleted-model")

    def test_project_and_hidden_apps_resolve_the_same_stable_model_record(self) -> None:
        model = self._add("siliconflow", "Qwen/Qwen3-8B")
        for project_id in ("visible-project", "__app_recon__", "__app_finmod__"):
            self.db.execute(
                """INSERT INTO projects
                   (id, name, work_dir, default_model, work_mode, created_at, updated_at)
                   VALUES (?, ?, '.', ?, 'manual', 'now', 'now')""",
                (project_id, project_id, model["id"]),
            )
        self.db.commit()

        self.store.update_model(model["id"], {
            "name": "硅基流动 Qwen",
            "type": "chat",
            "status": "available",
        })

        for row in self.db.execute("SELECT default_model FROM projects").fetchall():
            resolved = self.store.resolve_runtime_model(row["default_model"])
            self.assertEqual(resolved["id"], model["id"])
            self.assertEqual(resolved["api_model"], "Qwen/Qwen3-8B")


if __name__ == "__main__":
    unittest.main()
