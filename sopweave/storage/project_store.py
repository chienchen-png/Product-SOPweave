"""projects table CRUD — Phase 2."""

from __future__ import annotations

import uuid
from sqlite3 import Connection

from config.paths import portable_work_dir, resolve_work_dir
from storage._utils import iso_now, row_to_dict

UNSET = object()


class ProjectStore:
    def __init__(self, db: Connection) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create(
        self,
        name: str,
        work_dir: str,
        *,
        description: str | None = None,
        year: int | None = None,
        quarter: str | None = None,
        default_agent: str | None = None,
        default_model: str | None = None,
        work_mode: str = "manual",
    ) -> dict:
        now = iso_now()
        pid = uuid.uuid4().hex
        stored_work_dir = portable_work_dir(name, work_dir)
        self.db.execute(
            """INSERT INTO projects
               (id, name, description, year, quarter, work_dir,
                default_agent, default_model, work_mode, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, name, description, year, quarter, stored_work_dir,
             default_agent, default_model, work_mode, now, now),
        )
        self.db.commit()
        return self.get_by_id(pid)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get_all(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC"
        ).fetchall()
        return [self._runtime_project(r) for r in rows]

    def get_by_id(self, pid: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM projects WHERE id = ?", (pid,)
        ).fetchone()
        return self._runtime_project(row)

    def search_by_name(self, keyword: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM projects WHERE name LIKE ? ORDER BY updated_at DESC",
            (f"%{keyword}%",),
        ).fetchall()
        return [self._runtime_project(r) for r in rows]

    @staticmethod
    def _runtime_project(row) -> dict:
        """Expose an absolute runtime path while keeping the database portable."""
        project = row_to_dict(row)
        if project and project.get("work_dir"):
            project["work_dir"] = str(resolve_work_dir(project["work_dir"]))
        return project

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------
    def update(
        self,
        pid: str,
        *,
        name: str | None = None,
        description: str | None = None,
        year: int | None = None,
        quarter: str | None = None,
        default_agent: str | None = None,
        default_model: str | None | object = UNSET,
        work_mode: str | None = None,
    ) -> dict | None:
        existing = self.get_by_id(pid)
        if not existing:
            return None
        self.db.execute(
            """UPDATE projects
               SET name            = COALESCE(?, name),
                   description     = COALESCE(?, description),
                   year            = COALESCE(?, year),
                   quarter         = COALESCE(?, quarter),
                   default_agent   = COALESCE(?, default_agent),
                   default_model   = CASE WHEN ? THEN ? ELSE default_model END,
                   work_mode       = COALESCE(?, work_mode),
                   updated_at      = ?
               WHERE id = ?""",
            (name, description, year, quarter, default_agent,
             default_model is not UNSET, None if default_model is UNSET else default_model,
             work_mode, iso_now(), pid),
        )
        self.db.commit()
        return self.get_by_id(pid)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def delete(self, pid: str) -> bool:
        with self.db:
            return self._delete(pid)

    def _delete(self, pid: str) -> bool:
        from storage.project_lifecycle import clear_project_history
        clear_project_history(self.db, pid)
        self.db.execute('DELETE FROM workspace_drafts WHERE project_id=?',(pid,))
        self.db.execute("UPDATE token_usage SET project_id=NULL,conversation_id=NULL,message_id=NULL WHERE project_id = ?", (pid,))
        self.db.execute("DELETE FROM tool_calls WHERE project_id = ?", (pid,))
        self.db.execute("DELETE FROM local_logs WHERE project_id = ?", (pid,))
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_report_artifacts'").fetchone():
            self.db.execute("DELETE FROM app_report_artifacts WHERE project_id=?", (pid,))
        # Phase 14A: cascade-delete project data plane (data tables + metadata)
        data_file_rows = self.db.execute(
            "SELECT id FROM project_data_files WHERE project_id = ?", (pid,)
        ).fetchall()
        for row in data_file_rows:
            sheet_rows = self.db.execute(
                "SELECT table_name FROM project_data_sheets WHERE file_id = ?",
                (row["id"],),
            ).fetchall()
            for srow in sheet_rows:
                table_name = srow["table_name"]
                if table_name.startswith("data_"):
                    self.db.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            self.db.execute(
                "DELETE FROM project_data_sheets WHERE file_id = ?", (row["id"],)
            )
            # Phase 14C: cascade-delete cell change log
            self.db.execute(
                "DELETE FROM project_data_changes WHERE file_id = ?", (row["id"],)
            )
        self.db.execute(
            "DELETE FROM project_data_files WHERE project_id = ?", (pid,)
        )
        cur = self.db.execute("DELETE FROM projects WHERE id = ?", (pid,))
        return cur.rowcount > 0
