from __future__ import annotations

import sqlite3
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl
from flask import Flask

from routes import project_data
from tools.project_data_importer import IMPORT_VERSION, ProjectDataImporter


class ImportWorkflowTests(unittest.TestCase):
    def test_review_excludes_notes_and_empty_sheets_and_explains_what_is_skipped(self):
        from tools.table_layout_detector import Layout
        from tools.project_data_importer import NeedAnchorError
        with tempfile.TemporaryDirectory() as tmp:
            app=Flask(__name__)
            app.config['TASK_DB_PATH']=Path(tmp)/'tasks.db'
            layouts={'数据':Layout(columns=['编号','金额']),
                     '说明':Layout(classification='notes'),
                     '空白':Layout(classification='empty')}
            probe=NeedAnchorError('hash',layouts,{'数据':[['编号','金额'],['001',10]]})
            with patch('routes.project_data.get_db'), patch('routes.project_data._project',return_value={'work_dir':tmp}), patch('routes.project_data.ProjectDataImporter') as importer:
                importer.return_value.import_workbook.side_effect=probe
                project_data._run_import(app,'completeness','p','data.xlsx',always_anchor=True)
            anchor=project_data._task_store(app).get('completeness')['anchor']
            self.assertEqual([s['name'] for s in anchor['sheets']],['数据'])
            self.assertEqual(anchor['skipped_sheets'],[{'name':'说明','classification':'notes'},{'name':'空白','classification':'empty'}])

    def test_review_carries_hidden_source_coordinates_without_omitting_hidden_values(self):
        from tools.project_data_importer import NeedAnchorError
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=self._workbook(root);book=openpyxl.load_workbook(path)
            book.active.row_dimensions[2].hidden=True
            book.active.column_dimensions['B'].hidden=True
            book.save(path);book.close()
            db=self._db();self.addCleanup(db.close)
            with self.assertRaises(NeedAnchorError) as raised:
                ProjectDataImporter(db).import_workbook('p',path.name,str(root),always_anchor=True)
            details=raised.exception.sheet_info
            self.assertEqual(details[0]['hidden_rows'],[2])
            self.assertEqual(details[0]['hidden_columns'],['B'])
            self.assertEqual(details[0]['hidden_row_count'],1)

    def test_index_and_list_use_the_same_current_import_version(self):
        app = Flask(__name__)
        app.register_blueprint(project_data.project_data_bp)
        for version in (IMPORT_VERSION - 1, IMPORT_VERSION):
            with self.subTest(version=version), patch(
                "routes.project_data.get_db"
            ) as get_db, patch("routes.project_data.ProjectDataStore") as store_type:
                record = {
                    "id": "file",
                    "meta_json": json.dumps({"import_version": version}),
                }
                store = store_type.return_value
                store.get_file.return_value = record
                store.list_files.return_value = [record.copy()]
                store.list_sheets.return_value = []
                store.list_changes.return_value = []
                get_db.return_value.execute.return_value.fetchall.return_value = []
                client = app.test_client()
                index = client.get("/api/project-data/file/index").get_json()["data"][
                    "file"
                ]
                listing = client.get("/api/project-data/list?project_id=p").get_json()[
                    "data"
                ]["files"][0]
                self.assertEqual(index["needs_reimport"], version < IMPORT_VERSION)
                self.assertEqual(index["needs_reimport"], listing["needs_reimport"])

    def test_multiple_review_rounds_preserve_already_confirmed_sheets(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Flask(__name__)
            app.config["TASK_DB_PATH"] = Path(directory) / "tasks.db"
            app.register_blueprint(project_data.project_data_bp)
            with app.app_context():
                project_data._task_store(app).put(
                    "review",
                    {
                        "status": "needs_anchor",
                        "project_id": "p",
                        "path": "data.xlsx",
                        "anchor_rows": {"first": 0},
                    },
                )
                with patch("routes.project_data.threading.Thread") as thread:
                    result = app.test_client().post(
                        "/api/project-data/import/anchor",
                        json={"import_id": "review", "anchors": {"second": 1}},
                    )
                self.assertEqual(result.status_code, 200)
                self.assertEqual(
                    thread.call_args.kwargs["args"][4], {"first": 0, "second": 1}
                )

    def test_review_rejects_a_changed_source_without_starting_a_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data.xlsx").write_bytes(b"changed after review")
            app = Flask(__name__)
            app.config["TASK_DB_PATH"] = root / "tasks.db"
            app.register_blueprint(project_data.project_data_bp)
            with app.app_context():
                project_data._task_store(app).put(
                    "review",
                    {
                        "status": "needs_anchor",
                        "project_id": "p",
                        "path": "data.xlsx",
                        "anchor": {
                            "file_hash": hashlib.sha256(b"old content").hexdigest()
                        },
                    },
                )
                with patch(
                    "routes.project_data._project",
                    return_value={"id": "p", "work_dir": str(root)},
                ), patch("routes.project_data.threading.Thread") as thread:
                    result = app.test_client().post(
                        "/api/project-data/import/anchor",
                        json={"import_id": "review", "anchors": {"Data": 0}},
                    )
                self.assertEqual(result.status_code, 409, result.get_json())
                thread.assert_not_called()
                self.assertEqual(
                    project_data._task_store(app).get("review")["status"],
                    "needs_anchor",
                )

    def test_anchor_api_rejects_fractional_or_boolean_coordinates_before_resuming(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Flask(__name__)
            app.config["TASK_DB_PATH"] = Path(directory) / "tasks.db"
            app.register_blueprint(project_data.project_data_bp)
            task_id = "invalid-coordinates"
            for value in (0.5, True):
                with self.subTest(value=value), app.app_context():
                    project_data._task_store(app).put(
                        task_id,
                        {
                            "status": "needs_anchor",
                            "project_id": "p",
                            "path": "data.xlsx",
                        },
                    )
                    with patch("routes.project_data.threading.Thread") as thread:
                        result = app.test_client().post(
                            "/api/project-data/import/anchor",
                            json={
                                "import_id": task_id,
                                "anchors": {"Data": {"header_row": value}},
                            },
                        )
                    self.assertEqual(result.status_code, 400, result.get_json())
                    thread.assert_not_called()
                    self.assertEqual(
                        project_data._task_store(app).get(task_id)["status"],
                        "needs_anchor",
                    )

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.row_factory = sqlite3.Row
        schema = (
            Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
        ).read_text(encoding="utf-8")
        db.executescript(schema)
        return db

    def _workbook(self, root: Path, formula: bool = False) -> Path:
        path = root / "data.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "数据"
        ws.append(["编号", "金额"])
        ws.append(["001", "=1+2" if formula else 3])
        wb.save(path)
        wb.close()
        return path

    def test_formula_gate_is_exposed_as_needs_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._workbook(root, formula=True)
            db = self._db()
            app = Flask(__name__)
            app.config["TASK_DB_PATH"] = root / "tasks.sqlite3"
            task_id = "formula-review"
            project_data._task_store(app).delete(task_id)
            blocked = {
                "requires_review": True,
                "missing_after": 1,
                "missing_before": 1,
                "attempts": [],
            }
            with (
                patch("routes.project_data.get_db", return_value=db),
                patch(
                    "routes.project_data._project",
                    return_value={
                        "id": "p",
                        "name": "Project",
                        "work_dir": str(root),
                    },
                ),
                patch(
                    "tools.project_data_importer.ensure_calculated_workbook",
                    return_value=blocked,
                ),
            ):
                project_data._run_import(app, task_id, "p", "data.xlsx")
            state = project_data._task_store(app).get(task_id)
            self.assertEqual(state["status"], "needs_review")
            self.assertEqual(state["anchor"]["reason"], "formula_calculation_required")
            self.assertEqual(state["anchor"]["calculation"]["missing_after"], 1)
            project_data._task_store(app).delete(task_id)
            db.close()

    def test_all_three_project_contexts_produce_the_same_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._workbook(root)
            db = self._db()
            structures = []
            for project_id in ("visible", "__app_recon__", "__app_finmod__"):
                result = ProjectDataImporter(db).import_workbook(
                    project_id, "data.xlsx", str(root), anchor_rows={"数据": 0}
                )
                sheet = result["sheets"][0]
                record = db.execute(
                    "SELECT headers_json FROM project_data_sheets WHERE file_id = ?",
                    (result["file_id"],),
                ).fetchone()
                structures.append((sheet["row_count"], sheet["col_count"], record[0]))
            self.assertEqual(structures[0], structures[1])
            self.assertEqual(structures[1], structures[2])
            db.close()

    def test_finmod_upload_is_deleted_only_after_snapshot_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload = root / "data.xlsx"
            upload.write_bytes(b"upload")
            project_data._drop_source_file(str(root), upload.name, "missing.xlsx")
            self.assertTrue(upload.exists())
            snapshot = root / ".sopweave" / "import-cache" / "x" / "original.xlsx"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_bytes(b"snapshot")
            project_data._drop_source_file(
                str(root), upload.name, str(snapshot.relative_to(root))
            )
            self.assertFalse(upload.exists())
            self.assertTrue(snapshot.exists())


if __name__ == "__main__":
    unittest.main()
