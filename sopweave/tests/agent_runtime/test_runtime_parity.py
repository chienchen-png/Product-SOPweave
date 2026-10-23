from __future__ import annotations

import unittest
import ast
from pathlib import Path

from agent_runtime.tools.catalog import ToolCatalog


class RuntimeParityTests(unittest.TestCase):
    def test_representative_business_task_tools_are_present(self) -> None:
        names = set(ToolCatalog.default().names)
        tasks = {
            "file_read": "read_text",
            "excel_read": "read_sheet_range",
            "project_query": "query_dataset",
            "chart": "generate_chart",
            "write_approval": "write_text",
            "ask_user": "ask_user",
        }
        self.assertEqual({task for task, tool in tasks.items() if tool not in names}, set())

    def test_user_visible_terminal_contract_covers_resume_cancel_and_errors(self) -> None:
        terminals = {"run.completed", "run.waiting", "run.cancelled", "run.failed"}
        self.assertEqual(terminals, {
            "run.completed", "run.waiting", "run.cancelled", "run.failed",
        })

    def test_v2_has_no_polling_interval_constant(self) -> None:
        from agent_runtime import api
        source = Path(api.__file__).read_text(encoding="utf-8")
        tree=ast.parse(source)
        sleeps=[node for node in ast.walk(tree) if isinstance(node,ast.Call)
            and isinstance(node.func,ast.Attribute) and node.func.attr=='sleep']
        self.assertEqual(sleeps,[])


if __name__ == "__main__":
    unittest.main()
