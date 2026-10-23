from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path


class CoreExecutionTests(unittest.TestCase):
    def test_ambiguous_replacement_leaves_source_and_version_unchanged(self):
        from types import SimpleNamespace
        from agent_runtime.tools.core import EditText, edit_text
        from agent_runtime.workspace import file_version

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "重复.txt"
            path.write_text("金额100；金额100", encoding="utf-8")
            version = file_version(path)
            with self.assertRaisesRegex(ValueError, "唯一匹配"):
                edit_text(SimpleNamespace(work_dir=root), EditText(
                    path=path.name, old_text="金额100", new_text="金额200",
                    expected_version=version,
                ))
            self.assertEqual(path.read_text(encoding="utf-8"), "金额100；金额100")
            self.assertEqual(file_version(path), version)

    def test_shell_preserves_chinese_output_working_directory_and_exit_code(self):
        from agent_runtime.execution import ProcessExecutor
        import os

        async def execute(root):
            code = "echo 中文验收 & cd & exit /b 7" if os.name == "nt" else "printf '中文验收\\n'; pwd; exit 7"
            result = await ProcessExecutor(root).run(
                "shell", code, timeout=5, output_limit=1024,
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["exit_code"], 7)
            self.assertIn("中文验收", result["stdout"])
            self.assertIn(str(root), result["stdout"])
            self.assertEqual(result["stderr"], "")

        with tempfile.TemporaryDirectory(prefix="命令 空格 ") as directory:
            asyncio.run(execute(Path(directory).resolve()))

    def test_version_conflict_and_path_escape_do_not_change_files(self):
        from agent_runtime.tools.core import WriteText, EditText, write_text, edit_text
        from tests.agent_runtime.test_tool_catalog import ToolCatalogTests

        fixture = ToolCatalogTests()
        fixture.setUp()
        try:
            context = fixture.context
            result = write_text(
                context,
                WriteText(
                    path="中文 空格/a.txt",
                    content="one two",
                    expected_version="missing",
                ),
            )
            edit_text(
                context,
                EditText(
                    path="中文 空格/a.txt",
                    old_text="two",
                    new_text="three",
                    expected_version=result["version"],
                ),
            )
            with self.assertRaises(ValueError):
                edit_text(
                    context,
                    EditText(
                        path="中文 空格/a.txt",
                        old_text="one",
                        new_text="bad",
                        expected_version=result["version"],
                    ),
                )
            with self.assertRaises(ValueError):
                write_text(
                    context,
                    WriteText(
                        path="../escape.txt", content="bad", expected_version="missing"
                    ),
                )
            self.assertEqual(
                (context.work_dir / "中文 空格/a.txt").read_text(), "one three"
            )
        finally:
            fixture.doCleanups()

    def test_output_budget_timeout_and_cancel(self):
        from agent_runtime.execution import ProcessExecutor

        async def execute(root):
            executor = ProcessExecutor(root)
            output = await executor.run(
                "python", "print('x'*20000)", timeout=5, output_limit=1024
            )
            self.assertTrue(output["truncated"])
            self.assertLessEqual(len(output["stdout"]), 1024)
            result = await executor.run(
                "python", "import time; time.sleep(30)", timeout=0.2, output_limit=1024
            )
            self.assertEqual(result["error_code"], "process_timeout")
            task = asyncio.create_task(
                executor.run(
                    "python",
                    "import time; time.sleep(30)",
                    timeout=30,
                    output_limit=1024,
                )
            )
            await asyncio.sleep(0.2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            child = "import time,pathlib;time.sleep(1);pathlib.Path('orphan.txt').write_text('bad')"
            code = f'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",{child!r}]);time.sleep(30)'
            await executor.run("python", code, timeout=0.3, output_limit=1024)
            await asyncio.sleep(1.1)
            self.assertFalse((root / "orphan.txt").exists())

        with tempfile.TemporaryDirectory(prefix="执行 空格 ") as directory:
            asyncio.run(execute(Path(directory)))


if __name__ == "__main__":
    unittest.main()
