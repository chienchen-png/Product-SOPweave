"""Stable report allocation using SQLite ownership and relative artifact paths."""
from pathlib import Path
import re

from agent_runtime.workspace import resolve_path
from storage._utils import iso_now
from storage.project_store import ProjectStore


class ReportStore:
    def __init__(self, db):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS app_report_artifacts(
            run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            relative_dir TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(project_id,relative_dir))""")

    def allocate(self, project_id: str, run_id: str, work_dir: Path, *, category='财务分析报告') -> Path:
        if category not in {'财务分析报告','数据核对报告'}:
            raise ValueError('报告类别无效')
        if not run_id or not project_id:
            raise ValueError("报告缺少项目或运行标识")
        root = Path(work_dir).resolve()
        with self.db:
            if not self.db.in_transaction:
                self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT project_id,relative_dir FROM app_report_artifacts WHERE run_id=?", (run_id,)).fetchone()
            if row:
                if row["project_id"] != project_id:
                    raise ValueError("报告运行不属于当前项目")
                path = resolve_path(root, row["relative_dir"])
                path.mkdir(parents=True, exist_ok=True)
                return path
            reports = resolve_path(root, category)
            reports.mkdir(parents=True, exist_ok=True)
            numbers = [int(match[1]) for child in reports.iterdir() if child.is_dir() and (match := re.fullmatch(rf"{re.escape(category)}(\d+)", child.name))]
            number = max(numbers, default=0) + 1
            while True:
                path = reports / f"{category}{number}"
                try:
                    path.mkdir()
                    break
                except FileExistsError:
                    number += 1
            self.db.execute("INSERT INTO app_report_artifacts VALUES(?,?,?,?)", (run_id, project_id, path.relative_to(root).as_posix(), iso_now()))
        return path

    def find(self, run_id: str) -> Path | None:
        row = self.db.execute("SELECT project_id,relative_dir FROM app_report_artifacts WHERE run_id=?", (run_id,)).fetchone()
        project = ProjectStore(self.db).get_by_id(row["project_id"]) if row else None
        return resolve_path(Path(project["work_dir"]), row["relative_dir"]) if project else None
