from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from config.paths import portable_work_dir, resolve_work_dir
from config.settings import BASE_DIR, DATA_DIR
from storage.path_migration import migrate_portable_paths
from storage.project_store import ProjectStore


class PortablePathTests(unittest.TestCase):
    def test_internal_paths_are_relative_and_runtime_paths_are_absolute(self) -> None:
        stored = portable_work_dir("demo", "test-fixtures/phase5")
        self.assertEqual(stored, "test-fixtures/phase5")
        self.assertEqual(resolve_work_dir(stored), BASE_DIR / "test-fixtures" / "phase5")

    def test_external_user_directory_stays_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(portable_work_dir("demo", temp_dir), str(Path(temp_dir).resolve()))

    def test_hidden_app_and_source_paths_are_migrated_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_root = Path(temp_dir) / "old-install" / "data" / "app-workspaces" / "finmod"
            source = old_root / "Book.xlsx"
            backup = old_root / "Book.xlsx.bak"
            db = sqlite3.connect(":memory:")
            self.addCleanup(db.close)
            db.row_factory = sqlite3.Row
            db.executescript(
                """
                CREATE TABLE projects (id TEXT, name TEXT, work_dir TEXT);
                CREATE TABLE project_data_files (
                    id TEXT, project_id TEXT, source_path TEXT
                );
                CREATE TABLE writeback_history (
                    id INTEGER, file_id TEXT, backup_path TEXT
                );
                """
            )
            db.execute(
                "INSERT INTO projects VALUES (?, ?, ?)",
                ("p1", "__app_finmod__", str(old_root)),
            )
            db.execute(
                "INSERT INTO project_data_files VALUES (?, ?, ?)",
                ("f1", "p1", str(source)),
            )
            db.execute(
                "INSERT INTO project_data_files VALUES (?, ?, ?)",
                ("orphan", "deleted-project", str(source)),
            )
            db.execute(
                "INSERT INTO writeback_history VALUES (?, ?, ?)",
                (1, "f1", str(backup)),
            )

            self.assertEqual(migrate_portable_paths(db, backup_path=None), 4)
            self.assertEqual(migrate_portable_paths(db, backup_path=None), 0)
            stored_dir = db.execute("SELECT work_dir FROM projects").fetchone()[0]
            managed_dir = DATA_DIR / "app-workspaces" / "finmod"
            self.assertEqual((BASE_DIR / stored_dir).resolve(), managed_dir.resolve())
            self.assertEqual(
                Path(stored_dir).is_absolute(), not managed_dir.is_relative_to(BASE_DIR)
            )
            self.assertEqual(
                [row[0] for row in db.execute("SELECT source_path FROM project_data_files ORDER BY id")],
                ["Book.xlsx", "Book.xlsx"],
            )
            self.assertEqual(
                db.execute("SELECT backup_path FROM writeback_history").fetchone()[0],
                "Book.xlsx.bak",
            )
            self.assertEqual(
                Path(ProjectStore(db).get_by_id("p1")["work_dir"]),
                managed_dir,
            )


if __name__ == "__main__":
    unittest.main()
