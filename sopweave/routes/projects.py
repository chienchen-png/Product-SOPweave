from __future__ import annotations

import os
from flask import Blueprint, request, current_app

from routes._utils import error, payload, success
from config.paths import managed_work_dir, portable_work_dir, resolve_work_dir
from storage.db import get_db
from storage.log_store import LogStore
from storage.model_store import ModelStore
from storage.project_store import ProjectStore

projects_bp = Blueprint("projects_api", __name__, url_prefix="/api/projects")


def _is_hidden_project(name: str) -> bool:
    """集成应用内部隐藏项目判定（`__app_` 前缀命名约定，与应用端 ensureAppProject 一致）。"""
    return str(name or "").startswith("__app_")


def _resolve_project_model(db, value: object) -> tuple[str | None, str | None]:
    ref = str(value or "").strip()
    if not ref:
        return None, None
    model = ModelStore(db).get_available_model(ref)
    if not model:
        return None, f"默认模型不可用或已删除：{ref}"
    return str(model["id"]), None


def _log(db, event_type: str, summary: str, project_id: str | None = None,
         status: str = "success", detail: str | None = None) -> None:
    """Best-effort local_logs write (PRD §3.8 最简化本地日志)."""
    try:
        LogStore(db).write(event_type=event_type, project_id=project_id,
                           event_summary=summary, status=status, detail=detail)
    except Exception:
        pass


@projects_bp.get("")
def list_projects():
    """项目列表。

    默认**隐藏集成应用的内部项目**（`__app_` 前缀，如 __app_recon__/__app_finmod__），
    不污染用户项目列表（前端侧边栏 + 本接口双保险，隐藏下沉到后端权威层）。
    应用内部（ensureAppProject）传 `?include_hidden=1` 显式查找/复用隐藏项目，
    避免因默认过滤导致每次进入应用都新建隐藏项目（历史曾出现重复 __app_recon__）。
    """
    keyword = request.args.get("q", "").strip()
    include_hidden = request.args.get("include_hidden", "").strip() in ("1", "true", "yes")
    store = ProjectStore(get_db())
    projects = store.search_by_name(keyword) if keyword else store.get_all()
    if not include_hidden:
        projects = [p for p in projects if not _is_hidden_project(p.get("name") or "")]
    return success(projects)


@projects_bp.post("")
def create_project():
    data = payload()
    name = str(data.get("name", "")).strip()
    work_dir = str(data.get("work_dir", "")).strip()
    if not name:
        return error("项目名称不能为空")
    if not work_dir and not managed_work_dir(name):
        return error("工作目录不能为空")
    if not managed_work_dir(name):
        try:
            directory = resolve_work_dir(work_dir)
            if not directory.is_dir():
                return error("工作目录不存在或不是文件夹，请重新选择")
            with os.scandir(directory):
                pass
        except (OSError, ValueError):
            return error("工作目录无法访问，请检查路径和访问权限")

    db = get_db()
    default_model, model_error = _resolve_project_model(db, data.get("default_model"))
    if model_error:
        return error(model_error)
    store = ProjectStore(db)
    project = store.create(
        name,
        work_dir,
        description=(data.get("description") or None),
        year=data.get("year") or None,
        quarter=data.get("quarter") or None,
        default_agent=data.get("default_agent") or None,
        default_model=default_model,
        work_mode=data.get("work_mode") or "manual",
    )
    _log(db, "project.create", f"创建项目「{name}」", project_id=project.get("id"),
         detail=f"work_dir={portable_work_dir(name, work_dir)}")
    return success(project, 201)


@projects_bp.get("/<project_id>")
def get_project(project_id: str):
    project = ProjectStore(get_db()).get_by_id(project_id)
    if not project:
        return error("项目不存在", 404)
    return success(project)


@projects_bp.put("/<project_id>")
def update_project(project_id: str):
    data = payload()
    db = get_db()
    default_model = None
    if "default_model" in data:
        default_model, model_error = _resolve_project_model(db, data.get("default_model"))
        if model_error:
            return error(model_error)
    store = ProjectStore(db)
    project = store.update(
        project_id,
        name=(data.get("name") or None),
        description=(data.get("description") or None),
        year=data.get("year") or None,
        quarter=data.get("quarter") or None,
        default_agent=data.get("default_agent") or None,
        **({"default_model": default_model} if "default_model" in data else {}),
        work_mode=data.get("work_mode") or None,
    )
    if not project:
        return error("项目不存在", 404)
    return success(project)


@projects_bp.delete("/<project_id>")
def delete_project(project_id: str):
    from storage.task_store import TaskStore
    from config.settings import DATA_DIR
    tasks = TaskStore(current_app.config.get("TASK_DB_PATH", DATA_DIR / "tasks.sqlite3"))
    if tasks.has_running(project_id):
        return error("项目存在正在导入或写回的任务，请等待完成", 409)
    db = get_db()
    try:
        deleted = ProjectStore(db).delete(project_id)
    except ValueError as exc:
        return error(str(exc), 409)
    if not deleted:
        return error("项目不存在", 404)
    tasks.delete_project(project_id)
    _log(db, "project.delete", f"删除项目 {project_id}", project_id=project_id)
    return success({"id": project_id})


@projects_bp.post("/<project_id>/duplicate")
def duplicate_project(project_id: str):
    """Copy a data snapshot atomically while sharing the explicit workspace."""
    from storage.project_clone import clone_project
    from storage.task_store import TaskStore
    from config.settings import DATA_DIR

    db = get_db()
    store = ProjectStore(db)
    src = store.get_by_id(project_id)
    if not src:
        return error("项目不存在", 404)
    if _is_hidden_project(src.get("name")):
        return error("集成应用的内部项目不支持创建副本", 409)
    tasks = TaskStore(current_app.config.get("TASK_DB_PATH", DATA_DIR / "tasks.sqlite3"))
    if tasks.has_running(project_id):
        return error("项目正在导入或写回，请等待完成后再创建副本", 409)
    try:
        new_id = clone_project(db, project_id)
    except ValueError as exc:
        return error(str(exc), 409)
    copy = store.get_by_id(new_id)
    _log(db, "project.duplicate", f"创建副本「{copy['name']}」", project_id=new_id,
         detail=f"source={project_id} shared_work_dir={src.get('work_dir')}")
    return success(copy, 201)
