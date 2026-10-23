"""Durable import/export facts, independent of long workbook transactions."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from agent_runtime.continuations import process_alive
from storage._utils import iso_now


class TaskStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, state_json TEXT NOT NULL,
                owner_pid INTEGER NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(json_extract(state_json,'$.project_id'),updated_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(json_extract(state_json,'$.status'))")
            db.commit()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def get(self, identifier):
        with closing(self.connect()) as db:
            row = db.execute('SELECT state_json FROM tasks WHERE id=?', (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, identifier, state):
        with closing(self.connect()) as db:
            self._put(db, identifier, state)
            db.commit()

    def _put(self, db, identifier, state):
        db.execute("""INSERT INTO tasks VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            state_json=excluded.state_json,owner_pid=excluded.owner_pid,updated_at=excluded.updated_at""",
                   (identifier, json.dumps(state, ensure_ascii=False, default=str), os.getpid(), iso_now()))

    def update(self, identifier, changes):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state_json FROM tasks WHERE id=?', (identifier,)).fetchone()
            state = json.loads(row[0]) if row else {}
            self._put(db, identifier, {**state, **changes})
            db.commit()

    def transition(self, identifier, expected, changes):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state_json FROM tasks WHERE id=?', (identifier,)).fetchone()
            state = json.loads(row[0]) if row else {}
            if state.get('status') not in expected:
                db.rollback()
                return False
            self._put(db, identifier, {**state, **changes})
            db.commit()
        return True

    def delete(self, identifier):
        with closing(self.connect()) as db:
            db.execute('DELETE FROM tasks WHERE id=?', (identifier,))
            db.commit()

    def list_project(self, project_id):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT id,state_json FROM tasks WHERE json_extract(state_json,'$.project_id')=? ORDER BY updated_at DESC LIMIT 100", (project_id,)).fetchall()
        return [{**json.loads(raw), 'id': identifier} for identifier, raw in rows]

    def has_running(self, project_id):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT owner_pid FROM tasks WHERE json_extract(state_json,'$.project_id')=? AND json_extract(state_json,'$.status')='running'", (project_id,)).fetchall()
        return any(process_alive(row[0]) for row in rows)

    def delete_project(self, project_id):
        with closing(self.connect()) as db:
            db.execute("DELETE FROM tasks WHERE json_extract(state_json,'$.project_id')=?", (project_id,))
            db.commit()

    def recover_interrupted(self):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            for identifier, raw, owner in db.execute("SELECT id,state_json,owner_pid FROM tasks WHERE json_extract(state_json,'$.status')='running'").fetchall():
                state = json.loads(raw)
                if state.get('status') == 'running' and not process_alive(owner):
                    self._put(db, identifier, {**state, 'status':'failed', 'error_type':'interrupted', 'error':'进程意外结束，请检查现有导入/写回结果后重新操作'})
            db.commit()
