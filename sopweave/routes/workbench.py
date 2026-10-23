from flask import Blueprint,request
from pydantic import BaseModel,ConfigDict,Field,ValidationError,JsonValue
from routes._utils import success,error,payload
from storage.db import get_db
from storage.workbench_store import WorkbenchStore
from services.workbench import draft,import_legacy_history
from services.results import ServiceError

workbench_bp=Blueprint('workbench_api',__name__,url_prefix='/api/workbench')

class DraftPayload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0,strict=True)
    state:dict[str,JsonValue]
    data_revision:str|None=Field(default=None,max_length=64)

@workbench_bp.errorhandler(ServiceError)
def service_error(exc): return error(str(exc),exc.status_code)

@workbench_bp.route('/drafts/<project_id>/<kind>',methods=['GET','PUT'])
def workspace_draft(project_id,kind):
    try:
        body=DraftPayload.model_validate(payload()).model_dump() if request.method=='PUT' else None
        return success(draft(get_db(),project_id,kind,body))
    except (ValidationError,ValueError) as exc:
        if isinstance(exc,ServiceError): raise
        return error('工作草稿字段无效',422)

@workbench_bp.get('/tasks')
def list_tasks():
    try:
        limit=int(request.args.get('limit',30))
        if not 1<=limit<=50: raise ValueError()
    except ValueError: return error('任务分页参数无效',422)
    return success(WorkbenchStore(get_db()).tasks(project_id=request.args.get('project_id',''),limit=limit,cursor=request.args.get('cursor',''),status=request.args.get('status','')))

@workbench_bp.get('/tasks/<run_id>')
def task_detail(run_id): return success(WorkbenchStore(get_db()).task(run_id))

@workbench_bp.get('/history/recon')
def recon_history():return success({'records':WorkbenchStore(get_db()).recon_history(request.args.get('project_id',''))})

class LegacyPayload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    project_id:str=Field(min_length=1,max_length=100)
    kind:str
    records:list[dict[str,JsonValue]]=Field(max_length=50)

@workbench_bp.post('/legacy-history')
def import_legacy():
    try:body=LegacyPayload.model_validate(payload())
    except ValidationError:return error('旧记录格式无效',422)
    return success(import_legacy_history(get_db(),body.project_id,body.kind,body.records))

@workbench_bp.before_request
def bound_workbench_payload():
    if request.content_length and request.content_length>2*1024*1024+4096:return error('工作台请求超过 2 MiB 上限',413)

@workbench_bp.get('/reports')
def list_reports(): return success({'reports':WorkbenchStore(get_db()).reports(request.args.get('project_id',''))})

@workbench_bp.delete('/reports')
def hide_report():
    body=payload();store=WorkbenchStore(get_db())
    if not isinstance(body,dict):return error('请求必须为对象',422)
    task=store.task(str(body.get('run_id','')))
    path=body.get('rel_path')
    if task['project_id']!=body.get('project_id') or not any(item.get('rel_path')==path for item in task['reports']):
        return error('报告不属于当前任务',404)
    store.append_event(task['run_id'],'app.report.hidden',{'rel_path':path})
    return success({'removed':True,'file_preserved':True})
