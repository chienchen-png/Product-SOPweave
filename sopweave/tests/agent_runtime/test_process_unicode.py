import asyncio
import tempfile
import unittest
from pathlib import Path
from agent_runtime.execution import ProcessExecutor

class ProcessUnicodeTests(unittest.TestCase):
    def test_isolated_python_preserves_chinese_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            result=asyncio.run(ProcessExecutor(Path(directory)).run('python',"import sys;print('核对完成');print('中文说明',file=sys.stderr)",timeout=5,output_limit=1000))
            self.assertTrue(result['success'],result)
            self.assertIn('核对完成',result['stdout'])
            self.assertIn('中文说明',result['stderr'])
