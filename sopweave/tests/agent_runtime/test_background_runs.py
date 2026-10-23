import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from agent_runtime.background import BackgroundRuns
from agent_runtime.events import EventWriter
from storage.run_store import RunStore


class BackgroundRunTests(unittest.TestCase):
    def test_many_subscribers_share_one_execution_and_receive_ordered_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'app.db'
            with closing(sqlite3.connect(path)) as db:db.executescript(Path('storage/schema.sql').read_text(encoding='utf-8'))
            class Service:
                def cancel(self,_identifier):raise AssertionError('subscriber must not cancel execution')
            class Request:
                async def is_disconnected(self):return False
            async def scenario():
                starts=0;runs=BackgroundRuns(Service(),path);gate=asyncio.Event()
                async def source():
                    nonlocal starts
                    starts+=1
                    with closing(sqlite3.connect(path)) as db:
                        db.row_factory=sqlite3.Row;RunStore(db).create_run('shared')
                    async with EventWriter(path,'shared') as writer:
                        yield await writer.awrite('run.created',{})
                        await gate.wait()
                        for index in range(30):yield await writer.awrite('message.delta',{'delta':str(index)})
                        with closing(sqlite3.connect(path)) as db:
                            db.row_factory=sqlite3.Row;RunStore(db).update_run_status('shared','completed')
                        yield await writer.awrite('run.completed',{})
                await runs.start('shared',source())
                async def collect():return [event.seq async for event in runs.events('shared',Request()) if event]
                readers=[asyncio.create_task(collect()) for _ in range(24)]
                gate.set();received=await asyncio.gather(*readers)
                self.assertEqual(starts,1)
                self.assertTrue(all(sequence==list(range(1,33)) for sequence in received))
                await runs.shutdown()
            asyncio.run(scenario())
    def test_disconnecting_a_subscriber_does_not_cancel_execution_and_replay_uses_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'app.db'
            with closing(sqlite3.connect(path)) as db:db.executescript(Path('storage/schema.sql').read_text(encoding='utf-8'))
            class Service:
                cancelled=[]
                def cancel(self,identifier):self.cancelled.append(identifier)
            class Request:
                def __init__(self,disconnected):self.disconnected=disconnected
                async def is_disconnected(self):return self.disconnected
            async def scenario():
                gate=asyncio.Event();service=Service();runs=BackgroundRuns(service,path)
                async def source():
                    with closing(sqlite3.connect(path)) as db:
                        db.row_factory=sqlite3.Row;RunStore(db).create_run('test')
                    with EventWriter(path,'test') as writer:
                        yield writer.write('run.created',{})
                        await gate.wait()
                        yield writer.write('message.delta',{'delta':'完成'})
                        with closing(sqlite3.connect(path)) as db:
                            db.row_factory=sqlite3.Row;RunStore(db).update_run_status('test','completed')
                        yield writer.write('run.completed',{})
                await runs.start('test',source())
                self.assertEqual([event async for event in runs.events('test',Request(True),0)],[])
                gate.set()
                await runs.tasks['test']
                replay=[event async for event in runs.events('test',Request(False),1)]
                self.assertEqual([e.type for e in replay if e],['message.delta','run.completed'])
                self.assertEqual(service.cancelled,[])
                await runs.shutdown()
            asyncio.run(scenario())

    def test_sdk_approval_survives_background_subscription_and_resume(self):
        from tests.agent_runtime.test_run_service import RunServiceTests
        from agents.testing import ScriptedModel,ModelStep,function_call,assistant_message
        from fastapi.testclient import TestClient
        from agent_runtime.api import create_agent_api
        from dataclasses import asdict
        fixture=RunServiceTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        model=ScriptedModel([ModelStep(output=[function_call('write_text',{'path':'report.md','content':'# 复核完成','expected_version':'missing'},call_id='write')]),ModelStep(output=[assistant_message('已完成')])])
        service=fixture._service(model)
        request=asdict(fixture._request('background-approval'));request['work_dir']=str(request['work_dir']);request['background']=True
        app=create_agent_api(run_service=service,db_path=fixture.db_path)
        with TestClient(app) as client:
            response=client.post('/api/agent/runs',json=request)
            self.assertEqual(response.status_code,200)
            self.assertIn('run.waiting',response.text)
            state=client.get('/api/agent/runs/background-approval').json()['data']
            interaction=state['interactions'][0]
            self.assertFalse((fixture.root/'report.md').exists())
            with closing(sqlite3.connect(fixture.db_path)) as db:
                db.row_factory=sqlite3.Row
                from services.conversation_history import history_run
                store=RunStore(db);history=history_run(db,store,store.get_run('background-approval'))
                self.assertEqual(history['last_seq'],state['last_seq'])
                self.assertEqual(history['tool_cards'][0]['status'],'waiting')
            response=client.post('/api/agent/runs/background-approval/resume',json={'background':True,'after_seq':state['last_seq'],'action':'approve','interaction_id':interaction['interaction_id'],'version':interaction['version']})
            self.assertEqual(response.status_code,200);self.assertIn('run.completed',response.text)
            self.assertNotIn('run.created',response.text)
            self.assertEqual((fixture.root/'report.md').read_text(encoding='utf-8'),'# 复核完成')
            latest=client.get('/api/agent/runs/background-approval').json()['data']
            response=client.get('/api/agent/runs/background-approval/stream',params={'after_seq':latest['last_seq']})
            self.assertEqual(response.text,'')
            self.assertEqual(client.get('/api/agent/runs/missing/stream').status_code,404)
