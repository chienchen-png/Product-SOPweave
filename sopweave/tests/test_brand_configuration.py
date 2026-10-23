from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BrandConfigurationTests(unittest.TestCase):
    def _read_config(self, extra: dict[str, str]) -> dict:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("SOPWEAVE_", "FINANCE_"))
        }
        environment.update(extra)
        script = """
import json
from config.settings import (
    APP_NAME, ANALYSIS_MAX_CELLS, DATA_DIR, IMPORT_MAX_CELLS,
)
print(json.dumps({
    "app_name": APP_NAME,
    "data_dir": str(DATA_DIR),
    "import_max_cells": IMPORT_MAX_CELLS,
    "analysis_max_cells": ANALYSIS_MAX_CELLS,
}))
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_sopweave_names_and_environment_variables_are_primary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            values = self._read_config(
                {
                    "SOPWEAVE_DATA_DIR": directory,
                    "FINANCE_DATA_DIR": str(Path(directory) / "legacy"),
                    "SOPWEAVE_IMPORT_MAX_CELLS": "123",
                    "FINANCE_IMPORT_MAX_CELLS": "456",
                    "SOPWEAVE_ANALYSIS_MAX_CELLS": "321",
                    "FINANCE_ANALYSIS_MAX_CELLS": "654",
                }
            )
        self.assertEqual(values["app_name"], "SOPweave")
        self.assertEqual(Path(values["data_dir"]), Path(directory).resolve())
        self.assertEqual(values["import_max_cells"], 123)
        self.assertEqual(values["analysis_max_cells"], 321)

    def test_pre_release_environment_aliases_still_migrate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            values = self._read_config(
                {
                    "FINANCE_DATA_DIR": directory,
                    "FINANCE_IMPORT_MAX_CELLS": "456",
                    "FINANCE_ANALYSIS_MAX_CELLS": "654",
                }
            )
        self.assertEqual(Path(values["data_dir"]), Path(directory).resolve())
        self.assertEqual(values["import_max_cells"], 456)
        self.assertEqual(values["analysis_max_cells"], 654)

    def test_public_brand_surfaces_use_sopweave(self) -> None:
        root = ROOT.parent
        paths = [
            root / "README.md",
            root / "CONTRIBUTING.md",
            root / "SECURITY.md",
            ROOT / "README.md",
            ROOT / "frontend" / "index.html",
            ROOT / "frontend" / "src" / "layout" / "NavSidebar.tsx",
            ROOT / "SOPweave.bat",
            *list((ROOT / "docs").glob("*.md")),
        ]
        forbidden = (
            "Finance Recon Agent",
            "Finance Agent Workbench",
            "finance-agent-workbench",
            "finance-agent-frontend",
            "finance-workbench-",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertTrue(all(value not in text for value in forbidden))
        self.assertIn("SOPweave", (ROOT / "frontend" / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
