"""Project data plane API — Phase 14A/14E.

Blueprint project_data_bp (url_prefix=/api/project-data):
  POST   /import              提交 Excel 导入（后台线程，立即返回 import_id）
  POST   /import/anchor       提交用户确认的表头行锚点后继续导入（Phase 14E）
  GET    /import/status       轮询导入进度（sheet 级 % / needs_anchor）
  GET    /list                项目数据文件列表
  GET    /<file_id>/index     索引文档 JSON（分表/字段/类型/样例/非空率）
  GET    /<file_id>/changes   变更日志（Phase 14C）
  POST   /export              物化回写（Phase 14C）
  DELETE /<file_id>           移除数据文件（删数据表 + 元数据）
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid

from flask import Blueprint, current_app, request

from routes._utils import error, payload, success
from storage.db import get_db
from storage.project_data_store import ProjectDataStore
from storage.project_store import ProjectStore
from storage.task_store import TaskStore
from config.settings import DATA_DIR
from config.paths import resolve_work_dir
from agent_runtime.workspace import resolve_path
from tools.project_data_importer import (
    FormulaCalculationRequired,
    NeedAnchorError,
    ProjectDataImporter,
    IMPORT_VERSION,
    anchor_integer,
    sha256_file,
)

project_data_bp = Blueprint(
    "project_data_api", __name__, url_prefix="/api/project-data"
)

# import_id -> {"status": running|needs_anchor|done|failed, "progress": 0-100,
#               "file_id": str|None, "error": str|None,
#               "anchor": {sheets:[...], previews:{...}} | None,
#               "anchor_rows": {sheet: header_row} | None}


logger = logging.getLogger(__name__)


def _task_store(app=None):
    application = app or current_app._get_current_object()
    return TaskStore(application.config.get("TASK_DB_PATH", DATA_DIR / "tasks.sqlite3"))


@project_data_bp.get("/tasks")
def project_tasks():
    project_id = request.args.get("project_id", "").strip()
    if not project_id:
        return error("缺少 project_id")
    return success({"tasks": _task_store().list_project(project_id)})


def _drop_source_file(work_dir: str, rel_path: str, snapshot_relpath: str = "") -> None:
    """Delete a finmod upload only after its audit snapshot is durable."""
    from pathlib import Path

    try:
        work = Path(work_dir).resolve()
        target = (work / rel_path).resolve()
        snapshot = (work / snapshot_relpath).resolve() if snapshot_relpath else None
        if snapshot is None or not snapshot.is_file():
            logger.warning("保留 finmod 上传：审计快照不存在 %s", snapshot_relpath)
            return
        # 安全校验：必须在 work_dir 内（防路径穿越）
        if str(target) != str(work) and not str(target).startswith(str(work) + os.sep):
            logger.warning("跳过清理：路径超出工作目录 %s", rel_path)
            return
        if target.is_file():
            target.unlink()
            logger.info("已清理 finmod excel 源文件: %s", rel_path)
    except OSError as e:  # noqa: BLE001
        logger.warning("清理源文件失败 %s: %s", rel_path, e)


# Phase 17 P7：写回任务（异步 + 进度上报，复用导入模式）
# export_id -> {"status": running|done|failed, "progress": 0-100,
#               "message": str, "written_cells": int, "error": str|None}


def _run_export(
    app, export_id: str, project_id: str, file_name: str, mode: str
) -> None:
    """Background export thread: own app context + progress reporting."""

    def report(progress: float, msg: str = "") -> None:
        prev = _task_store(app).get(export_id) or {}
        prev["progress"] = round(progress, 1)
        if msg:
            prev["message"] = msg
        _task_store(app).put(export_id, prev)

    _task_store(app).put(
        export_id,
        {
            "status": "running",
            "progress": 0,
            "message": "准备写回",
            "written_cells": 0,
            "error": None,
            "project_id": project_id,
            "kind": "export",
        },
    )
    try:
        with app.app_context():
            project = _project(project_id)
            if not project:
                raise ValueError("项目不存在")
            from tools.project_data_tools import export_project_data

            result = export_project_data(
                file=file_name,
                mode=mode,
                project_id=project_id,
                work_dir=project["work_dir"],
                progress=report,
            )
        if not result.get("success"):
            # Phase 17 P9：透传 file_locked 错误类型（供 /export/status 返回）
            err = ValueError(result.get("error") or "写回失败")
            if result.get("error_type"):
                err.error_type = result["error_type"]  # type: ignore[attr-defined]
            raise err
        _task_store(app).update(
            export_id,
            {
                "status": "done",
                "progress": 100,
                "message": result.get("message", "写回完成"),
                "written_cells": result.get("written_cells", 0),
            },
        )
    except Exception as exc:  # noqa: BLE001
        # Phase 17 P9：透传 error_type=file_locked（文件被占用时给用户中文提示）
        err_type = getattr(exc, "error_type", None)
        _task_store(app).update(
            export_id,
            {
                "status": "failed",
                "error": str(exc)[:300],
                "error_type": err_type,
                "message": f"写回失败: {exc}",
            },
        )


def _project(project_id: str) -> dict | None:
    if not project_id:
        return None
    return ProjectStore(get_db()).get_by_id(project_id)


def _run_import(
    app,
    import_id: str,
    project_id: str,
    rel_path: str,
    anchor_rows: dict[str, object] | None = None,
    always_anchor: bool = False,
    *,
    expected_file_hash: str | None = None,
    formula_strategy: str = "auto",
) -> None:
    """Background import thread: own app context + progress reporting."""

    def report(
        status: str,
        progress: float,
        file_id: str | None = None,
        err: str | None = None,
        anchor: dict | None = None,
    ) -> None:
        prev = _task_store(app).get(import_id) or {}
        _task_store(app).put(
            import_id,
            {
                "status": status,
                "progress": round(progress, 1),
                "file_id": file_id,
                "error": err,
                "anchor": anchor,
                "anchor_rows": anchor_rows,
                # Preserve the original task fields so /import/anchor can
                # resume the same import on the same project+path.
                "project_id": prev.get("project_id", project_id),
                "path": prev.get("path", rel_path),
                "always_anchor": prev.get("always_anchor", always_anchor),
                "formula_strategy": prev.get("formula_strategy", formula_strategy),
                "kind": "import",
            },
        )

    try:
        with app.app_context():
            db = get_db()
            project = _project(project_id)
            if not project:
                report("failed", 0, err="项目不存在")
                return
            importer = ProjectDataImporter(db)
            result = importer.import_workbook(
                project_id,
                rel_path,
                project["work_dir"],
                anchor_rows=anchor_rows,
                always_anchor=always_anchor,
                expected_file_hash=expected_file_hash,
                calculation_strategy=formula_strategy,
                progress=lambda done, total, _name: report(
                    "running", done / max(total, 1) * 100
                ),
            )
            report("done", 100, file_id=result.get("file_id"))
            # 1c：应用2「财务建模与分析」（__app_finmod__，纯读分析无写回）导入完成后，
            # 清理 work_dir 下的 excel 源文件——只留产物（报告+图片），AI 只读数据库索引。
            if str(project.get("name") or "").startswith("__app_finmod__"):
                try:
                    result_meta = _safe_json(
                        (result.get("file") or {}).get("meta_json")
                    )
                    _drop_source_file(
                        project["work_dir"],
                        rel_path,
                        str(result_meta.get("snapshot_relpath") or ""),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("清理 finmod excel 源文件失败（忽略）: %s", exc)
    except FormulaCalculationRequired as exc:
        report(
            "needs_review",
            0,
            err=str(exc),
            anchor={
                "reason": "formula_calculation_required",
                "calculation": exc.calculation,
                "file_hash": (exc.calculation.get("manifest") or {}).get("file_hash"),
            },
        )
    except NeedAnchorError as exc:
        # Low-confidence header detection → ask the user to confirm the
        # header row.  The thread ends here; /import/anchor restarts it.
        # always_anchor 时 sheets 含全部（含高置信），previews 同源。
        report(
            "needs_anchor",
            0,
            anchor={
                "file_hash": exc.file_hash,
                "sheets": [
                    {
                        "name": name,
                        "header_row": lay.header_row,
                        "confidence": lay.confidence,
                        "columns": lay.columns,
                        "data_start_row": lay.data_start_row,
                        "data_end_row": lay.data_end_row,
                        "start_col": lay.start_col,
                        "end_col": lay.end_col,
                        "excluded_columns": lay.excluded_columns,
                        "classification": lay.classification,
                    }
                    for name, lay in exc.layouts.items()
                    if lay.classification not in {"empty", "notes"}
                    and (always_anchor or not lay.is_confident)
                ],
                "previews": {name: rows for name, rows in exc.previews.items()},
                "sheet_info": exc.sheet_info,
                "skipped_sheets": [
                    {"name": name, "classification": lay.classification}
                    for name, lay in exc.layouts.items()
                    if lay.classification in {"empty", "notes"}
                ],
            },
        )
    except Exception as exc:  # noqa: BLE001
        report("failed", 0, err=str(exc)[:300])


@project_data_bp.post("/import")
def import_data():
    body = payload()
    project_id = (body.get("project_id") or "").strip()
    rel_path = (body.get("path") or "").strip()
    # 应用场景：每次上传都要求用户确认表头行（always_anchor=True）
    always_anchor = bool(body.get("always_anchor"))
    if not project_id or not rel_path:
        return error("缺少 project_id 或 path")
    if not _project(project_id):
        return error("项目不存在", 404)
    import_id = uuid.uuid4().hex
    _task_store().put(
        import_id,
        {
            "status": "running",
            "progress": 0,
            "file_id": None,
            "error": None,
            "anchor": None,
            "anchor_rows": None,
            "formula_strategy": "auto",
            "project_id": project_id,
            "path": rel_path,
            "always_anchor": always_anchor,
            "kind": "import",
        },
    )
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_import,
        args=(app, import_id, project_id, rel_path),
        kwargs={"always_anchor": always_anchor},
        daemon=True,
    )
    thread.start()
    return success({"import_id": import_id})


@project_data_bp.post("/import/anchor")
def import_anchor():
    """Phase 14E: submit the user-confirmed header rows and resume import."""
    body = payload()
    import_id = (body.get("import_id") or "").strip()
    anchors = body.get("anchors") or {}  # {sheet_name: header_row}
    if not import_id:
        return error("缺少 import_id")
    if not isinstance(anchors, dict) or not anchors:
        return error("缺少 anchors（{工作表: 表头行号}）")
    info = _task_store().get(import_id)
    if not info:
        return error("导入任务不存在或已过期", 404)
    if info.get("status") != "needs_anchor":
        return error("该导入任务不在待确认状态", 400)
    # Normalize the legacy integer form and the full region form.
    try:
        anchor_rows: dict[str, object] = {}
        for key, value in anchors.items():
            if isinstance(value, dict):
                normalized = {
                    "header_row": anchor_integer(value.get("header_row", 0)),
                    "data_start_row": anchor_integer(value.get("data_start_row", 1)),
                    "data_end_row": anchor_integer(value.get("data_end_row", -1)),
                    "start_col": anchor_integer(value.get("start_col", 0)),
                    "end_col": anchor_integer(value.get("end_col", 0)),
                    "excluded_columns": [
                        anchor_integer(item)
                        for item in value.get("excluded_columns", [])
                    ],
                }
                anchor_rows[str(key)] = normalized
            else:
                anchor_rows[str(key)] = anchor_integer(value)
    except (TypeError, ValueError):
        return error("anchors 的行列范围必须是整数")
    expected_file_hash = (info.get("anchor") or {}).get("file_hash")
    if expected_file_hash:
        project = _project(info.get("project_id", ""))
        if not project:
            return error("项目不存在", 404)
        try:
            source = resolve_path(
                resolve_work_dir(project["work_dir"]), info.get("path", "")
            )
            if not source.is_file():
                return error("源文件不存在，请重新选择文件", 404)
            if sha256_file(str(source)) != expected_file_hash:
                return error("源文件已变化，请重新导入并确认新的导入区域", 409)
        except (OSError, ValueError) as exc:
            return error(str(exc), 400)
    anchor_rows = {**(info.get("anchor_rows") or {}), **anchor_rows}
    info["anchor_rows"] = anchor_rows
    if not _task_store().transition(
        import_id, {"needs_anchor"}, {"status": "running", "anchor_rows": anchor_rows}
    ):
        return error("该导入确认已提交", 409)
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_import,
        args=(
            app,
            import_id,
            info.get("project_id", ""),
            info.get("path", ""),
            anchor_rows,
        ),
        kwargs={
            "always_anchor": bool(info.get("always_anchor", False)),
            "expected_file_hash": expected_file_hash,
            "formula_strategy": info.get("formula_strategy", "auto"),
        },
        daemon=True,
    )
    thread.start()
    return success({"import_id": import_id, "resumed": True})


@project_data_bp.post("/import/calculation")
def resume_formula_review():
    """Resume a formula gate only after an explicit, auditable choice."""
    body = payload()
    import_id = (body.get("import_id") or "").strip()
    strategy = (body.get("strategy") or "").strip().lower()
    if not import_id:
        return error("缺少 import_id")
    if strategy not in {"cached", "recalculate"}:
        return error("公式处理策略必须是 cached 或 recalculate")
    info = _task_store().get(import_id)
    if not info:
        return error("导入任务不存在或已过期", 404)
    if info.get("status") != "needs_review":
        return error("该导入任务不在公式复核状态", 400)
    anchor = info.get("anchor") or {}
    calculation = anchor.get("calculation") or {}
    if anchor.get("reason") != "formula_calculation_required":
        return error("该任务不是公式复核任务", 400)
    if strategy == "cached" and not (
        calculation.get("stale_cache")
        and int(calculation.get("missing_after") or 0) == 0
        and int(calculation.get("errors_after") or 0) == 0
    ):
        return error("当前工作簿没有可安全接受的完整缓存值，请先修复源文件", 409)
    project = _project(info.get("project_id", ""))
    if not project:
        return error("项目不存在", 404)
    expected_file_hash = anchor.get("file_hash")
    if expected_file_hash:
        try:
            source = resolve_path(
                resolve_work_dir(project["work_dir"]), info.get("path", "")
            )
            if not source.is_file():
                return error("源文件不存在，请重新选择文件", 404)
            if sha256_file(str(source)) != expected_file_hash:
                return error("源文件已变化，请重新导入并复核公式", 409)
        except (OSError, ValueError) as exc:
            return error(str(exc), 400)
    if not _task_store().transition(
        import_id,
        {"needs_review"},
        {"status": "running", "formula_strategy": strategy, "error": None},
    ):
        return error("该公式复核已提交", 409)
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_import,
        args=(
            app,
            import_id,
            info.get("project_id", ""),
            info.get("path", ""),
            info.get("anchor_rows"),
            bool(info.get("always_anchor", False)),
        ),
        kwargs={
            "expected_file_hash": expected_file_hash,
            "formula_strategy": strategy,
        },
        daemon=True,
    )
    thread.start()
    return success({"import_id": import_id, "resumed": True, "strategy": strategy})


@project_data_bp.get("/import/status")
def import_status():
    import_id = request.args.get("import_id", "").strip()
    if not import_id:
        return error("缺少 import_id")
    info = _task_store().get(import_id)
    if not info:
        return error("导入任务不存在或已过期", 404)
    return success(info)


@project_data_bp.get("/list")
def list_data():
    project_id = request.args.get("project_id", "").strip()
    if not project_id:
        return error("缺少 project_id")
    store = ProjectDataStore(get_db())
    files = store.list_files(project_id)
    # Phase 17 P5：附每文件变更计数（列表角标）
    if files:
        ids = [f["id"] for f in files]
        rows = (
            get_db()
            .execute(
                "SELECT c.file_id, c.op_type, COUNT(*) AS n "
                "FROM project_data_changes c JOIN project_data_files f ON f.id=c.file_id "
                f"WHERE c.file_id IN ({','.join('?' * len(ids))}) "
                "AND c.id > COALESCE(CASE WHEN json_valid(f.meta_json) "
                "THEN CAST(json_extract(f.meta_json,'$.exported_change_id') AS INTEGER) END,0) "
                "GROUP BY c.file_id, c.op_type",
                ids,
            )
            .fetchall()
        )
        by_file: dict[str, dict] = {}
        for r in rows:
            d = by_file.setdefault(
                r["file_id"], {"update": 0, "insert": 0, "delete": 0}
            )
            d[r["op_type"]] = d.get(r["op_type"], 0) + int(r["n"])
        for f in files:
            c = by_file.get(f["id"], {})
            f["changes_count"] = {
                "total": sum(c.values()),
                "update": c.get("update", 0),
                "insert": c.get("insert", 0),
                "delete": c.get("delete", 0),
            }
    for f in files:
        meta = _safe_json(f.get("meta_json"))
        f["import_version"] = int(meta.get("import_version") or 0)
        f["needs_reimport"] = f["import_version"] < IMPORT_VERSION
        f.setdefault(
            "changes_count",
            {"total": 0, "update": 0, "insert": 0, "delete": 0},
        )
    return success({"files": files})


@project_data_bp.get("/<file_id>/index")
def data_index(file_id: str):
    """Phase 14A / 17 P6: 数据索引 —— 每字段精确到 Excel 列（列字母）。"""
    store = ProjectDataStore(get_db())
    file_rec = store.get_file(file_id)
    if not file_rec:
        return error("数据文件不存在", 404)
    sheets = store.list_sheets(file_id)
    meta = _safe_json(file_rec.get("meta_json"))

    # Phase 17 P6：每字段补 Excel 列字母（0-based idx → A/B/C…）；
    # layouts 补 1-based 表头行/数据起始行（直接给前端显示）。
    for s in sheets:
        headers = s.get("headers") or []
        for i, h in enumerate(headers):
            if not h.get("excel_col"):
                h["excel_col"] = _col_letter(int(h.get("source_col", i)))
        s["headers"] = headers
    layouts = meta.get("layouts") or {}
    for name, lay in layouts.items():
        if isinstance(lay, dict) and "header_row" in lay:
            hr = int(lay["header_row"] or 0)
            lay["excel_header_row"] = hr + 1
            data_start = int(lay.get("data_start_row", hr + 1) or hr + 1)
            lay["excel_data_start_row"] = data_start + 1
    meta["layouts"] = layouts

    changes = store.list_changes(file_id, limit=5)
    return success(
        {
            "file": {
                **file_rec,
                "meta": meta,
                "import_version": int(meta.get("import_version") or 0),
                "needs_reimport": int(meta.get("import_version") or 0) < IMPORT_VERSION,
            },
            "sheets": sheets,
            "changes": changes,
        }
    )


@project_data_bp.get("/<file_id>/changes")
def data_changes(file_id: str):
    """Phase 14C / 17 P5: cell-level change log — 正序 + 分页/过滤 + 摘要。

    每条日志明确到字段级（col_name）与 Excel 物理位置（excel_row/excel_col），
    与 _export_precise 写回引擎同源换算，是精准写回的剧本。
    """
    store = ProjectDataStore(get_db())
    file_rec = store.get_file(file_id)
    if not file_rec:
        return error("数据文件不存在", 404)
    try:
        limit = max(1, min(int(request.args.get("limit", "200")), 2000))
        offset = max(0, int(request.args.get("offset", "0")))
    except (TypeError, ValueError):
        limit, offset = 200, 0
    sheet = (request.args.get("sheet") or "").strip()
    op = (request.args.get("op") or "").strip()

    changes = store.list_changes(
        file_id, limit=limit, offset=offset, sheet=sheet, op=op
    )
    summary = store.count_changes(file_id)
    checkpoint = store.export_checkpoint(file_id)
    changes = [{**change, "applied": change["id"] <= checkpoint} for change in changes]
    return success(
        {
            "summary": summary,
            "pending_summary": store.count_changes(file_id, pending_only=True),
            "changes": _augment_changes(store, file_rec, changes),
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(changes) < summary.get("total", 0),
        }
    )


@project_data_bp.post("/export")
def export_data():
    """Phase 14C / 17 P7: 异步写回——立即返回 export_id，进度走 /export/status。"""
    body = payload()
    project_id = (body.get("project_id") or "").strip()
    file_name = (body.get("file") or "").strip()
    mode = (body.get("mode") or "precise").strip()
    if not project_id or not file_name:
        return error("缺少 project_id 或 file")
    project = _project(project_id)
    if not project:
        return error("项目不存在", 404)
    export_id = uuid.uuid4().hex
    _task_store().put(
        export_id,
        {
            "status": "running",
            "progress": 0,
            "message": "准备写回",
            "written_cells": 0,
            "error": None,
            "project_id": project_id,
            "kind": "export",
        },
    )
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_export,
        args=(app, export_id, project_id, file_name, mode),
        daemon=True,
    )
    thread.start()
    return success({"export_id": export_id})


@project_data_bp.get("/export/status")
def export_status():
    """Phase 17 P7: 写回任务进度轮询。"""
    export_id = request.args.get("export_id", "").strip()
    if not export_id:
        return error("缺少 export_id")
    info = _task_store().get(export_id)
    if not info:
        return error("写回任务不存在或已过期", 404)
    return success(info)


@project_data_bp.delete("/<file_id>")
def delete_data(file_id: str):
    store = ProjectDataStore(get_db())
    if not store.get_file(file_id):
        return error("数据文件不存在", 404)
    store.delete_file(file_id)
    return success({"deleted": True})


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _col_letter(idx: int) -> str:
    """0-based 列号 → Excel 列字母（0→A, 25→Z, 26→AA）。"""
    n = idx + 1
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _augment_changes(
    store: ProjectDataStore, file_rec: dict, changes: list[dict]
) -> list[dict]:
    """给每条日志补全 Excel 物理行列（与 _export_precise 写回引擎同源换算）。

    - 新导入记录直接使用持久化的 Excel 物理行列；旧记录按原顺序兼容换算。
    - 已删除行查不到序号 → excel_row 为 None（显示"已删除"）
    """
    meta = _safe_json(file_rec.get("meta_json"))
    layouts = meta.get("layouts") or {}
    sheet_recs = {s["sheet_name"]: s for s in store.list_sheets(file_rec["id"])}
    # sheet_name -> {data_id: Excel 物理行}
    seq_cache: dict[str, dict] = {}
    for name, s in sheet_recs.items():
        table = s.get("table_name")
        if not table:
            seq_cache[name] = {}
            continue
        table_columns = {
            row["name"]
            for row in store.db.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
        if "_source_row" in table_columns:
            rows = store.db.execute(
                f'SELECT id, _source_row FROM "{table}" ORDER BY id'
            ).fetchall()
        else:
            rows = store.db.execute(
                f'SELECT id, NULL AS _source_row FROM "{table}" ORDER BY id'
            ).fetchall()
        lay = layouts.get(name) or {}
        next_row = int(lay.get("data_start_row", 1) or 1) + 1
        mapping: dict[int, int] = {}
        for row in rows:
            physical_row = row["_source_row"]
            if physical_row is None:
                physical_row = next_row
                next_row += 1
            mapping[row["id"]] = int(physical_row)
        seq_cache[name] = mapping

    out = []
    for ch in changes:
        d = dict(ch)
        sheet_name = d.get("sheet_name") or ""
        seq = seq_cache.get(sheet_name, {}).get(d.get("row_index"))
        d["excel_row"] = seq
        col_index = d.get("col_index")
        headers = (sheet_recs.get(sheet_name) or {}).get("headers") or []
        if col_index is not None and col_index < len(headers):
            source_col = int(headers[col_index].get("source_col", col_index))
            d["excel_col"] = _col_letter(source_col)
        else:
            d["excel_col"] = _col_letter(col_index) if col_index is not None else None
        out.append(d)
    return out


def _safe_json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}
