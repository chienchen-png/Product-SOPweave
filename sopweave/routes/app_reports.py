"""HTTP adapter for integrated application operations."""
from flask import Blueprint
from pathlib import Path
from routes.app_commands import invoke, AppCommand
from services import finmod_reports

app_reports_bp = Blueprint("app_reports", __name__, url_prefix="")


@app_reports_bp.post('/save-chart')
def finmod_save_chart():
    return invoke(finmod_reports.finmod_save_chart, AppCommand)


@app_reports_bp.post('/export-md')
def finmod_export_md():
    return invoke(finmod_reports.finmod_export_md, AppCommand)


import os
from routes._utils import payload, error
from storage.report_store import ReportStore
from storage.db import get_db
from agent_runtime.workspace import resolve_path


@app_reports_bp.get("/files/<run_id>/assets/<path:filepath>")
def finmod_files_assets(run_id: str, filepath: str):
    """工作区静态代理：只读托管 run 报告目录里的图片（兼容旧 tmp_runs/assets）。

    新架构：图片已写到隐藏项目「财务分析报告N」，前端主要走 /api/files/raw。
    此端点保留，用于旧 format 的 assets/ 相对路径兼容。
    """
    import mimetypes
    from flask import send_file
    if not run_id or not filepath:
        return error("缺少 run_id/filepath")
    safe = os.path.basename(filepath)
    # 优先新报告目录（run_id → 报告目录映射）；其次旧 tmp_runs/assets
    report_dir = ReportStore(get_db()).find(run_id)
    if report_dir and os.path.isfile(os.path.join(report_dir, safe)):
        full = os.path.join(report_dir, safe)
    else:
        from config.settings import DATA_DIR
        try:
            full = str(resolve_path(DATA_DIR / "tmp_runs", run_id + "/assets/" + safe))
        except ValueError as exc:
            return error(str(exc), 400)
    if not os.path.isfile(full):
        return error(f"资源不存在: {filepath}", 404)
    mime, _ = mimetypes.guess_type(full)
    return send_file(full, mimetype=mime or "application/octet-stream")


@app_reports_bp.post("/export-docx")
def finmod_export_docx():
    """Export the stored report, or the editor's current Markdown, with shared Pandoc conversion."""
    from io import BytesIO
    from flask import send_file
    from services.office_export import export_markdown_docx
    from services.results import ServiceError
    p = payload()
    run_id = str(p.get("run_id") or "").strip()
    if not run_id:
        return error("缺少 run_id")
    directory = ReportStore(get_db()).find(run_id)
    if directory is None:
        return error("报告不存在", 404)
    content = p.get("content")
    if content is None:
        target = directory / f"{directory.name}.md"
        if not target.is_file():
            return error("报告文件不存在", 404)
        content = target.read_text(encoding="utf-8")
    from storage.project_store import ProjectStore
    record = get_db().execute("SELECT project_id FROM app_report_artifacts WHERE run_id=?", (run_id,)).fetchone()
    project = ProjectStore(get_db()).get_by_id(record["project_id"])
    try:
        raw = export_markdown_docx(content, Path(project["work_dir"]))
    except ServiceError as exc:
        return error(str(exc), exc.status_code)
    return send_file(BytesIO(raw), as_attachment=True, download_name=f"{directory.name}.docx",
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
