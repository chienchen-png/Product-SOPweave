from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from services.skills.contracts import SkillConflict, SkillSpec
from storage.skill_store import SkillStore


class SkillStoreTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.db.close)
        self.schema = Path("storage/schema.sql").read_text(encoding="utf-8")
        self.db.executescript(self.schema)
        self.store = SkillStore(self.db)

    def test_migrate_twice_preserves_existing_data(self):
        self.db.execute(
            "INSERT INTO skill_catalog_state(id,generation) VALUES(1,9) ON CONFLICT(id) DO UPDATE SET generation=9"
        )
        before = [tuple(r) for r in self.db.execute("SELECT * FROM projects")]
        self.db.executescript(self.schema)
        self.db.executescript(self.schema)
        self.assertEqual(self.store.generation(), 9)
        self.assertEqual(
            [tuple(r) for r in self.db.execute("SELECT * FROM projects")], before
        )

    def test_draft_revision_conflict(self):
        draft = self.store.create_draft()
        updated = self.store.update_draft_cas(draft["draft_id"], 1, state="ready")
        self.assertEqual(updated["draft_revision"], 2)
        with self.assertRaises(SkillConflict):
            self.store.update_draft_cas(draft["draft_id"], 1, state="failed")

    def test_reserved_id_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO user_skills(skill_id,title,created_at,updated_at) VALUES('data-reconcile','x','now','now')"
            )

    def test_strict_spec_rejects_unknown_fields_and_empty_steps(self):
        with self.assertRaises(ValueError):
            SkillSpec.model_validate(
                {"title": "流程", "description": "说明", "steps": [], "shell": "run"}
            )

    def test_store_does_not_commit_callers_transaction(self):
        self.store.create_draft()
        self.db.rollback()
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM skill_drafts").fetchone()[0], 0
        )
