from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class NoLegacyRuntimeTests(unittest.TestCase):
    def test_legacy_runtime_files_and_dependency_are_removed(self) -> None:
        legacy = [
            "agent/smol_engine.py", "agent/smol_model.py", "agent/smol_bridge.py",
            "agent/smol_tools.py", "agent/core.py", "agent/llm_client.py",
            "agent/tool_registry.py", "agent/ask_user_tool.py", "agent/agent_config.py",
            "agent/agent_events.py", "agent/context_manager.py", "agent/skill_loader.py",
            "agent/tool_titles.py", "agent/approval/approval_legacy.py",
            "storage/agent_store.py",
        ]
        self.assertEqual([path for path in legacy if (ROOT / path).exists()], [])
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?m)^\s*smolagents(?:\W|$)", requirements))

    def test_production_sources_have_no_legacy_imports_or_polling_client(self) -> None:
        python_import = re.compile(
            r"(?m)^\s*(?:from|import)\s+(?:smolagents|agent\.(?:smol_engine|smol_model|"
            r"smol_bridge|smol_tools|core|llm_client|tool_registry))(?:\W|$)"
        )
        typescript_legacy = re.compile(
            r"\bpollRunEvents\b|engine\s*:\s*[\"']smol[\"']"
        )
        offenders = []
        for base, suffixes in ((ROOT / "agent", {".py"}), (ROOT / "agent_runtime", {".py"}),
                               (ROOT / "routes", {".py"}), (ROOT / "frontend" / "src", {".ts", ".tsx"})):
            for path in base.rglob("*"):
                if path.suffix not in suffixes:
                    continue
                source = path.read_text(encoding="utf-8")
                pattern = python_import if path.suffix == ".py" else typescript_legacy
                if pattern.search(source):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_runtime_skill_roots_do_not_include_github_compatibility_copy(self) -> None:
        source = (ROOT / "agent_runtime/definitions/agent_registry.py").read_text(encoding="utf-8")
        self.assertNotIn('BASE_DIR / ".github" / "skills"', source)
        self.assertFalse((ROOT / ".github" / "skills" / "data-reconcile").exists())
        self.assertFalse((ROOT / ".github" / "skills" / "financial-modeling").exists())


if __name__ == "__main__":
    unittest.main()
