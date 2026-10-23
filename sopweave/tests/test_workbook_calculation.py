from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from tools.workbook_calculation import (
    CalculationAttempt,
    CalculationEngine,
    ensure_calculated_workbook,
)
from tools.workbook_scan import snapshot_and_scan


class _UnavailableEngine(CalculationEngine):
    name = "unavailable"

    def is_available(self, source: Path) -> bool:
        return False

    def recalculate(
        self, source: Path, destination: Path, timeout_seconds: int
    ) -> CalculationAttempt:
        raise AssertionError("unavailable engine must not run")


class _CopyEngine(CalculationEngine):
    name = "copy-engine"

    def is_available(self, source: Path) -> bool:
        return True

    def recalculate(
        self, source: Path, destination: Path, timeout_seconds: int
    ) -> CalculationAttempt:
        shutil.copy2(source, destination)
        return CalculationAttempt(
            engine=self.name,
            available=True,
            success=True,
            message="copied",
        )


class _TimeoutEngine(CalculationEngine):
    name = "timeout-engine"

    def is_available(self, source: Path) -> bool:
        return True

    def recalculate(
        self, source: Path, destination: Path, timeout_seconds: int
    ) -> CalculationAttempt:
        return CalculationAttempt(
            engine=self.name,
            available=True,
            success=False,
            message="公式重算超时",
        )


class WorkbookCalculationTests(unittest.TestCase):
    def test_excel_uses_a_short_disposable_path_for_long_workspace_paths(self):
        from tools.workbook_calculation import ExcelComEngine
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            padding = 220 - len(str(Path(directory))) - 1 - len("/calculated.xlsx")
            root = Path(directory) / ("x" * padding)
            root.mkdir()
            source = root / "source.xlsx"
            source.write_bytes(b"original workbook")
            output = root / "calculated.xlsx"
            self.assertGreater(len(str(output)), 218)
            opened = []

            def calculate(command, **options):
                staged = Path(command[-2])
                self.assertLess(len(str(staged)), 218)
                self.assertNotEqual(staged.parent, output.parent)
                self.assertEqual(staged.read_bytes(), b"original workbook")
                staged.write_bytes(b"calculated workbook")
                opened.append(staged)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch(
                "tools.workbook_calculation.subprocess.run", side_effect=calculate
            ):
                result = ExcelComEngine().recalculate(source, output, 10)
            self.assertTrue(result.success, result)
            self.assertEqual(output.read_bytes(), b"calculated workbook")
            self.assertEqual(source.read_bytes(), b"original workbook")
            self.assertFalse(opened[0].exists())

    def test_stale_cache_flags_require_recalculation_even_when_a_value_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._formula_snapshot(Path(directory))
            manifest["formula_missing_count"] = 0
            manifest["sheets"][0]["formula_missing_count"] = 0
            manifest["formula_cached_count"] = 1
            manifest["has_unresolved_formulas"] = False
            manifest["calculation_properties"] = {"fullCalcOnLoad": True}
            result = ensure_calculated_workbook(manifest, engines=[])
            self.assertTrue(result["requires_review"], result)

    def test_stale_cache_can_be_explicitly_accepted_with_unverified_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._formula_snapshot(Path(directory))
            manifest["formula_missing_count"] = 0
            manifest["formula_cached_count"] = 1
            manifest["has_unresolved_formulas"] = False
            manifest["calculation_properties"] = {"fullCalcOnLoad": True}
            manifest["sheets"][0]["formula_missing_count"] = 0
            result = ensure_calculated_workbook(
                manifest, engines=[], allow_stale_cache=True
            )
            self.assertFalse(result["requires_review"], result)
            self.assertEqual(result["freshness"], "stored_cache_unverified")
            self.assertTrue(result["user_acknowledged_stale"])
            self.assertFalse(result["verified_recalculation"])

    def test_error_cells_are_not_accepted_as_verified_cached_values(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._formula_snapshot(Path(directory))
            manifest["formula_missing_count"] = 0
            manifest["sheets"][0]["formula_missing_count"] = 0
            manifest["formula_cached_count"] = 1
            manifest["has_unresolved_formulas"] = False
            manifest["calculation_properties"] = {
                "fullCalcOnLoad": False,
                "forceFullCalc": False,
            }
            manifest["sheets"][0]["error_cell_count"] = 1
            result = ensure_calculated_workbook(manifest, engines=[])
            self.assertTrue(result["requires_review"], result)

    def test_recalculated_audit_uses_new_manifest_when_quality_counts_are_equal(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._formula_snapshot(Path(directory))
            manifest.update(
                formula_missing_count=0,
                formula_cached_count=1,
                has_unresolved_formulas=False,
                calculation_properties={"fullCalcOnLoad": True},
            )
            old_sheet = {
                **manifest["sheets"][0],
                "formula_missing_count": 0,
                "formula_cached_count": 1,
                "formula_examples": [{"cell": "A2", "cached_value": 999}],
            }
            manifest["sheets"] = [old_sheet]
            rescanned = {
                **manifest,
                "calculation_properties": {"fullCalcOnLoad": False},
                "sheets": [
                    {
                        **old_sheet,
                        "formula_examples": [{"cell": "A2", "cached_value": 3}],
                    }
                ],
            }
            result = ensure_calculated_workbook(
                manifest, engines=[_CopyEngine()], scanner=lambda _: rescanned
            )
            self.assertTrue(result["success"])
            self.assertEqual(
                result["manifest"]["sheets"][0]["formula_examples"][0]["cached_value"],
                3,
            )
            self.assertFalse(
                result["manifest"]["calculation_properties"]["fullCalcOnLoad"]
            )
            self.assertEqual(
                manifest["sheets"][0]["formula_examples"][0]["cached_value"], 999
            )

    def _formula_snapshot(self, root: Path) -> dict:
        path = root / "formula.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["金额"])
        ws.append(["=1+2"])
        wb.save(path)
        wb.close()
        return snapshot_and_scan(path, root)

    def test_cached_workbook_needs_no_external_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "values.xlsx"
            wb = openpyxl.Workbook()
            wb.active.append(["金额"])
            wb.active.append([3])
            wb.save(path)
            wb.close()
            manifest = snapshot_and_scan(path, tmp)

            result = ensure_calculated_workbook(
                manifest, engines=[_UnavailableEngine()]
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["engine"], "cached-values")
        self.assertFalse(result["requires_review"])
        self.assertEqual(result["attempts"], [])

    def test_unavailable_engines_leave_an_explicit_review_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._formula_snapshot(Path(tmp))
            result = ensure_calculated_workbook(
                manifest, engines=[_UnavailableEngine()]
            )

        self.assertFalse(result["success"])
        self.assertTrue(result["requires_review"])
        self.assertEqual(result["missing_before"], 1)
        self.assertEqual(result["missing_after"], 1)
        self.assertFalse(result["attempts"][0]["available"])

    def test_recalculation_is_verified_by_rescanning_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._formula_snapshot(Path(tmp))

            def resolved_scanner(path: Path) -> dict:
                return {
                    **manifest,
                    "source_name": path.name,
                    "formula_cached_count": 1,
                    "formula_missing_count": 0,
                    "has_unresolved_formulas": False,
                    "sheets": [
                        {
                            **manifest["sheets"][0],
                            "formula_cached_count": 1,
                            "formula_missing_count": 0,
                        }
                    ],
                }

            result = ensure_calculated_workbook(
                manifest,
                engines=[_CopyEngine()],
                scanner=resolved_scanner,
            )

            self.assertTrue(result["success"])
            self.assertEqual(result["engine"], "copy-engine")
            self.assertEqual(result["missing_after"], 0)
            self.assertTrue(Path(result["calculated_path"]).is_file())

    def test_timeout_is_reported_without_accepting_the_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._formula_snapshot(Path(tmp))
            result = ensure_calculated_workbook(
                manifest, engines=[_TimeoutEngine()], timeout_seconds=1
            )

        self.assertFalse(result["success"])
        self.assertTrue(result["requires_review"])
        self.assertIn("超时", result["attempts"][0]["message"])

    def test_engine_success_without_formula_improvement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._formula_snapshot(Path(tmp))
            result = ensure_calculated_workbook(
                manifest,
                engines=[_CopyEngine()],
            )

        self.assertFalse(result["success"])
        self.assertTrue(result["requires_review"])
        self.assertEqual(result["attempts"][0]["missing_after"], 1)


if __name__ == "__main__":
    unittest.main()
