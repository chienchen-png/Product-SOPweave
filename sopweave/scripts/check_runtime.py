"""Validate the supported local runtime against the complete distribution lock."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import struct
import sys
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.dependency_lock import parse_dependency_lock


def dependency_errors(lock_text: str, installed=version) -> list[str]:
    errors = []
    for dependency in parse_dependency_lock(lock_text):
        name, expected = dependency.name, dependency.version
        try:
            actual = installed(name)
        except PackageNotFoundError:
            errors.append(f"{name}: missing (requires {expected})")
        else:
            if actual != expected:
                errors.append(f"{name}: {actual} (requires {expected})")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dependencies-only", action="store_true")
    args = parser.parse_args()
    errors = []
    if (
        sys.version_info[:2] != (3, 14)
        or struct.calcsize("P") != 8
        or sys.platform != "win32"
    ):
        errors.append("Supported runtime: Windows x64, CPython 3.14")
    errors.extend(
        dependency_errors((ROOT / "requirements.lock").read_text(encoding="utf-8"))
    )
    if errors:
        print("\n".join(errors))
        return 1
    if not args.dependencies_only:
        if not (ROOT / "frontend/dist/index.html").is_file():
            print("Frontend missing: run npm ci and npm run build in frontend")
            return 1
        # Check imports and the ASGI factory in ONE disposable process. Loading
        # the same scientific stack here and again in a child doubles peak memory.
        probe = (
            "import flask, openpyxl, docx, agents, fastapi, uvicorn, yaml; "
            "import pandas, numpy, scipy, matplotlib, numpy_financial, xlrd, xlwt, xlutils; "
            "import asgi; assert asgi.app"
        )
        # This is a startup probe, not a numerical workload. OpenBLAS/OMP worker
        # stacks can exhaust Windows commit memory before the check even runs.
        # Restrict only this child; preserve the user's application settings.
        with tempfile.TemporaryDirectory(prefix="sopweave-runtime-check-") as directory:
            try:
                checked = subprocess.run(
                    [sys.executable, "-c", probe],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "SOPWEAVE_DATA_DIR": directory,
                        "OPENBLAS_NUM_THREADS": "1",
                        "OMP_NUM_THREADS": "1",
                    },
                    check=False,
                    timeout=90,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                print(f"Runtime check failed: {error}")
                return 1
            if checked.returncode:
                print(f"Runtime check failed (process exit {checked.returncode})")
                # cmd.exe's `if errorlevel 1` does not catch negative NTSTATUS
                # crash codes. Normalize to a portable nonzero failure status.
                return 1
        print("Runtime check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
