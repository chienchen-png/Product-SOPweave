import sqlite3
import tempfile
import unittest
from pathlib import Path
from flask import Flask
from unittest.mock import patch
from storage.project_store import ProjectStore
from routes.workbench import workbench_bp


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=sqlite3.connect(Path(self.temp.name)/'app.db');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        self.db.executescript(Path('storage/schema.sql').read_text(encoding='utf-8'))
        self.recon=ProjectStore(self.db).create('__app_recon__',str(Path(self.temp.name)/'recon'))
        self.finmod=ProjectStore(self.db).create('__app_finmod__',str(Path(self.temp.name)/'finmod'))
        self.app=Flask(__name__);self.app.register_blueprint(workbench_bp)
        self.patch=patch('routes.workbench.get_db',return_value=self.db);self.patch.start();self.addCleanup(self.patch.stop)
        self.client=self.app.test_client()

    def test_drafts_compare_revisions_and_isolate_project_and_kind(self):
        path=f"/api/workbench/drafts/{self.recon['id']}/recon"
        self.assertEqual(self.client.get(path).get_json()['data']['revision'],0)
        body={'expected_revision':0,'state':{'params':{'tolerance':0.01}}}
        self.assertEqual(self.client.put(path,json=body).status_code,200)
        self.assertEqual(self.client.put(path,json=body).status_code,409)
        self.assertEqual(self.client.get(path).get_json()['data']['state']['params']['tolerance'],0.01)
        other=f"/api/workbench/drafts/{self.finmod['id']}/finmod"
        self.assertEqual(self.client.get(other).get_json()['data']['revision'],0)
        self.assertEqual(self.client.get(path.replace('/recon','/finmod')).status_code,422)
        self.assertEqual(self.client.put(path,json={'expected_revision':1,'state':{'nested':{'api_key':'synthetic'}}}).status_code,422)
        self.assertEqual(self.client.get('/api/workbench/drafts/missing/recon').status_code,404)

    def test_application_results_and_reports_use_existing_runs_and_events(self):
        from storage.workbench_store import WorkbenchStore
        store=WorkbenchStore(self.db)
        store.record_result('recon','sample-run',self.recon['id'],{'baseFile':'账单.xlsx'},{'report':'# 核对报告','reconData':{'summary':{'ok':2},'diffs':[]}})
        page=self.client.get('/api/workbench/tasks').get_json()['data']
        self.assertEqual(page['tasks'][0]['run_id'],'sample-run')
        detail=self.client.get('/api/workbench/tasks/sample-run').get_json()['data']
        self.assertEqual(detail['result']['reconData']['summary']['ok'],2)
        self.assertEqual(self.db.execute("SELECT status FROM runs WHERE id='sample-run'").fetchone()[0],'completed')
        self.assertEqual(self.client.get('/api/workbench/tasks?project_id='+self.finmod['id']).get_json()['data']['tasks'],[])
        ProjectStore(self.db).delete(self.recon['id'])
        self.assertEqual(self.client.get('/api/workbench/tasks/sample-run').status_code,404)

    def test_report_creation_preserves_user_edits_and_uses_project_owned_relative_paths(self):
        from storage.workbench_store import WorkbenchStore
        from services.workbench import persist_report
        store=WorkbenchStore(self.db)
        store.record_result('recon','report-run',self.recon['id'],{}, {'report':'# 初稿'})
        report=persist_report(self.db,self.recon['id'],'report-run','# 初稿')
        path=Path(self.recon['work_dir'])/report['rel_path']
        self.assertTrue(path.is_file());self.assertFalse(Path(report['rel_path']).is_absolute())
        path.write_text('# 用户已修改',encoding='utf-8')
        persist_report(self.db,self.recon['id'],'report-run','# 初稿')
        self.assertEqual(path.read_text(encoding='utf-8'),'# 用户已修改')

    def test_application_duplicate_execution_is_blocked_and_completed_run_releases_it(self):
        from storage.workbench_store import WorkbenchStore
        from services.results import ServiceError
        store=WorkbenchStore(self.db)
        store.start_result('recon','one',self.recon['id'],{})
        with self.assertRaises(ServiceError):store.start_result('recon','two',self.recon['id'],{})
        store.record_result('recon','one',self.recon['id'],{}, {})
        store.start_result('recon','two',self.recon['id'],{})

    def test_legacy_browser_records_are_imported_as_unverified_notes_and_are_idempotent(self):
        body={'project_id':self.recon['id'],'kind':'recon','records':[{'id':'old','params':{'tolerance':0.01},'report':'旧报告：匹配 999 条'}]}
        response=self.client.post('/api/workbench/legacy-history',json=body)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['data']['imported'],1)
        self.client.post('/api/workbench/legacy-history',json=body)
        tasks=self.client.get('/api/workbench/tasks').get_json()['data']['tasks']
        self.assertEqual(len(tasks),1);self.assertEqual(tasks[0]['status'],'legacy')
        detail=self.client.get('/api/workbench/tasks/'+tasks[0]['run_id']).get_json()['data']
        self.assertIsNone(detail['result']);self.assertEqual(detail['params']['tolerance'],0.01)
        self.assertEqual(detail['legacy_note']['report'],'旧报告：匹配 999 条')

    def test_chart_saving_is_not_a_report_and_markdown_export_is_discoverable(self):
        from storage.workbench_store import WorkbenchStore
        from services.finmod_reports import finmod_save_chart,finmod_export_md
        from storage.tmp_db import TmpRun,register_run,unregister_run
        run=TmpRun.create();register_run(run.run_id,run)
        self.addCleanup(run.cleanup);self.addCleanup(unregister_run,run.run_id)
        WorkbenchStore(self.db).record_result('finmod',run.run_id,self.finmod['id'],{}, {})
        with patch('services.finmod_reports.get_db',return_value=self.db),patch('services.app_data.get_db',return_value=self.db),patch('services.finmod_reports._build_llm_client',return_value=None):
            image=finmod_save_chart({'project_id':self.finmod['id'],'run_id':run.run_id,'filename':'chart.svg','content':'<svg xmlns="http://www.w3.org/2000/svg"/>','format':'svg'})
            self.assertEqual(image.status_code,200)
            self.assertEqual(WorkbenchStore(self.db).reports(),[])
            report=finmod_export_md({'project_id':self.finmod['id'],'run_id':run.run_id,'goal':'人工验收'})
            self.assertEqual(report.status_code,200)
            reports=WorkbenchStore(self.db).reports(self.finmod['id'])
            self.assertEqual(len(reports),1);self.assertEqual(reports[0]['rel_path'],report.data['rel_path'])

    def test_concurrent_draft_writers_cannot_overwrite_each_other(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import closing
        from storage.workbench_store import WorkbenchStore
        from services.results import ServiceError
        def write(number):
            with closing(sqlite3.connect(Path(self.temp.name)/'app.db',timeout=10)) as db:
                db.row_factory=sqlite3.Row
                try:WorkbenchStore(db).save_draft(self.recon['id'],'recon',0,{'writer':number});return 200
                except ServiceError as cause:return cause.status_code
        with ThreadPoolExecutor(max_workers=12) as pool:statuses=list(pool.map(write,range(24)))
        self.assertEqual(statuses.count(200),1)
        self.assertEqual(statuses.count(409),23)
        self.assertEqual(WorkbenchStore(self.db).get_draft(self.recon['id'],'recon')['revision'],1)
