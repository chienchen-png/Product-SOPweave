from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
from routes import projects
from storage.project_store import ProjectStore
from storage.project_data_store import ProjectDataStore
from tools.project_data_importer import ProjectDataImporter
import openpyxl


class ProjectRouteTests(unittest.TestCase):
    def test_clone_starts_a_new_conversation_without_copying_execution_records(self):
        source, _, _ = self.clone_source()
        self.db.execute(
            "INSERT INTO conversations(id,project_id,created_at,updated_at) VALUES('legacy',?,'now','now')",
            (source["id"],),
        )
        self.db.execute(
            "INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES('message','legacy','user','旧对话','now')"
        )
        self.db.execute(
            "INSERT INTO runs(id,project_id,conversation_id,status,created_at,updated_at) VALUES('completed',?,?,'completed','now','now')",
            (source["id"], source["id"]),
        )
        self.db.execute(
            "INSERT INTO agent_session_items(session_id,seq,item_json,created_at) VALUES(?,1,'{}','now')",
            (source["id"] + ":general:model",),
        )
        self.db.commit()
        result = self.client.post(f'/api/projects/{source["id"]}/duplicate')
        self.assertEqual(result.status_code, 201, result.get_json())
        target = result.get_json()["data"]["id"]
        for table in ("conversations", "runs"):
            self.assertEqual(
                self.db.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE project_id=?', (target,)
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                self.db.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE project_id=?',
                    (source["id"],),
                ).fetchone()[0],
                1,
            )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM agent_session_items").fetchone()[0], 1
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1
        )

    def clone_source(self):
        source = ProjectStore(self.db).create("源项目", str(self.root))
        book = openpyxl.Workbook()
        book.active.title = "数据"
        book.active.append(["编号", "金额"])
        book.active.append(["001", 10])
        book.active.append(["002", 20])
        book.save(self.root / "data.xlsx")
        book.close()
        imported = ProjectDataImporter(self.db).import_workbook(
            source["id"], "data.xlsx", str(self.root), anchor_rows={"数据": 0}
        )
        store = ProjectDataStore(self.db)
        file = store.get_file(imported["file_id"])
        sheet = store.list_sheets(file["id"])[0]
        return source, file, sheet

    def test_clone_preserves_primary_key_and_independent_mutable_rows(self):
        source, file, sheet = self.clone_source()
        result = self.client.post(f'/api/projects/{source["id"]}/duplicate')
        self.assertEqual(result.status_code, 201, result.get_json())
        clone = result.get_json()["data"]
        self.assertEqual(clone["work_dir"], source["work_dir"])
        store = ProjectDataStore(self.db)
        copied_file = store.list_files(clone["id"])[0]
        copied_sheet = store.list_sheets(copied_file["id"])[0]
        table = copied_sheet["table_name"]
        self.assertNotEqual(table, sheet["table_name"])
        columns = list(self.db.execute(f'PRAGMA table_info("{table}")'))
        self.assertEqual(next(row["pk"] for row in columns if row["name"] == "id"), 1)
        self.db.execute(
            f'INSERT INTO "{table}" (_source_row,"编号","金额") VALUES (4,"003",30)'
        )
        self.assertEqual(
            self.db.execute(f'SELECT id FROM "{table}" WHERE "编号"="003"').fetchone()[
                0
            ],
            3,
        )
        self.db.execute(f'UPDATE "{table}" SET "金额"=99 WHERE id=1')
        self.assertEqual(
            self.db.execute(
                f'SELECT "金额" FROM "{sheet["table_name"]}" WHERE id=1'
            ).fetchone()[0],
            10,
        )

    def test_clone_copies_audit_history_and_remaps_the_export_checkpoint(self):
        source, file, _ = self.clone_source()
        store = ProjectDataStore(self.db)
        store.log_change(
            file["id"],
            "数据",
            1,
            "update",
            col_index=1,
            old_value=9,
            new_value=10,
            col_name="金额",
            key_value='{"编号":"001"}',
        )
        applied = self.db.execute(
            "SELECT MAX(id) FROM project_data_changes"
        ).fetchone()[0]
        meta = json.loads(file["meta_json"])
        meta["exported_change_id"] = applied
        store.update_file(file["id"], meta_json=json.dumps(meta))
        store.log_change(
            file["id"],
            "数据",
            1,
            "update",
            col_index=1,
            old_value=10,
            new_value=15,
            col_name="金额",
            key_value='{"编号":"001"}',
        )
        self.db.commit()
        result = self.client.post(f'/api/projects/{source["id"]}/duplicate')
        self.assertEqual(result.status_code, 201, result.get_json())
        copied = store.list_files(result.get_json()["data"]["id"])[0]
        changes = store.list_changes(copied["id"])
        self.assertEqual(len(changes), 2)
        self.assertEqual(store.export_checkpoint(copied["id"]), changes[0]["id"])
        self.assertEqual(
            store.count_changes(copied["id"], pending_only=True)["total"], 1
        )
        self.assertEqual(changes[1]["key_value"], '{"编号":"001"}')

    def test_clone_rolls_back_project_metadata_and_tables_if_one_sheet_is_invalid(self):
        source, file, _ = self.clone_source()
        self.db.execute(
            "INSERT INTO project_data_sheets(file_id,sheet_name,sheet_index,table_name,row_count,col_count,headers_json) VALUES(?, '损坏',1,'data_dead_1',1,1,'[]')",
            (file["id"],),
        )
        self.db.commit()
        before = [
            row[0]
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        result = self.client.post(f'/api/projects/{source["id"]}/duplicate')
        self.assertGreaterEqual(result.status_code, 400)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM project_data_files").fetchone()[0], 1
        )
        self.assertEqual(
            [
                row[0]
                for row in self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ],
            before,
        )

    def test_clone_rejects_an_active_source_run_before_creating_a_copy(self):
        source, _, _ = self.clone_source()
        self.db.execute(
            "INSERT INTO runs(id,project_id,status,created_at,updated_at) VALUES('live',?,'waiting','2026-10-20','2026-10-20')",
            (source["id"],),
        )
        self.db.commit()
        result = self.client.post(f'/api/projects/{source["id"]}/duplicate')
        self.assertEqual(result.status_code, 409, result.get_json())
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1
        )

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            (Path(__file__).resolve().parents[1] / "storage/schema.sql").read_text(
                encoding="utf-8"
            )
        )
        self.addCleanup(self.db.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config["TASK_DB_PATH"] = self.root / "tasks.sqlite3"
        self.app.register_blueprint(projects.projects_bp)
        self.patch_db = patch("routes.projects.get_db", return_value=self.db)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        self.client = self.app.test_client()

    def create(self, directory):
        return self.client.post(
            "/api/projects", json={"name": "目录验收", "work_dir": str(directory)}
        )

    def assert_rejected_without_project(self, result):
        self.assertEqual(result.status_code, 400, result.get_json())
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0
        )

    def test_missing_directory_is_rejected_without_creating_it(self):
        target = self.root / "不存在"
        self.assert_rejected_without_project(self.create(target))
        self.assertFalse(target.exists())

    def test_file_cannot_be_selected_as_work_directory(self):
        target = self.root / "file.txt"
        target.write_text("keep", encoding="utf-8")
        self.assert_rejected_without_project(self.create(target))
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_unreadable_directory_is_rejected_before_creating_records(self):
        with patch("os.scandir", side_effect=PermissionError("denied")):
            result = self.create(self.root)
        self.assert_rejected_without_project(result)

    def test_existing_directory_is_created_once_without_altering_files(self):
        target = self.root / "中文 空格"
        target.mkdir()
        (target / "keep.txt").write_text("unchanged", encoding="utf-8")
        result = self.create(target)
        self.assertEqual(result.status_code, 201, result.get_json())
        self.assertEqual(Path(result.get_json()["data"]["work_dir"]), target)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1
        )
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "unchanged")
