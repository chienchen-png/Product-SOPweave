from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AppOptionsTests(unittest.TestCase):
    def test_fresh_app_options_uses_current_model_store_without_legacy_presets(self):
        with tempfile.TemporaryDirectory(prefix="SOP options ") as folder:
            script = """
from app import create_app
from storage.db import get_db
from storage.model_store import ModelStore
app=create_app()
for path in ['/projects/example', '/settings/models', '/settings/agent', '/apps/reconcile', '/apps/financial-modeling', '/sops']:
    result=app.test_client().get(path)
    assert result.status_code in (200,503), (path,result.status_code)
    assert result.status_code != 404
for path in ['/ai-config', '/settings/skills', '/tasks']:
    assert app.test_client().get(path).status_code == 404, path
with app.app_context():
    store=ModelStore(get_db())
    provider=store.create_provider({'name':'local','api_url':'http://127.0.0.1:1/v1','api_key':'synthetic','status':'available'})
    model=store.create_model(provider['id'],{'name':'options-test','type':'chat','status':'available'})
response=app.test_client().get('/api/options')
assert response.status_code==200, response.get_json()
body=response.get_json()['data']
assert any(m['id']==model['id'] for m in body['models']), body
assert all('api_key' not in m for m in body['models'])
assert body['agents']
"""
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env={
                    **os.environ,
                    "SOPWEAVE_DATA_DIR": folder,
                    "SOPWEAVE_USER_SKILLS_ENABLED": "1",
                },
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
