"""Own executions independently of browser subscriptions; replay durable events."""
import asyncio
import logging
import sqlite3
from contextlib import closing
from storage.run_store import RunStore
from agent_runtime.events import EventWriter,WorkbenchEvent

logger=logging.getLogger(__name__)


class BackgroundRuns:
    def __init__(self, service, db_path):
        self.service=service
        self.db_path=db_path
        self.tasks:dict[str,asyncio.Task]={}

    async def start(self, run_id, source):
        active=self.tasks.get(run_id)
        if active and not active.done():
            await source.aclose()
            raise ValueError('任务正在执行，请连接已有运行')
        iterator=source.__aiter__()
        # Validate/create the durable run before sending successful HTTP headers.
        try:await anext(iterator)
        except StopAsyncIteration:return
        except BaseException:
            await iterator.aclose()
            raise
        task=asyncio.create_task(self._consume(run_id,iterator),name=f'agent:{run_id}')
        self.tasks[run_id]=task
        def finished(done):
            if self.tasks.get(run_id) is done:self.tasks.pop(run_id,None)
        task.add_done_callback(finished)

    async def _consume(self,run_id,iterator):
        try:
            async for _event in iterator:pass
        except asyncio.CancelledError:
            self.service.cancel(run_id)
            raise
        except Exception as exc:
            logger.error('后台运行失败 %s (%s)',run_id,type(exc).__name__)
            await asyncio.to_thread(self._fail,run_id,'后台执行中断，请查看服务日志后重试')
        finally:
            await iterator.aclose()

    def _connection(self):
        db=sqlite3.connect(self.db_path,timeout=10)
        db.row_factory=sqlite3.Row
        return db

    def _read(self,run_id,after_seq):
        with closing(self._connection()) as db:
            store=RunStore(db)
            return store.get_run(run_id),store.get_events_after(run_id,after_seq)

    def _fail(self,run_id,message):
        with closing(self._connection()) as db:
            store=RunStore(db)
            if not store.get_run(run_id):return
            store.update_run_status(run_id,'failed',error_message=message)
        with EventWriter(self.db_path,run_id) as writer:writer.write('run.failed',{'message':message})

    async def events(self,run_id,request,after_seq=0):
        last=after_seq
        heartbeat=asyncio.get_running_loop().time()
        while not await request.is_disconnected():
            run,rows=await asyncio.to_thread(self._read,run_id,last)
            for row in rows:
                last=row['seq']
                yield WorkbenchEvent(run_id,last,row['event_type'],row.get('payload') or {},row['created_at'])
            active=self.tasks.get(run_id)
            if run is None or run['status'] in {'completed','failed','stopped','cancelled','waiting','legacy'} and (active is None or active.done()):return
            now=asyncio.get_running_loop().time()
            if now-heartbeat>=15:heartbeat=now;yield None
            await asyncio.sleep(0.25)

    async def shutdown(self):
        tasks=list(self.tasks.items())
        for run_id,task in tasks:
            self.service.cancel(run_id)
            task.cancel()
        if tasks:await asyncio.gather(*(task for _,task in tasks),return_exceptions=True)
