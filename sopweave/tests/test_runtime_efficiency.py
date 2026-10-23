import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor


class RuntimeEfficiencyTests(unittest.TestCase):
    def test_closing_active_resume_stream_disposes_sdk_execution_before_returning(self):
        from agents.testing import ScriptedModel, ModelStep, assistant_message, function_call
        from tests.agent_runtime.test_run_service import RunServiceTests
        from agent_runtime.service import ResumeCommand
        fixture = RunServiceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.registry.definition.manifest.tools.append('run_process')
        model = ScriptedModel([ModelStep(output=[function_call('run_process', {'program':'python','code':"import time;print('ready',flush=True);time.sleep(1)", 'reason':'test','timeout':5},call_id='process')]),ModelStep(output=[assistant_message('done')])])
        service = fixture._service(model)
        async def scenario():
            events = [event async for event in service.start(fixture._request('close-owned'))]
            approval = next(event.payload for event in events if event.type=='approval.required')
            source = service.resume('close-owned', ResumeCommand(action='approve', interaction_id=approval['interaction_id']))
            async for event in source:
                if event.type == 'tool.output':
                    break
            await source.aclose()
            self.assertFalse(service.is_active('close-owned'))
        asyncio.run(scenario())
    def test_snapshot_uses_one_consistent_source_transaction(self):
        from services.app_data import _copy_project_data_to_tmp
        from tools.project_data_tools import _quote
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'source.db'
            with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(':memory:')) as target:
                source.row_factory = target.row_factory = sqlite3.Row
                source.execute('PRAGMA journal_mode=WAL')
                for db in [source, target]:
                    db.executescript(Path('storage/schema.sql').read_text(encoding='utf-8'))
                source.execute("INSERT INTO project_data_files(id,project_id,source_path,file_name,format,created_at,updated_at) VALUES('f','p','f.xlsx','f.xlsx','xlsx','now','now')")
                for index, name in enumerate(['data_first', 'data_second']):
                    source.execute(f'CREATE TABLE {name}(amount REAL)')
                    source.execute(f'INSERT INTO {name} VALUES(?)', (index+1,))
                    source.execute('INSERT INTO project_data_sheets(file_id,sheet_name,sheet_index,table_name) VALUES(?,?,?,?)', ('f',name,index,name))
                source.commit()
                def concurrent_edit(name):
                    if name == 'data_second':
                        with closing(sqlite3.connect(path)) as other:
                            other.execute('UPDATE data_second SET amount=999')
                            other.commit()
                    return _quote(name)
                with patch('services.app_data._quote', side_effect=concurrent_edit):
                    _copy_project_data_to_tmp(source, target, 'p')
                self.assertEqual(target.execute('SELECT amount FROM data_second').fetchone()[0], 2)
                self.assertEqual(source.execute('SELECT amount FROM data_second').fetchone()[0], 999)
    def test_initial_committed_request_recovers_only_if_execution_never_started(self):
        from agents.testing import ScriptedModel, ModelStep, assistant_message
        from tests.agent_runtime.test_run_service import RunServiceTests
        fixture = RunServiceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        model = ScriptedModel([ModelStep(output=[assistant_message('recovered')])])
        service = fixture._service(model)
        async def scenario():
            source = service.start(fixture._request('initial-gap'))
            self.assertEqual((await anext(source)).type, 'run.created')
            await source.aclose()
            restarted = fixture._service(model)
            await restarted.recover_pending()
            with closing(sqlite3.connect(fixture.db_path)) as db:
                self.assertEqual(db.execute("SELECT status FROM runs WHERE id='initial-gap'").fetchone()[0], 'completed')
            self.assertFalse(service._requests)
        asyncio.run(scenario())
    def test_terminal_runs_and_unknown_cancellations_do_not_accumulate_in_memory(self):
        from agents.testing import ScriptedModel, ModelStep, assistant_message
        from tests.agent_runtime.test_run_service import RunServiceTests
        fixture = RunServiceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        service = fixture._service(ScriptedModel([ModelStep(output=[assistant_message('done')]) for _ in range(3)]))
        async def scenario():
            for i in range(3):
                events = [event async for event in service.start(fixture._request(f'bounded-{i}'))]
                self.assertEqual(events[-1].type, 'run.completed')
                self.assertFalse(service._requests)
            for i in range(10):
                service.cancel(f'unknown-{i}')
            self.assertFalse(service._cancelled)
        asyncio.run(scenario())
    def test_event_writer_recovers_failed_transaction_and_preserves_concurrent_sequences(self):
        from agent_runtime.events import EventWriter
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'db.sqlite'
            with closing(sqlite3.connect(path)) as db:
                db.executescript(Path('storage/schema.sql').read_text(encoding='utf-8'))
                db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON run_events WHEN NEW.event_type='fail' BEGIN SELECT RAISE(ABORT,'expected failure'); END")
                db.commit()
            with EventWriter(path, 'r') as writer:
                with self.assertRaises(sqlite3.IntegrityError):
                    writer.write('fail')
                self.assertEqual(writer.write('ok').seq, 1)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    events = list(pool.map(lambda i: writer.write('delta', {'i':i}), range(30)))
                self.assertEqual(sorted(event.seq for event in events), list(range(2,32)))
    def test_sqlite_write_lock_does_not_block_async_observers(self):
        from agent_runtime.session import WorkbenchSession
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'db.sqlite'
            session = WorkbenchSession('s', db_path=path)
            locked = threading.Event()
            def block():
                with closing(sqlite3.connect(path)) as db:
                    db.execute('BEGIN IMMEDIATE')
                    locked.set()
                    time.sleep(.3)
                    db.rollback()
            worker = threading.Thread(target=block)
            worker.start()
            locked.wait(2)
            async def scenario():
                started = time.monotonic()
                async def observe():
                    await asyncio.sleep(.01)
                    return time.monotonic()-started
                _, elapsed = await asyncio.gather(session.add_items([{'role':'user','content':'x'}]), observe())
                self.assertLess(elapsed, .15)
            try:
                asyncio.run(scenario())
            finally:
                worker.join()

    def test_model_budget_bounds_input_without_truncating_stored_tool_pairs(self):
        from agent_runtime.session import WorkbenchSession
        with tempfile.TemporaryDirectory() as directory:
            session = WorkbenchSession('s', db_path=Path(directory)/'db.sqlite')
            items = []
            for i in range(300):
                items.extend([{'role':'user','content':f'question-{i}'}, {'type':'function_call','call_id':str(i),'name':'read_text','arguments':'{}'}, {'type':'function_call_output','call_id':str(i),'output':'x'*1000}])
            asyncio.run(session.add_items(items))
            from agent_runtime.context_budget import ContextBudget
            history = asyncio.run(session.get_items())
            self.assertEqual(history, items)
            budget = ContextBudget(100000)
            selection = budget.select(history)
            selected = selection.items
            self.assertGreater(selection.omitted_items, 0)
            self.assertLessEqual(selection.estimated_tokens, budget.effective_budget)
            self.assertEqual(selected[-1]['call_id'], '299')
            calls = {item['call_id'] for item in selected if item.get('type')=='function_call'}
            outputs = {item['call_id'] for item in selected if item.get('type')=='function_call_output'}
            self.assertEqual(calls, outputs)
            self.assertEqual(selected[0]['role'], 'user')

    def test_search_prunes_ignored_directories_before_enumerating_them(self):
        from agent_runtime.context import WorkbenchRunContext
        from agent_runtime.tools.core import SearchText, search_text
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            excluded = root / 'node_modules'
            excluded.mkdir()
            (excluded/'dependency.txt').write_text('needle', encoding='utf-8')
            (root/'source.txt').write_text('needle', encoding='utf-8')
            context = WorkbenchRunContext(project_id='p',work_dir=root,run_id='r',conversation_id='c',work_mode='auto',agent_id='general-agent')
            scanned = []
            original = os.scandir
            def record(path):
                scanned.append(Path(path))
                return original(path)
            with patch('os.scandir', side_effect=record):
                result = search_text(context, SearchText(text='needle'))
            self.assertEqual(len(result['matches']), 1)
            self.assertNotIn(excluded, scanned)

    def test_static_3d_output_does_not_generate_unused_interactive_payload(self):
        from tools.finmod_py_charts import render_py_chart
        result = render_py_chart('bar3d', {'series':[{'data':[[1,2],[2,3],[3,4]]}]})
        self.assertTrue(result['success'], result)
        self.assertTrue(result['b64'])
        self.assertFalse('fig_json' in result, 'static rendering must not create a second interactive figure')
