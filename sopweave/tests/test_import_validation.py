from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from tools.project_data_importer import (
    ProjectDataImporter,
    WorkbookTooLargeError,
)
from tools.office_documents import inspect_workbook, read_sheet_range


class ImportValidationTests(unittest.TestCase):
    def test_excluded_columns_and_later_regions_are_counted_with_bounded_physical_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            rows=[['编号','金额','隐藏备注'],['001',10,'甲'],['002',20,'乙']]+[[f'EXTRA{i}',i,'尾注'] for i in range(30)]
            path=self._save(root,'regions.xlsx',{'数据':rows})
            db=self._db();self.addCleanup(db.close)
            result=ProjectDataImporter(db).import_workbook('p',path.name,str(root),anchor_rows={'数据':{'header_row':0,'data_start_row':1,'data_end_row':2,'start_col':0,'end_col':2,'excluded_columns':[2]}})
            meta=json.loads(db.execute('select meta_json from project_data_files where id=?',(result['file_id'],)).fetchone()[0])
            issue=next(i for i in meta['validation']['sheets']['数据']['issues'] if i['code']=='excluded_non_empty_cells')
            self.assertEqual(issue['count'],92)
            self.assertEqual(len(issue['cells']),20)
            self.assertEqual(issue['cells'][:5],['C2','C3','A4','B4','C4'])
            self.assertEqual(result['sheets'][0]['row_count'],2)

    def test_blank_header_diagnostics_keep_physical_columns_after_exclusions(self):
        for start_col, excluded in ((0, [1, 3]), (1, [3])):
            with self.subTest(
                start_col=start_col
            ), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = self._save(
                    root,
                    "columns.xlsx",
                    {"数据": [["金额", "金额", None, "备注"], [10, 20, 30, "甲"]]},
                )
                db = self._db()
                try:
                    result = ProjectDataImporter(db).import_workbook(
                        "p",
                        path.name,
                        str(root),
                        anchor_rows={
                            "数据": {
                                "header_row": 0,
                                "data_start_row": 1,
                                "data_end_row": 1,
                                "start_col": start_col,
                                "end_col": 3,
                                "excluded_columns": excluded,
                            }
                        },
                    )
                    meta = json.loads(
                        db.execute(
                            "SELECT meta_json FROM project_data_files WHERE id=?",
                            (result["file_id"],),
                        ).fetchone()[0]
                    )
                    self.assertEqual(meta["uncorrected_cols"], {"数据": [2]})
                    issue = next(
                        i
                        for i in meta["validation"]["sheets"]["数据"]["issues"]
                        if i["code"] == "blank_headers"
                    )
                    self.assertEqual(issue["columns"], [2])
                finally:
                    db.close()

    def test_macro_workbook_import_is_rejected_before_scanning_or_calculation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._save(
                root, "macro.xlsm", {"Data": [["Key", "Amount"], ["A", 10]]}
            )
            db = self._db()
            self.addCleanup(db.close)
            with patch("tools.project_data_importer.snapshot_and_scan") as scan, patch(
                "tools.project_data_importer.ensure_calculated_workbook"
            ) as calculate:
                with self.assertRaisesRegex(ValueError, "xlsm|宏"):
                    ProjectDataImporter(db).import_workbook("p", path.name, str(root))
                scan.assert_not_called()
                calculate.assert_not_called()
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM project_data_files").fetchone()[0], 0
            )

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        schema = (
            Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
        ).read_text(encoding="utf-8")
        db.executescript(schema)
        return db

    def _save(self, root: Path, name: str, sheets: dict[str, list[list]]) -> Path:
        path = root / name
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for sheet_name, rows in sheets.items():
            ws = wb.create_sheet(sheet_name)
            for row in rows:
                ws.append(row)
        wb.save(path)
        wb.close()
        return path

    def test_nulls_do_not_demote_numeric_type_and_leading_zeros_survive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._save(
                root,
                "types.xlsx",
                {
                    "数据": [
                        ["编号", "金额"],
                        ["001", 10],
                        ["002", None],
                        ["003", 30],
                    ],
                },
            )
            db = self._db()
            result = ProjectDataImporter(db).import_workbook(
                "p", path.name, str(root), anchor_rows={"数据": 0}
            )
            sheet = result["sheets"][0]
            record = db.execute(
                "SELECT headers_json FROM project_data_sheets WHERE file_id = ?",
                (result["file_id"],),
            ).fetchone()
            headers = json.loads(record["headers_json"])
            self.assertEqual(headers[0]["type"], "text")
            self.assertEqual(headers[1]["type"], "int")
            self.assertEqual(headers[1]["type_counts"], {"int": 2})
            values = db.execute(
                f'SELECT "编号" FROM "{sheet["table_name"]}" ORDER BY id'
            ).fetchall()
            self.assertEqual([row["编号"] for row in values], ["001", "002", "003"])
            db.close()

    def test_mixed_types_duplicates_and_excluded_cells_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._save(
                root,
                "quality.xlsx",
                {
                    "数据": [
                        ["金额", "金额", None],
                        [10, 1, "范围外说明"],
                        ["待定", 2, None],
                    ],
                    "说明": [["使用说明"], ["请核对来源"]],
                },
            )
            db = self._db()
            result = ProjectDataImporter(db).import_workbook(
                "p", path.name, str(root), anchor_rows={"数据": 0}
            )
            file_rec = db.execute(
                "SELECT meta_json FROM project_data_files WHERE id = ?",
                (result["file_id"],),
            ).fetchone()
            meta = json.loads(file_rec["meta_json"])
            headers = json.loads(
                db.execute(
                    "SELECT headers_json FROM project_data_sheets WHERE file_id = ?",
                    (result["file_id"],),
                ).fetchone()["headers_json"]
            )
            self.assertEqual([item["name"] for item in headers], ["金额", "金额_B"])
            self.assertEqual(headers[0]["type"], "mixed")
            codes = {
                item["code"] for item in meta["validation"]["sheets"]["数据"]["issues"]
            }
            self.assertIn("duplicate_headers", codes)
            self.assertIn("mixed_or_error_type", codes)
            self.assertIn("excluded_non_empty_cells", codes)
            self.assertEqual(
                meta["validation"]["skipped_sheets"],
                [{"name": "说明", "classification": "notes"}],
            )
            db.close()

    def test_materialization_failure_rolls_back_all_dynamic_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._save(
                root,
                "rollback.xlsx",
                {
                    "甲": [["编号", "金额"], ["A", 1]],
                    "乙": [["编号", "金额"], ["B", 2]],
                },
            )
            db = self._db()
            importer = ProjectDataImporter(db)
            original = importer._materialize_sheet
            calls = 0

            def fail_second(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("模拟批量写入失败")
                return original(*args, **kwargs)

            importer._materialize_sheet = fail_second  # type: ignore[method-assign]
            with self.assertRaisesRegex(RuntimeError, "模拟批量写入失败"):
                importer.import_workbook(
                    "p", path.name, str(root), anchor_rows={"甲": 0, "乙": 0}
                )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM project_data_sheets").fetchone()[0],
                0,
            )
            dynamic = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'data_%'"
            ).fetchall()
            self.assertEqual(dynamic, [])
            self.assertEqual(
                db.execute("SELECT status FROM project_data_files").fetchone()[0],
                "failed",
            )
            db.close()

    def test_oversized_sheet_is_blocked_before_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._save(
                root,
                "large.xlsx",
                {
                    "数据": [["编号", "金额"], ["1", 1], ["2", 2], ["3", 3]],
                },
            )
            db = self._db()
            with patch("tools.project_data_importer.MAX_ROWS_PER_SHEET", 3):
                with self.assertRaisesRegex(WorkbookTooLargeError, "已阻止截断导入"):
                    ProjectDataImporter(db).import_workbook(
                        "p", path.name, str(root), anchor_rows={"数据": 0}
                    )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM project_data_files").fetchone()[0],
                0,
            )
            db.close()

    def test_office_tools_share_scanner_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._save(
                root,
                "shared.xlsx",
                {
                    "数据": [["编号", "金额"], ["001", 10]],
                },
            )
            info = inspect_workbook(path)
            data = read_sheet_range(path, "数据", "A1:B2", False)
            self.assertTrue(info["success"])
            self.assertEqual(info["workbook"]["sheets"][0]["actual_range"], "A1:B2")
            self.assertEqual(info["workbook"]["formula_count"], 0)
            self.assertEqual(
                {cell["coordinate"]: cell["value"] for cell in data["cells"]},
                {"A1": "编号", "B1": "金额", "A2": "001", "B2": 10},
            )

    def test_user_headers_cannot_collide_with_internal_lineage_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._save(
                root,
                "reserved.xlsx",
                {
                    "数据": [["id", "_source_row", "金额"], ["A", 99, 10]],
                },
            )
            db = self._db()
            result = ProjectDataImporter(db).import_workbook(
                "p", path.name, str(root), anchor_rows={"数据": 0}
            )
            headers = json.loads(
                db.execute(
                    "SELECT headers_json FROM project_data_sheets WHERE file_id = ?",
                    (result["file_id"],),
                ).fetchone()["headers_json"]
            )
            self.assertEqual(
                [item["name"] for item in headers],
                ["id_A", "source_row", "金额"],
            )
            db.close()


if __name__ == "__main__":
    unittest.main()
