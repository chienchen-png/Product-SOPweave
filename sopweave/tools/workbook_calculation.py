"""Verified formula recalculation on disposable workbook copies."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from tools.workbook_scan import scan_workbook


@dataclass(frozen=True)
class CalculationAttempt:
    engine: str
    available: bool
    success: bool
    message: str
    missing_after: int | None = None


class CalculationEngine:
    """Interface implemented by workbook calculation backends."""

    name = "calculation-engine"

    def is_available(self, source: Path) -> bool:
        raise NotImplementedError

    def recalculate(
        self,
        source: Path,
        destination: Path,
        timeout_seconds: int,
    ) -> CalculationAttempt:
        raise NotImplementedError


class ExcelComEngine(CalculationEngine):
    """Use installed Microsoft Excel to rebuild dependencies and caches."""

    name = "microsoft-excel"

    def is_available(self, source: Path) -> bool:
        if os.name != "nt" or source.suffix.lower() not in {".xlsx", ".xlsm"}:
            return False
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application\CLSID"):
                return True
        except OSError:
            return False

    def recalculate(
        self,
        source: Path,
        destination: Path,
        timeout_seconds: int,
    ) -> CalculationAttempt:
        # Excel's path limit is shorter than Windows'. Cache paths include a
        # content hash, so the automation process must use a short copy.
        with tempfile.TemporaryDirectory(prefix="fra-calc-") as directory:
            staged = Path(directory) / f"workbook{source.suffix.lower()}"
            attempt = self._recalculate_copy(source, staged, timeout_seconds)
            if attempt.success:
                try:
                    shutil.copy2(staged, destination)
                except OSError as exc:
                    return CalculationAttempt(
                        self.name, True, False, f"无法保存重算副本: {exc}"
                    )
            return attempt

    def _recalculate_copy(
        self, source: Path, destination: Path, timeout_seconds: int
    ) -> CalculationAttempt:
        shutil.copy2(source, destination)
        script = r"""
param([string]$WorkbookPath, [int]$TimeoutSeconds)
$ErrorActionPreference = "Stop"
$excel = $null
$workbook = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.AutomationSecurity = 3
    $workbook = $excel.Workbooks.Open($WorkbookPath, 0, $false)
    $excel.CalculateFullRebuild()
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    # CalculateFullRebuild is explicit; some Excel installations retain
    # xlPending (2) after it returns. Wait for active calculation (1), then
    # save and let the scanner verify every cache/error instead of hanging.
    while ([int]$excel.CalculationState -eq 1) {
        if ((Get-Date) -gt $deadline) {
            throw "Excel calculation timed out"
        }
        Start-Sleep -Milliseconds 200
    }
    $workbook.Save()
}
finally {
    if ($null -ne $workbook) {
        $workbook.Close($true)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($workbook)
    }
    if ($null -ne $excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""
        script_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8-sig",
                suffix=".ps1",
                delete=False,
            ) as handle:
                handle.write(script)
                script_path = Path(handle.name)
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    str(destination.resolve()),
                    str(timeout_seconds),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 20,
                check=False,
            )
        except subprocess.TimeoutExpired:
            destination.unlink(missing_ok=True)
            return CalculationAttempt(
                self.name, True, False, "Microsoft Excel 公式重算超时"
            )
        except OSError as exc:
            destination.unlink(missing_ok=True)
            return CalculationAttempt(
                self.name, True, False, f"无法启动 Microsoft Excel: {exc}"
            )
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)

        if completed.returncode != 0:
            destination.unlink(missing_ok=True)
            detail = (completed.stderr or completed.stdout or "未知错误").strip()
            return CalculationAttempt(
                self.name, True, False, f"Microsoft Excel 重算失败: {detail[:300]}"
            )
        return CalculationAttempt(self.name, True, True, "Microsoft Excel 重算完成")


class LibreOfficeEngine(CalculationEngine):
    """Use LibreOffice headless conversion to refresh XLSX formula caches."""

    name = "libreoffice"

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or self._find_executable()

    @staticmethod
    def _find_executable() -> str | None:
        found = shutil.which("soffice") or shutil.which("soffice.exe")
        if found:
            return found
        if os.name == "nt":
            candidates = (
                Path(os.environ.get("ProgramFiles", ""))
                / "LibreOffice"
                / "program"
                / "soffice.exe",
                Path(os.environ.get("ProgramFiles(x86)", ""))
                / "LibreOffice"
                / "program"
                / "soffice.exe",
            )
            for candidate in candidates:
                if candidate.is_file():
                    return str(candidate)
        return None

    def is_available(self, source: Path) -> bool:
        # Converting xlsm through LibreOffice can alter VBA content. Excel COM
        # remains the only automatic calculation backend for macro workbooks.
        return bool(self.executable) and source.suffix.lower() == ".xlsx"

    def recalculate(
        self,
        source: Path,
        destination: Path,
        timeout_seconds: int,
    ) -> CalculationAttempt:
        if not self.executable:
            return CalculationAttempt(self.name, False, False, "LibreOffice 不可用")
        with tempfile.TemporaryDirectory(prefix="sopweave-calc-") as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            profile_dir = root / "profile"
            input_dir.mkdir()
            output_dir.mkdir()
            profile_dir.mkdir()
            workbook = input_dir / "calculation-input.xlsx"
            shutil.copy2(source, workbook)
            command = [
                self.executable,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                "--convert-to",
                "xlsx:Calc MS Excel 2007 XML",
                "--outdir",
                str(output_dir),
                str(workbook),
            ]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return CalculationAttempt(
                    self.name, True, False, "LibreOffice 公式重算超时"
                )
            except OSError as exc:
                return CalculationAttempt(
                    self.name, True, False, f"无法启动 LibreOffice: {exc}"
                )
            output = output_dir / workbook.name
            if completed.returncode != 0 or not output.is_file():
                detail = (completed.stderr or completed.stdout or "未知错误").strip()
                return CalculationAttempt(
                    self.name, True, False, f"LibreOffice 重算失败: {detail[:300]}"
                )
            shutil.copy2(output, destination)
        return CalculationAttempt(self.name, True, True, "LibreOffice 重算完成")


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"snapshot_path", "manifest_path", "reused_manifest"}
    }


def ensure_calculated_workbook(
    snapshot_manifest: dict[str, Any],
    *,
    engines: Sequence[CalculationEngine] | None = None,
    timeout_seconds: int = 120,
    scanner: Callable[[Path], dict[str, Any]] = scan_workbook,
    allow_stale_cache: bool = False,
) -> dict[str, Any]:
    """Return a verified value source or an explicit review requirement."""
    source_value = snapshot_manifest.get("snapshot_path")
    if not source_value:
        raise ValueError("工作簿快照缺少 snapshot_path")
    source = Path(str(source_value)).resolve()
    missing_before = int(snapshot_manifest.get("formula_missing_count") or 0)
    formula_count = int(snapshot_manifest.get("formula_count") or 0)
    error_before = sum(
        int(sheet.get("error_cell_count") or 0)
        for sheet in snapshot_manifest.get("sheets") or []
    )
    properties = snapshot_manifest.get("calculation_properties") or {}
    stale_cache = formula_count > 0 and bool(
        properties.get("fullCalcOnLoad") or properties.get("forceFullCalc")
    )
    if missing_before == 0 and error_before == 0 and not stale_cache:
        return {
            "success": True,
            "requires_review": False,
            "engine": "cached-values",
            "calculated_path": str(source),
            "missing_before": 0,
            "missing_after": 0,
            "formula_count": formula_count,
            "freshness": "stored_cache" if formula_count else "not_applicable",
            "verified_recalculation": False,
            "manifest": _public_manifest(snapshot_manifest),
            "attempts": [],
        }
    if allow_stale_cache and stale_cache and missing_before == 0 and error_before == 0:
        return {
            "success": True,
            "requires_review": False,
            "engine": "cached-values-unverified",
            "calculated_path": str(source),
            "missing_before": 0,
            "missing_after": 0,
            "formula_count": formula_count,
            "freshness": "stored_cache_unverified",
            "verified_recalculation": False,
            "user_acknowledged_stale": True,
            "manifest": _public_manifest(snapshot_manifest),
            "attempts": [],
        }

    configured = (
        list(engines)
        if engines is not None
        else [
            ExcelComEngine(),
            LibreOfficeEngine(),
        ]
    )
    attempts: list[dict[str, Any]] = []
    best_missing = missing_before
    best_errors = error_before
    best_manifest = _public_manifest(snapshot_manifest)
    destination = source.with_name(f"calculated{source.suffix.lower()}")

    for engine in configured:
        available = engine.is_available(source)
        if not available:
            attempts.append(
                asdict(
                    CalculationAttempt(
                        engine=engine.name,
                        available=False,
                        success=False,
                        message=f"{engine.name} 不可用",
                    )
                )
            )
            continue
        candidate = source.with_name(
            f".{engine.name.replace(' ', '-')}-candidate{source.suffix.lower()}"
        )
        candidate.unlink(missing_ok=True)
        attempt = engine.recalculate(source, candidate, timeout_seconds)
        attempt_payload = asdict(attempt)
        if not attempt.success or not candidate.is_file():
            attempts.append(attempt_payload)
            candidate.unlink(missing_ok=True)
            continue
        try:
            rescanned = scanner(candidate)
            missing_after = int(rescanned.get("formula_missing_count") or 0)
            errors_after = sum(
                int(sheet.get("error_cell_count") or 0)
                for sheet in rescanned.get("sheets") or []
            )
        except Exception as exc:  # noqa: BLE001
            attempt_payload.update(
                success=False,
                message=f"重算结果无法验证: {exc}",
            )
            attempts.append(attempt_payload)
            candidate.unlink(missing_ok=True)
            continue
        attempt_payload["missing_after"] = missing_after
        attempt_payload["errors_after"] = errors_after
        attempts.append(attempt_payload)
        if missing_after + errors_after < best_missing + best_errors:
            best_missing = missing_after
            best_errors = errors_after
            best_manifest = _public_manifest(rescanned)
        if missing_after == 0 and errors_after == 0:
            os.replace(candidate, destination)
            return {
                "success": True,
                "requires_review": False,
                "engine": engine.name,
                "calculated_path": str(destination),
                "missing_before": missing_before,
                "missing_after": 0,
                "formula_count": formula_count,
                "freshness": "recalculated",
                "verified_recalculation": True,
                "errors_before": error_before,
                "errors_after": 0,
                "manifest": _public_manifest(rescanned),
                "attempts": attempts,
            }
        candidate.unlink(missing_ok=True)

    destination.unlink(missing_ok=True)
    return {
        "success": False,
        "requires_review": True,
        "engine": None,
        "calculated_path": None,
        "missing_before": missing_before,
        "missing_after": best_missing,
        "formula_count": formula_count,
        "errors_before": error_before,
        "errors_after": best_errors,
        "stale_cache": stale_cache,
        "manifest": best_manifest,
        "attempts": attempts,
        "message": (
            f"检测到 {missing_before} 个缺少缓存值的公式；"
            "当前环境无法生成经过验证的计算结果"
        ),
    }


__all__ = [
    "CalculationAttempt",
    "CalculationEngine",
    "ExcelComEngine",
    "LibreOfficeEngine",
    "ensure_calculated_workbook",
]
