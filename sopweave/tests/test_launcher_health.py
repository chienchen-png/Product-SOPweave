"""The native launcher must identify its own ready installation."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LauncherHealthTests(unittest.TestCase):
    def test_health_identifies_installation_without_exposing_local_configuration(self):
        with tempfile.TemporaryDirectory(prefix="SOP launcher health ") as folder:
            result = subprocess.run(
                [sys.executable, "-c", """
from unittest.mock import patch
from pathlib import Path
from app import create_app
app = create_app()
client = app.test_client()
import os
ready = Path(os.environ['SOPWEAVE_DATA_DIR']) / 'health-frontend'
ready.mkdir()
(ready / 'index.html').write_text('<html></html>', encoding='utf-8')
with patch('app.FRONTEND_DIST_DIR', ready):
    response = client.get('/api/health')
assert response.status_code == 200, response.status_code
body = response.get_json()
assert body['success'] is True
assert body['data']['product'] == 'sopweave'
assert body['data']['status'] == 'ready'
assert body['data']['protocol'] == 1
assert len(body['data']['installation']) == 64
assert body['data']['pid'] > 0
assert 'api_key' not in response.text and 'work_dir' not in response.text
assert str(Path.cwd()) not in response.text
with patch('app.FRONTEND_DIST_DIR', Path('missing-frontend-probe')):
    failed = client.get('/api/health')
    assert failed.status_code == 503
    assert failed.get_json()['data']['status'] == 'missing_frontend'
"""],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "SOPWEAVE_DATA_DIR": folder},
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
