from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import openpyxl

from storage.project_data_store import ProjectDataStore
from tools.project_data_importer import NeedAnchorError, ProjectDataImporter


class ImportIdentityTests(unittest.TestCase):
    def test_dataset_lookup_rejects_ambiguous_basenames_and_accepts_path_or_id(self):
        for folder, amount in (("left", 10), ("right", 999)):
            self.write(amount)
            (self.root / folder).mkdir()
            shutil.copy2(self.root / "data.xlsx", self.root / folder / "data.xlsx")
            self.importer.import_workbook("p",f"{folder}/data.xlsx",str(self.root),anchor_rows={"Data":0})
        with self.assertRaisesRegex(ValueError,"同名|不唯一"):
            self.store.find_file_by_name("p","data.xlsx")
        left=self.store.find_file_by_name("p","left/data.xlsx")
        self.assertEqual(left["source_path"],"left/data.xlsx")
        self.assertEqual(self.store.find_file_by_name("p",left["id"])["id"],left["id"])
        self.assertEqual(self.store.find_file_by_name("p","left\\data.xlsx")["id"],left["id"])
        self.assertFalse(self.store.find_file_by_name("another-project",left["id"]))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript(Path("storage/schema.sql").read_text(encoding="utf-8"))
        self.importer = ProjectDataImporter(self.db)
        self.store = ProjectDataStore(self.db)
        self.write(10)

    def write(self, amount):
        book = openpyxl.Workbook()
        book.active.title = "Data"
        book.active.append(["code", "amount"])
        book.active.append(["001", amount])
        book.save(self.root / "data.xlsx")
        book.close()

    def load(self, **kwargs):
        return self.importer.import_workbook("p", "data.xlsx", str(self.root), **kwargs)

    def assert_old_intact(self, original):
        record = self.store.get_file(original["file_id"])
        self.assertEqual(record.get("status"), "done")
        sheet = self.store.list_sheets(original["file_id"])[0]
        self.assertEqual(
            self.db.execute(f'SELECT amount FROM "{sheet["table_name"]}"').fetchone()[
                0
            ],
            10,
        )

    def test_equal_bytes_at_different_paths_keep_independent_source_identity(self):
        original = self.load(anchor_rows={"Data": 0})
        shutil.copy2(self.root / "data.xlsx", self.root / "other.xlsx")
        copied = self.importer.import_workbook(
            "p", "other.xlsx", str(self.root), anchor_rows={"Data": 0}
        )
        self.assertNotEqual(copied["file_id"], original["file_id"])
        self.assertEqual(copied["file"]["source_path"], "other.xlsx")
        repeated = self.load()
        self.assertTrue(repeated["reused"])
        self.assertEqual(repeated["file_id"], original["file_id"])

    def test_reimport_waiting_for_review_preserves_the_existing_dataset(self):
        original = self.load(anchor_rows={"Data": 0})
        self.write(20)
        with self.assertRaises(NeedAnchorError):
            self.load(always_anchor=True)
        self.assert_old_intact(original)

    def test_reimport_failure_preserves_old_tables_and_change_history(self):
        original = self.load(anchor_rows={"Data": 0})
        self.store.log_change(
            original["file_id"],
            "Data",
            1,
            "update",
            col_index=1,
            old_value=9,
            new_value=10,
        )
        self.db.commit()
        self.write(20)
        with patch.object(
            self.importer,
            "_materialize_sheet",
            side_effect=ValueError("injected validation failure"),
        ):
            with self.assertRaisesRegex(ValueError, "injected validation failure"):
                self.load(anchor_rows={"Data": 0})
        self.assert_old_intact(original)
        self.assertEqual(len(self.store.list_changes(original["file_id"])), 1)

    def test_successful_replacement_exposes_only_the_new_done_dataset(self):
        original = self.load(anchor_rows={"Data": 0})
        self.write(20)
        replacement = self.load(anchor_rows={"Data": 0})
        self.assertFalse(self.store.get_file(original["file_id"]))
        self.assertEqual(replacement["file"]["status"], "done")
        self.assertEqual(
            len([row for row in self.store.list_files("p") if row["status"] == "done"]),
            1,
        )
        sheet = self.store.list_sheets(replacement["file_id"])[0]
        self.assertEqual(
            self.db.execute(f'SELECT amount FROM "{sheet["table_name"]}"').fetchone()[
                0
            ],
            20,
        )

    def test_publication_failure_rolls_back_old_table_deletion(self):
        original = self.load(anchor_rows={"Data": 0})
        self.write(20)
        delete_file = self.store.delete_file

        def fail_after_delete(file_id, **kwargs):
            delete_file(file_id, **kwargs)
            raise OSError("injected publication failure")

        with patch.object(
            self.importer.store, "delete_file", side_effect=fail_after_delete
        ):
            with self.assertRaisesRegex(OSError, "injected publication failure"):
                self.load(anchor_rows={"Data": 0})
        self.assert_old_intact(original)
        self.assertEqual(
            len([row for row in self.store.list_files("p") if row["status"] == "done"]),
            1,
        )

    def test_explicit_review_cannot_be_bypassed_by_a_cached_import(self):
        original = self.load(anchor_rows={"Data": 0})
        with self.assertRaises(NeedAnchorError):
            self.load(always_anchor=True)
        self.assert_old_intact(original)

    def test_explicit_region_change_applies_even_when_source_bytes_are_unchanged(self):
        original = self.load(anchor_rows={"Data": 0})
        changed = self.load(
            anchor_rows={"Data": {"header_row": 0, "start_col": 1, "end_col": 1}}
        )
        self.assertNotEqual(changed["file_id"], original["file_id"])
        self.assertEqual(self.store.list_sheets(changed["file_id"])[0]["col_count"], 1)

    def test_equivalent_relative_paths_reuse_the_same_source(self):
        original = self.load(anchor_rows={"Data": 0})
        repeated = self.importer.import_workbook("p", "./data.xlsx", str(self.root))
        self.assertTrue(repeated["reused"])
        self.assertEqual(repeated["file_id"], original["file_id"])

    def test_concurrent_reimports_publish_one_dataset_for_the_source(self):
        database = self.root / "parallel.db"
        with closing(sqlite3.connect(database)) as setup:
            setup.executescript(Path("storage/schema.sql").read_text(encoding="utf-8"))
            setup.row_factory = sqlite3.Row
            ProjectDataImporter(setup).import_workbook(
                "p", "data.xlsx", str(self.root), anchor_rows={"Data": 0}
            )
        barrier = threading.Barrier(2)
        failures = []
        original = ProjectDataImporter._materialize_sheet

        def slow_materialize(importer, *args, **kwargs):
            result = original(importer, *args, **kwargs)
            time.sleep(0.1)
            return result

        def run():
            try:
                with closing(sqlite3.connect(database)) as db:
                    db.row_factory = sqlite3.Row
                    barrier.wait(timeout=3)
                    ProjectDataImporter(db).import_workbook(
                        "p", "data.xlsx", str(self.root), anchor_rows={"Data": 0}
                    )
            except Exception as error:
                failures.append(str(error))

        threads = [threading.Thread(target=run) for _ in range(2)]
        with patch.object(
            ProjectDataImporter, "_materialize_sheet", new=slow_materialize
        ):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM project_data_files WHERE status='done'"
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
