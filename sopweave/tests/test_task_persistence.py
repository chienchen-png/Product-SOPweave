from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


class TaskPersistenceTests(unittest.TestCase):
    def test_task_state_survives_restart_and_interrupted_writes_are_not_retried(self):
        from storage.task_store import TaskStore
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'tasks.db'
            store = TaskStore(path)
            store.put('review', {'status':'needs_anchor','project_id':'p','path':'data.xlsx','anchor':{'sheets':['Sheet']}})
            store.put('writing', {'status':'running','project_id':'p'})
            restarted = TaskStore(path)
            self.assertEqual(restarted.get('review')['path'], 'data.xlsx')
            with patch('storage.task_store.process_alive', return_value=False):
                restarted.recover_interrupted()
            self.assertEqual(restarted.get('writing')['status'], 'failed')
            self.assertEqual(restarted.get('writing')['error_type'], 'interrupted')
            self.assertTrue(restarted.transition('review', {'needs_anchor'}, {'status':'running'}))
            self.assertFalse(store.transition('review', {'needs_anchor'}, {'status':'running'}))
            from flask import Flask
            from routes.project_data import project_data_bp
            app = Flask(__name__)
            app.config['TASK_DB_PATH'] = path
            app.register_blueprint(project_data_bp)
            client = app.test_client()
            status = client.get('/api/project-data/import/status?import_id=writing')
            self.assertEqual(status.get_json()['data']['error_type'], 'interrupted')
            store.put('review-http', {'kind':'import','status':'needs_anchor','project_id':'p','path':'data.xlsx'})
            with patch('routes.project_data.threading.Thread') as thread:
                first = client.post('/api/project-data/import/anchor', json={'import_id':'review-http','anchors':{'Sheet':0}})
                repeated = client.post('/api/project-data/import/anchor', json={'import_id':'review-http','anchors':{'Sheet':0}})
                self.assertEqual(first.status_code, 200)
                self.assertIn(repeated.status_code, (400,409))
                self.assertEqual(thread.call_count, 1)

    def test_tmp_snapshot_and_audit_decisions_reload_without_process_registry(self):
        import storage.tmp_db as module
        with tempfile.TemporaryDirectory() as directory, patch.object(module, 'TMP_RUNS_DIR', Path(directory)):
            run = module.TmpRun.create('reload-test')
            module.register_run(run.run_id, run)
            run.db.execute('CREATE TABLE data_example(value TEXT)')
            run.db.execute("INSERT INTO data_example VALUES('保留结果')")
            run.decisions['base:A'] = '确认'
            run.commit()
            module.unregister_run(run.run_id)
            run.db.close()
            restored = module.get_run(run.run_id)
            self.assertIsNotNone(restored)
            try:
                self.assertEqual(restored.db.execute('SELECT value FROM data_example').fetchone()[0], '保留结果')
                self.assertEqual(restored.decisions['base:A'], '确认')
                self.assertIsNone(module.get_run('../escape'))
            finally:
                module.unregister_run(run.run_id)
                if restored:
                    restored.cleanup()

    def test_formula_review_can_resume_once_with_an_explicit_strategy(self):
        from flask import Flask
        from routes.project_data import project_data_bp
        from storage.task_store import TaskStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.db"
            store = TaskStore(path)
            store.put(
                "formula-review",
                {
                    "kind": "import",
                    "status": "needs_review",
                    "project_id": "p",
                    "path": "data.xlsx",
                    "anchor": {
                        "reason": "formula_calculation_required",
                        "calculation": {
                            "stale_cache": True,
                            "missing_after": 0,
                            "errors_after": 0,
                        },
                    },
                },
            )
            app = Flask(__name__)
            app.config["TASK_DB_PATH"] = path
            app.register_blueprint(project_data_bp)
            client = app.test_client()
            with patch("routes.project_data.threading.Thread") as thread, patch(
                "routes.project_data._project",
                return_value={"id": "p", "work_dir": directory},
            ):
                first = client.post(
                    "/api/project-data/import/calculation",
                    json={"import_id": "formula-review", "strategy": "cached"},
                )
                repeated = client.post(
                    "/api/project-data/import/calculation",
                    json={"import_id": "formula-review", "strategy": "cached"},
                )
            self.assertEqual(first.status_code, 200, first.get_json())
            self.assertIn(repeated.status_code, (400, 409))
            self.assertEqual(thread.call_count, 1)
            resumed = store.get("formula-review")
            self.assertEqual(resumed["status"], "running")
            self.assertEqual(resumed["formula_strategy"], "cached")

            store.put(
                "invalid-strategy",
                {
                    "kind": "import",
                    "status": "needs_review",
                    "project_id": "p",
                    "path": "data.xlsx",
                    "anchor": {"reason": "formula_calculation_required", "calculation": {}},
                },
            )
            invalid = client.post(
                "/api/project-data/import/calculation",
                json={"import_id": "invalid-strategy", "strategy": "unsafe"},
            )
            self.assertEqual(invalid.status_code, 400, invalid.get_json())
