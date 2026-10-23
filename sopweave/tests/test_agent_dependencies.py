from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentDependencyTests(unittest.TestCase):
    def test_agent_runtime_versions_are_pinned_in_the_lock_file(self) -> None:
        lock_text = (ROOT / "requirements-agent.lock").read_text(encoding="utf-8")

        for requirement in (
            "openai-agents==0.23.1",
            "fastapi==0.143.0",
            "uvicorn==0.54.0",
            "pyyaml==6.0.3",
        ):
            self.assertRegex(lock_text.lower(), rf"(?m)^{re.escape(requirement)}(?:\s|\\|$)")

        pinned_lines = [
            line for line in lock_text.splitlines()
            if line and not line.startswith((" ", "#", "--"))
        ]
        self.assertTrue(pinned_lines)
        self.assertTrue(all("==" in line for line in pinned_lines))
        self.assertIn("--hash=sha256:", lock_text)

    def test_primary_requirements_enable_the_agent_runtime(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()

        for package in ("openai-agents", "fastapi", "uvicorn", "pyyaml"):
            self.assertRegex(requirements, rf"(?m)^{re.escape(package)}(?:==|>=)")

    def test_startup_checks_agent_and_asgi_imports_from_relative_paths(self) -> None:
        script = (ROOT / "scripts" / "start.bat").read_text(encoding="utf-8")
        lowered = script.lower()
        check = (ROOT / "scripts" / "check_runtime.py").read_text(encoding="utf-8").lower()

        self.assertIn('cd /d "%~dp0.."', lowered)
        self.assertNotRegex(lowered, r"[a-z]:\\sopweave")
        self.assertIn("scripts\\check_runtime.py", lowered)
        self.assertRegex(check, r"import[^\r\n]*agents")
        self.assertRegex(check, r"import[^\r\n]*fastapi")
        self.assertRegex(check, r"import[^\r\n]*uvicorn")
        self.assertIn("runtime check passed", check)

    def test_startup_check_finds_an_eligible_system_python(self) -> None:
        completed = subprocess.run(
            ["cmd", "/c", r"SOPweave.bat", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("Runtime check passed", completed.stdout, completed.stdout + completed.stderr)

    def test_offline_builder_requires_binary_wheels_and_fails_fast(self) -> None:
        script = (ROOT / "scripts" / "build_agent_offline_bundle.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("--only-binary=:all:", script)
        self.assertIn("requirements.lock", script)
        self.assertRegex(script, r"(?i)exit\s+\$LASTEXITCODE|throw")


if __name__ == "__main__":
    unittest.main()
