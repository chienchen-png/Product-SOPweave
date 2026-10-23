"""Workspace drafts and application facts; execution history stays in runs/events."""
import hashlib
import json
import os
from storage._utils import iso_now
from services.results import ServiceError


class WorkbenchStore:
    def __init__(self, db):
        self.db = db

    def data_revision(self, project_id):
        files = self.db.execute('SELECT id,file_hash,updated_at,meta_json FROM project_data_files WHERE project_id=? ORDER BY id', (project_id,)).fetchall()
        change = self.db.execute('SELECT COALESCE(MAX(c.id),0) FROM project_data_changes c JOIN project_data_files f ON f.id=c.file_id WHERE f.project_id=?', (project_id,)).fetchone()[0]
        return hashlib.sha256(json.dumps([list(row) for row in files]+[change],ensure_ascii=False).encode()).hexdigest()

    def get_draft(self, project_id, kind):
        row = self.db.execute('SELECT * FROM workspace_drafts WHERE project_id=? AND kind=?', (project_id,kind)).fetchone()
        return {'project_id':project_id,'kind':kind,'revision':row['revision'] if row else 0,
                'state':json.loads(row['state_json']) if row else {},'updated_at':row['updated_at'] if row else None,
                'data_revision':self.data_revision(project_id),'saved_data_revision':row['data_revision'] if row else ''}

    def save_draft(self, project_id, kind, expected_revision, state, data_revision=None):
        raw = json.dumps(state,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>2*1024*1024:
            raise ServiceError('工作草稿超过 2 MiB，请减少内容',422)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            revision = self.data_revision(project_id)
            if data_revision is not None and data_revision != revision:
                raise ServiceError('数据已变化，请重新加载并检查下游结果后保存',409)
            row = self.db.execute('SELECT revision FROM workspace_drafts WHERE project_id=? AND kind=?', (project_id,kind)).fetchone()
            if (row['revision'] if row else 0) != expected_revision:
                raise ServiceError('草稿已在其他页面更新，当前输入已保留，请重新加载后比较',409)
            self.db.execute('INSERT INTO workspace_drafts VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,kind) DO UPDATE SET state_json=excluded.state_json,revision=excluded.revision,data_revision=excluded.data_revision,updated_at=excluded.updated_at',
                            (project_id,kind,raw,expected_revision+1,revision,iso_now(),1))
        return self.get_draft(project_id,kind)

    def record_result(self, kind, run_id, project_id, params, result):
        with self.db:
            old = self.db.execute('SELECT project_id FROM runs WHERE id=?',(run_id,)).fetchone()
            if old and old['project_id'] != project_id:
                raise ServiceError('运行不属于当前项目',409)
            now=iso_now()
            title = params.get('goal') or ('数据核对' if kind=='recon' else '财务建模')
            self.db.execute("INSERT OR IGNORE INTO runs(id,project_id,agent_id,model_id,title,status,created_at,updated_at) VALUES(?,?,?,?,?,'starting',?,?)",(run_id,project_id,'app:'+kind,params.get('model_id',''),title[:200],now,now))
            self.append_event(run_id,'app.result',{'kind':kind,'params':params,'result':result},commit=False)
            self.db.execute("UPDATE runs SET status='completed',completed_at=?,updated_at=? WHERE id=?",(now,now,run_id))

    def start_result(self, kind, run_id, project_id, params):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute("SELECT 1 FROM runs WHERE project_id=? AND agent_id=? AND status IN ('starting','running') LIMIT 1",(project_id,'app:'+kind)).fetchone():
                raise ServiceError('此应用已有运行中的任务，请等待完成后再执行',409)
            now=iso_now()
            self.db.execute("INSERT INTO runs(id,project_id,agent_id,model_id,title,status,created_at,updated_at) VALUES(?,?,?,?,?,'running',?,?)",(run_id,project_id,'app:'+kind,params.get('model_id',''),(params.get('goal') or ('数据核对' if kind=='recon' else '财务建模'))[:200],now,now))
            self.append_event(run_id,'app.started',{'owner_pid':os.getpid(),'data_revision':self.data_revision(project_id)},commit=False)

    def fail_result(self, run_id, message):
        with self.db:
            self.db.execute("UPDATE runs SET status='failed',error_message=?,updated_at=?,completed_at=? WHERE id=? AND status IN ('starting','running')",(str(message)[:2000],iso_now(),iso_now(),run_id))

    def recover_interrupted(self):
        from agent_runtime.continuations import process_alive
        rows=self.db.execute("SELECT r.id,e.payload_json FROM runs r JOIN run_events e ON e.run_id=r.id AND e.event_type='app.started' WHERE r.agent_id LIKE 'app:%' AND r.status IN ('starting','running')").fetchall()
        for row in rows:
            if not process_alive(json.loads(row['payload_json']).get('owner_pid',0)):
                self.fail_result(row['id'],'服务重启导致任务中断，请检查现有结果后重新执行')

    def append_event(self, run_id, event_type, value, *, commit=True):
        self.db.execute('INSERT INTO run_events(run_id,seq,event_type,payload_json,created_at) SELECT ?,COALESCE(MAX(seq),0)+1,?,?,? FROM run_events WHERE run_id=?',
                        (run_id,event_type,json.dumps(value,ensure_ascii=False,allow_nan=False),iso_now(),run_id))
        if commit:
            self.db.execute('UPDATE runs SET updated_at=? WHERE id=?',(iso_now(),run_id))
            self.db.commit()

    def tasks(self, *, project_id='', limit=30, cursor='', status=''):
        predicates=['p.id IS NOT NULL'];params=[]
        if project_id:
            predicates.append('r.project_id=?');params.append(project_id)
        if status=='running':predicates.append("r.status IN ('starting','running','resuming')")
        elif status:
            predicates.append('r.status=?');params.append(status)
        if cursor:
            row=self.db.execute('SELECT updated_at,id FROM runs WHERE id=?',(cursor,)).fetchone()
            if not row: raise ServiceError('任务分页位置无效，请刷新',422)
            predicates.append('(r.updated_at,r.id)<(?,?)');params.extend(row)
        rows=self.db.execute(f"SELECT r.*,p.name AS project_name FROM runs r JOIN projects p ON p.id=r.project_id WHERE {' AND '.join(predicates)} ORDER BY r.updated_at DESC,r.id DESC LIMIT ?",(*params,limit+1)).fetchall()
        items=[self._summary(row) for row in rows[:limit]]
        return {'tasks':items,'next_cursor':items[-1]['run_id'] if len(rows)>limit else ''}

    @staticmethod
    def _summary(row):
        kind=row['agent_id'][4:] if (row['agent_id'] or '').startswith('app:') else 'chat'
        return {'run_id':row['id'],'project_id':row['project_id'],'project_name':row['project_name'],'kind':kind,
                'title':row['title'],'status':row['status'],'created_at':row['created_at'],'updated_at':row['updated_at'],
                'error':row['error_message'] or ''}

    def task(self, run_id):
        row=self.db.execute('SELECT r.*,p.name AS project_name FROM runs r JOIN projects p ON p.id=r.project_id WHERE r.id=?',(run_id,)).fetchone()
        if not row: raise ServiceError('任务不存在或所属项目已删除',404)
        events=self.db.execute("SELECT event_type,payload_json FROM run_events WHERE run_id=? AND event_type IN ('app.started','app.result','app.audit','app.report','app.writeback','legacy.note') ORDER BY seq",(run_id,)).fetchall()
        detail={**self._summary(row),'result':None,'params':{},'reports':[],'writeback':None,'data_revision':'','legacy_note':None}
        for event in events:
            value=json.loads(event['payload_json'] or '{}')
            if event['event_type']=='app.result': detail.update(result=value.get('result'),params=value.get('params',{}))
            elif event['event_type']=='app.started': detail['data_revision']=value.get('data_revision','')
            elif event['event_type']=='app.audit': detail['result']={**(detail['result'] or {}),**value}
            elif event['event_type']=='app.report': detail['reports'].append(value)
            elif event['event_type']=='app.writeback': detail['writeback']=value
            elif event['event_type']=='legacy.note': detail.update(legacy_note=value,params=value.get('params',{}))
        detail['reports']=list({item['rel_path']:item for item in detail['reports']}.values())
        return detail

    def recon_history(self, project_id):
        rows=self.db.execute("""SELECT r.id,r.created_at,
          (SELECT payload_json FROM run_events e WHERE e.run_id=r.id AND event_type='app.result' ORDER BY seq DESC LIMIT 1) AS facts,
          (SELECT payload_json FROM run_events e WHERE e.run_id=r.id AND event_type='app.audit' ORDER BY seq DESC LIMIT 1) AS audit
          FROM runs r JOIN projects p ON p.id=r.project_id WHERE r.project_id=? AND r.agent_id='app:recon' AND r.status='completed' ORDER BY r.updated_at DESC LIMIT 10""",(project_id,)).fetchall()
        from datetime import datetime
        items=[]
        for row in rows:
            payload=json.loads(row['facts'] or '{}');result={**payload.get('result',{}),**json.loads(row['audit'] or '{}')}
            data=result.get('reconData') or {}
            items.append({'id':row['id'],'runId':row['id'],'time':int(datetime.fromisoformat(row['created_at']).timestamp()*1000),'params':payload.get('params',{}).get('params',{}),'summary':data.get('summary'),'diffs':data.get('diffs',[]),'auditCount':len(result.get('auditItems',[])),'report':result.get('report','')})
        return items

    def reports(self, project_id=''):
        rows=self.db.execute("""WITH entries AS (
          SELECT e.*,r.project_id,r.title,
            ROW_NUMBER() OVER(PARTITION BY e.run_id,json_extract(e.payload_json,'$.rel_path') ORDER BY e.id DESC) AS rank
          FROM run_events e JOIN runs r ON r.id=e.run_id JOIN projects p ON p.id=r.project_id
          WHERE e.event_type IN ('app.report','app.report.hidden') AND (?='' OR r.project_id=?)
        ) SELECT * FROM entries WHERE rank=1 AND event_type='app.report' ORDER BY id DESC LIMIT 50""",(project_id,project_id)).fetchall()
        return [{**json.loads(row['payload_json']),'run_id':row['run_id'],'project_id':row['project_id'],'goal':json.loads(row['payload_json']).get('goal') or row['title'],'created_at':row['created_at']} for row in rows]
