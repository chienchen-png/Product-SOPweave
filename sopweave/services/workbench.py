"""Validation for work-in-progress state, separate from execution facts."""
from storage.project_store import ProjectStore
from storage.workbench_store import WorkbenchStore
from services.results import ServiceError
from pathlib import Path
import hashlib
from storage._utils import iso_now


def project_scope(db, project_id, kind):
    project=ProjectStore(db).get_by_id(project_id)
    if not project: raise ServiceError('项目不存在',404)
    if kind not in {'chat','recon','finmod'}: raise ServiceError('工作草稿类型无效',422)
    hidden=project['name'].startswith('__app_')
    if kind=='chat' and hidden or kind!='chat' and project['name']!=f'__app_{kind}__':
        raise ServiceError('工作草稿与项目作用域不一致',422)
    return project


def validate_state(value):
    if isinstance(value,dict):
        for key,item in value.items():
            normalized=key.lower().replace('_','').replace('-','')
            if normalized in {'apikey','authorization','accesstoken','refreshtoken','secret','password'}:
                raise ServiceError('草稿不能保存密钥或登录信息',422)
            validate_state(item)
    elif isinstance(value,list):
        for item in value: validate_state(item)


def draft(db, project_id, kind, body=None):
    project_scope(db,project_id,kind)
    store=WorkbenchStore(db)
    if body is None: return store.get_draft(project_id,kind)
    validate_state(body['state'])
    return store.save_draft(project_id,kind,body['expected_revision'],body['state'],body.get('data_revision'))


def persist_report(db, project_id, run_id, text, *, filename='核对报告.md', category='数据核对报告'):
    from storage.report_store import ReportStore
    from agent_runtime.workspace import file_version,atomic_bytes
    if Path(filename).name!=filename or not filename.endswith('.md'):
        raise ServiceError('报告文件名无效',422)
    project=ProjectStore(db).get_by_id(project_id)
    if not project: raise ServiceError('项目不存在',404)
    root=Path(project['work_dir']).resolve()
    directory=ReportStore(db).allocate(project_id,run_id,root,category=category)
    path=directory/filename
    if not path.exists(): atomic_bytes(path,text.encode('utf-8'),'missing')
    artifact={'project_id':project_id,'run_id':run_id,'filename':filename,'rel_path':path.relative_to(root).as_posix(),'version':file_version(path)}
    WorkbenchStore(db).append_event(run_id,'app.report',artifact)
    return artifact


def import_legacy_history(db, project_id, kind, records):
    project=project_scope(db,project_id,kind)
    if kind=='chat':raise ServiceError('不支持导入此类历史',422)
    imported=0;skipped=[];store=WorkbenchStore(db)
    for record in records:
        identifier=str(record.get('id') or record.get('run_id') or '')[:200]
        if not identifier:skipped.append('旧记录缺少标识');continue
        artifact=None
        if kind=='finmod':
            from agent_runtime.workspace import resolve_path,file_version
            try:
                relative=str(record.get('rel_path',''))
                if Path(relative).is_absolute() or not relative.endswith('.md'):raise ValueError()
                path=resolve_path(Path(project['work_dir']),relative)
                if not path.is_file():raise ValueError()
                artifact={'project_id':project_id,'rel_path':relative,'filename':path.name,'version':file_version(path)}
            except (ValueError,OSError):skipped.append(f'报告不可访问：{record.get("filename") or identifier}');continue
        if not isinstance(record.get('params',{}),dict):skipped.append('旧记录参数必须为对象');continue
        validate_state(record)
        run_id='legacy-'+hashlib.sha256(f'{project_id}:{kind}:{identifier}'.encode()).hexdigest()[:32]
        with db:
            if db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():imported+=1;continue
            now=iso_now();title='旧记录待复核：'+str(record.get('goal') or ('数据核对' if kind=='recon' else '财务报告'))[:150]
            db.execute("INSERT INTO runs(id,project_id,agent_id,title,status,created_at,updated_at) VALUES(?,?,?,?,'legacy',?,?)",(run_id,project_id,'app:'+kind,title,now,now))
            store.append_event(run_id,'legacy.note',{'params':record.get('params',{}),'report':str(record.get('report',''))[:100000],'original_created_at':record.get('created_at','')},commit=False)
            if artifact:store.append_event(run_id,'app.report',{**artifact,'run_id':run_id,'goal':title},commit=False)
        imported+=1
    return {'imported':imported,'skipped':skipped}
