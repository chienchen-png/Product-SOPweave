"""Copy current source to a fresh Windows path and verify runtime behavior."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    excluded = {
        ".git",
        ".runtime",
        ".venv",
        "node_modules",
        "__pycache__",
        ".ruff_cache",
        ".pytest_cache",
        ".test-build",
        "data",
        "offline_packages",
    }
    with tempfile.TemporaryDirectory(prefix="智能体验收 空格 ") as temporary:
        target = Path(temporary).resolve() / "复制项目"
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(*excluded))
        # The installed interpreter supplies dependencies; the copy must supply
        # all code, definitions, skills and frontend production assets itself.
        subprocess.run(
            [sys.executable, "scripts/check_runtime.py"], cwd=target, check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                "tests.test_path_portability",
                "tests.agent_runtime.test_progressive_tools",
                "tests.agent_runtime.test_dataset_lifecycle",
                "tests.agent_runtime.test_office_tools",
                "tests.agent_runtime.test_interactions",
                "tests.agent_runtime.test_core_execution",
            ],
            cwd=target,
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path;assert Path('frontend/dist/index.html').is_file()",
            ],
            cwd=target,
            check=True,
        )
        print(
            "Portable source, runtime, skills, Office and interactions: passed",
            flush=True,
        )


if __name__ == "__main__":
    main()
