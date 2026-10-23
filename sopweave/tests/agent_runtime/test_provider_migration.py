from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from storage.provider_migration import migrate_provider_configuration


class ProviderMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "storage" / "schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.db.close()

    def test_deepseek_official_row_is_normalized_without_changing_secret_or_models(self) -> None:
        self._provider(
            "provider_deepseek",
            "Old Deepseek",
            "https://api.deepseek.com/v1/chat/completions",
            "openai_compatible",
            api_key="keep-secret",
        )
        self._model("deepseek-model", "provider_deepseek", "deepseek-special")

        migrate_provider_configuration(self.db)

        provider = self._provider_row("provider_deepseek")
        model = self._model_row("deepseek-model")
        self.assertEqual(provider["name"], "DeepSeek 官方")
        self.assertEqual(provider["api_url"], "https://api.deepseek.com")
        self.assertEqual(provider["provider_kind"], "deepseek")
        self.assertEqual(provider["api_key"], "keep-secret")
        self.assertEqual(model["api_model"], "deepseek-special")
        self.assertEqual(model["provider_id"], "provider_deepseek")

    def test_unreferenced_legacy_presets_and_dashscope_are_removed(self) -> None:
        self._provider("provider_tianjian_local", "Tianjian", "http://old.local/v1", "openai_compatible")
        self._model("unused-tianjian", "provider_tianjian_local", "old-model")
        self._provider("provider_chat_api", "Chat Api", "https://old.example/v1", "openai_compatible")
        self._model("unused-chat", "provider_chat_api", "old-chat")
        self._provider("dash-unused", "DashScope", "https://dashscope.example/v1", "dashscope")
        self._model("unused-dash", "dash-unused", "qwen")

        migrate_provider_configuration(self.db)

        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM model_providers").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM models").fetchone()[0], 0)

    def test_every_reference_class_preserves_and_converts_legacy_provider(self) -> None:
        cases = [
            ("provider_tianjian_local", "visible-model", "visible_project"),
            ("provider_chat_api", "hidden-model", "hidden_project"),
            ("dash-global", "global-model", "global_default"),
            ("dash-run", "run-model", "run_history"),
        ]
        for provider_id, model_id, reference in cases:
            kind = "dashscope" if provider_id.startswith("dash-") else "openai_compatible"
            self._provider(provider_id, provider_id, f"https://{provider_id}.example/v1", kind)
            self._model(model_id, provider_id, f"api-{model_id}", is_default=reference == "global_default")
            if reference in {"visible_project", "hidden_project"}:
                project_id = "project-visible" if reference == "visible_project" else "__app_recon__"
                self.db.execute(
                    """INSERT INTO projects
                       (id, name, work_dir, default_model, created_at, updated_at)
                       VALUES (?, ?, '.', ?, 'now', 'now')""",
                    (project_id, project_id, model_id),
                )
            elif reference == "run_history":
                self.db.execute(
                    """INSERT INTO runs
                       (id, model_id, status, created_at, updated_at)
                       VALUES ('historical-run', ?, 'completed', 'now', 'now')""",
                    (model_id,),
                )

        migrate_provider_configuration(self.db)

        for provider_id, model_id, _ in cases:
            with self.subTest(provider_id=provider_id):
                provider = self._provider_row(provider_id)
                self.assertIsNotNone(provider)
                self.assertEqual(provider["provider_kind"], "openai_compatible")
                self.assertEqual(provider["is_preset"], 0)
                self.assertEqual(self._model_row(model_id)["api_model"], f"api-{model_id}")
                options = json.loads(provider["options_json"] or "{}")
                expected_style = "enable_thinking" if provider_id.startswith("dash-") else "none"
                self.assertEqual(options["thinking_style"], expected_style)

    def test_migration_is_idempotent(self) -> None:
        self._provider("provider_chat_api", "Chat Api", "https://old.example/v1", "openai_compatible")
        self._model("history-model", "provider_chat_api", "history-api")
        self.db.execute(
            """INSERT INTO runs (id, model_id, status, created_at, updated_at)
               VALUES ('run-1', 'history-model', 'completed', 'now', 'now')"""
        )

        migrate_provider_configuration(self.db)
        first = self._snapshot()
        migrate_provider_configuration(self.db)

        self.assertEqual(self._snapshot(), first)

    def _provider(
        self,
        provider_id: str,
        name: str,
        api_url: str,
        provider_kind: str,
        *,
        api_key: str = "legacy-secret",
    ) -> None:
        self.db.execute(
            """INSERT INTO model_providers
               (id, name, api_url, api_key, provider_kind, api_mode, options_json,
                is_preset, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'chat_completions', '{}', 1, 'available', 'now', 'now')""",
            (provider_id, name, api_url, api_key, provider_kind),
        )

    def _model(
        self,
        model_id: str,
        provider_id: str,
        api_model: str,
        *,
        is_default: bool = False,
    ) -> None:
        provider = self._provider_row(provider_id)
        self.db.execute(
            """INSERT INTO models
               (id, provider_id, name, api_url, api_key, type, api_model,
                capabilities_json, request_options_json, is_default, status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'chat', ?, '{}', '{}', ?, 'available', 'now', 'now')""",
            (
                model_id,
                provider_id,
                api_model,
                provider["api_url"],
                provider["api_key"],
                api_model,
                int(is_default),
            ),
        )

    def _provider_row(self, provider_id: str):
        return self.db.execute(
            "SELECT * FROM model_providers WHERE id = ?", (provider_id,)
        ).fetchone()

    def _model_row(self, model_id: str):
        return self.db.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()

    def _snapshot(self) -> tuple[list[tuple], list[tuple]]:
        providers = [tuple(row) for row in self.db.execute(
            "SELECT * FROM model_providers ORDER BY id"
        ).fetchall()]
        models = [tuple(row) for row in self.db.execute(
            "SELECT * FROM models ORDER BY id"
        ).fetchall()]
        return providers, models


if __name__ == "__main__":
    unittest.main()
