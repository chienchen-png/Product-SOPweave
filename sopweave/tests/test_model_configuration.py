from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from agent_runtime.models.factory import resolve_model_config
from storage.db import _ensure_model_schema
from storage.model_store import ModelStore


class ModelConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        _ensure_model_schema(self.db)
        self.store = ModelStore(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def add_provider(self, name: str, *, status: str = "available") -> dict:
        return self.store.create_provider({
            "name": name,
            "api_url": f"https://{name.lower()}.example/v1",
            "api_key": f"key-{name}",
            "status": status,
        })

    def add_model(self, provider_id: str, name: str, *, default: bool = False,
                  status: str = "available", model_type: str = "chat") -> dict:
        model = self.store.create_model(provider_id, {
            "name": name,
            "type": model_type,
            "is_default": default,
            "status": status,
            "context_window": 8192,
        })
        assert model is not None
        return model

    def add_project(self, project_id: str, model_id: str) -> None:
        self.db.execute(
            """INSERT INTO projects
               (id, name, work_dir, default_model, work_mode, created_at, updated_at)
               VALUES (?, ?, '.', ?, 'manual', 'now', 'now')""",
            (project_id, project_id, model_id),
        )
        self.db.commit()

    def project_model(self, project_id: str) -> str | None:
        row = self.db.execute(
            "SELECT default_model FROM projects WHERE id = ?", (project_id,),
        ).fetchone()
        return row["default_model"]

    def test_delete_model_requires_visible_and_hidden_projects_to_reselect(self) -> None:
        provider = self.add_provider("Primary")
        old = self.add_model(provider["id"], "old-model", default=True)
        replacement = self.add_model(provider["id"], "replacement-model")
        self.add_project("visible", old["id"])
        self.add_project("__app_recon__", old["id"])

        self.assertTrue(self.store.delete_model(old["id"]))

        self.assertEqual(self.project_model("visible"), old["id"])
        self.assertEqual(self.project_model("__app_recon__"), old["id"])
        self.assertEqual(self.store.get_model(replacement["id"])["is_default"], 1)
        with self.assertRaises(ValueError):
            self.store.resolve_runtime_model(self.project_model("visible"))

    def test_delete_provider_does_not_silently_switch_child_model_references(self) -> None:
        removed_provider = self.add_provider("Removed")
        kept_provider = self.add_provider("Kept")
        removed_default = self.add_model(removed_provider["id"], "removed-default", default=True)
        removed_other = self.add_model(removed_provider["id"], "removed-other")
        replacement = self.add_model(kept_provider["id"], "kept-model")
        self.add_project("p1", removed_default["id"])
        self.add_project("__app_finmod__", removed_other["id"])

        self.assertTrue(self.store.delete_provider(removed_provider["id"]))

        self.assertEqual(self.project_model("p1"), removed_default["id"])
        self.assertEqual(self.project_model("__app_finmod__"), removed_other["id"])
        self.assertEqual(self.store.get_model(replacement["id"])["is_default"], 1)

    def test_delete_last_model_preserves_the_explicit_choice_for_reselection(self) -> None:
        provider = self.add_provider("Only")
        model = self.add_model(provider["id"], "only-model", default=True)
        self.add_project("p1", model["id"])

        self.assertTrue(self.store.delete_model(model["id"]))

        self.assertEqual(self.project_model("p1"), model["id"])

    def test_available_models_exclude_disabled_provider_and_non_chat_models(self) -> None:
        enabled = self.add_provider("Enabled")
        disabled = self.add_provider("Disabled", status="disabled")
        visible = self.add_model(enabled["id"], "visible")
        self.add_model(enabled["id"], "embedding", model_type="embedding")
        self.add_model(disabled["id"], "hidden-by-provider")
        self.add_model(enabled["id"], "unverified", status="unverified")

        models = self.store.list_available_models(chat_only=True)

        self.assertEqual([model["id"] for model in models], [visible["id"]])

    def test_repair_promotes_an_available_default_when_old_default_is_disabled(self) -> None:
        old_provider = self.add_provider("Old")
        new_provider = self.add_provider("New")
        old_default = self.add_model(old_provider["id"], "old-default", default=True)
        replacement = self.add_model(new_provider["id"], "new-default")
        self.store.update_provider(old_provider["id"], {
            "name": old_provider["name"],
            "api_url": old_provider["api_url"],
            "api_key": old_provider["api_key"],
            "status": "disabled",
        })

        self.assertEqual(self.store.get_model(old_default["id"])["is_default"], 0)
        self.assertEqual(self.store.get_model(replacement["id"])["is_default"], 1)

    def test_first_available_chat_model_is_promoted_to_default(self) -> None:
        provider = self.add_provider("First")
        model = self.add_model(provider["id"], "first-model")

        self.assertEqual(self.store.get_model(model["id"])["is_default"], 1)

    def test_explicit_invalid_model_reference_is_rejected(self) -> None:
        provider = self.add_provider("Fallback")
        self.add_model(provider["id"], "fallback-model", default=True)

        with self.assertRaises(ValueError):
            self.store.resolve_runtime_model("deleted-model-id")

    def test_provider_api_update_is_used_by_the_next_runtime_client(self) -> None:
        provider = self.add_provider("Mutable")
        model = self.add_model(provider["id"], "mutable-model")
        self.store.update_provider(provider["id"], {
            "name": "Renamed Provider",
            "api_url": "https://new-endpoint.example/v1",
            "api_key": "new-key",
            "status": "available",
        })

        with self.assertRaises(ValueError):
            self.store.resolve_runtime_model(model['id'])
        self.store.apply_model_verification(model['id'], True)
        config = resolve_model_config(self.store.resolve_runtime_model(model["id"]))

        self.assertEqual(config.base_url, "https://new-endpoint.example/v1/")
        self.assertEqual(config.api_key.get_secret_value(), "new-key")
        self.assertEqual(config.api_model, "mutable-model")

    def test_empty_model_reference_uses_default(self) -> None:
        provider = self.add_provider("Default")
        self.add_model(provider["id"], "default-model", default=True)

        config = resolve_model_config(self.store.resolve_runtime_model(""))

        self.assertEqual(config.api_model, "default-model")

    def test_legacy_model_name_still_resolves(self) -> None:
        provider = self.add_provider("Legacy")
        self.add_model(provider["id"], "legacy-name")

        config = resolve_model_config(self.store.resolve_runtime_model("legacy-name"))

        self.assertEqual(config.api_model, "legacy-name")

    def test_model_api_identifier_survives_display_name_edit(self) -> None:
        provider = self.add_provider("StableApiModel")
        model = self.store.create_model(provider["id"], {
            "name": "Initial label",
            "api_model": "vendor-model-v1",
            "type": "chat",
            "status": "available",
        })
        assert model is not None

        updated = self.store.update_model(model["id"], {
            "name": "Renamed label",
            "type": "chat",
            "status": "available",
        })

        self.assertEqual(updated["api_model"], "vendor-model-v1")

    def test_repair_does_not_replace_a_missing_explicit_model(self) -> None:
        provider = self.add_provider("Repair")
        replacement = self.add_model(provider["id"], "repair-model", default=True)
        self.add_project("p1", "already-deleted")

        repaired = self.store.repair_project_model_references()

        self.assertEqual(repaired, 0)
        self.assertEqual(self.project_model("p1"), "already-deleted")

    def test_repair_normalizes_resolvable_legacy_names_only(self) -> None:
        provider = self.add_provider("LegacyChoice")
        model = self.add_model(provider["id"], "legacy-choice")
        self.add_project("p1", "legacy-choice")
        self.assertEqual(self.store.repair_project_model_references(), 1)
        self.assertEqual(self.project_model("p1"), model["id"])

    def test_disabled_provider_never_changes_an_explicit_project_choice(self) -> None:
        old_provider = self.add_provider("DisabledChoice")
        other_provider = self.add_provider("Alternative")
        old = self.add_model(old_provider["id"], "chosen", default=True)
        self.add_model(other_provider["id"], "other")
        self.add_project("p1", old["id"])
        self.store.update_provider(old_provider["id"], {
            "name": old_provider["name"],
            "api_url": old_provider["api_url"],
            "api_key": old_provider["api_key"],
            "status": "disabled",
        })
        self.assertEqual(self.project_model("p1"), old["id"])
        with self.assertRaises(ValueError):
            self.store.resolve_runtime_model(self.project_model("p1"))


if __name__ == "__main__":
    unittest.main()
