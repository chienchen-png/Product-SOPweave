"""Durable continuation ownership; interrupted effects are never replayed automatically."""
from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing

from storage._utils import iso_now


def process_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not proof of exit.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class ContinuationStore:
    def __init__(self, path):
        self.path = path
        with closing(self.connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS agent_continuations(
                run_id TEXT PRIMARY KEY, status TEXT NOT NULL, owner_pid INTEGER,
                token TEXT, updated_at TEXT NOT NULL)""")
            # A dead execution may already have written files: mark it failed for review.
            for run_id, pid in db.execute("SELECT run_id,owner_pid FROM agent_continuations WHERE status='executing'").fetchall():
                if not process_alive(pid):
                    db.execute("UPDATE agent_continuations SET status='interrupted',updated_at=? WHERE run_id=?", (iso_now(), run_id))
                    db.execute("UPDATE runs SET status='failed',error_message=?,updated_at=? WHERE id=? AND status IN ('starting','running','resuming')", ("执行进程意外结束，请检查已完成操作后重新发起任务", iso_now(), run_id))
                    db.execute("UPDATE agent_run_checkpoints SET status='interrupted',lease_token=NULL,updated_at=? WHERE run_id=? AND status='acquired'", (iso_now(), run_id))
            db.commit()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        return db

    def pending(self, run_id):
        with closing(self.connect()) as db:
            row = db.execute("SELECT status FROM agent_continuations WHERE run_id=?", (run_id,)).fetchone()
        return bool(row and row[0] == "pending")

    def claim(self, run_id):
        token = uuid.uuid4().hex
        with closing(self.connect()) as db:
            cursor = db.execute("UPDATE agent_continuations SET status='executing',owner_pid=?,token=?,updated_at=? WHERE run_id=? AND status='pending'", (os.getpid(), token, iso_now(), run_id))
            db.commit()
        return token if cursor.rowcount == 1 else None

    def begin_execution(self, run_id, token=None):
        """Atomically claim an initial request or validate an accepted continuation."""
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            run = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run or run[0] not in {'starting', 'resuming'}:
                db.rollback()
                return None
            if token is None:
                token = uuid.uuid4().hex
                cursor = db.execute("UPDATE agent_continuations SET status='executing',owner_pid=?,token=?,updated_at=? WHERE run_id=? AND status='pending'", (os.getpid(), token, iso_now(), run_id))
            else:
                cursor = db.execute("SELECT 1 FROM agent_continuations WHERE run_id=? AND status='executing' AND token=?", (run_id, token))
                if cursor.fetchone() is None:
                    db.rollback()
                    return None
                cursor = None
            if cursor is not None and cursor.rowcount != 1:
                db.rollback()
                return None
            db.execute("UPDATE runs SET status='running',updated_at=? WHERE id=?", (iso_now(), run_id))
            db.commit()
        return token

    def cancel(self, run_id):
        with closing(self.connect()) as db:
            db.execute("UPDATE agent_continuations SET status='cancelled',updated_at=? WHERE run_id=? AND status='pending'", (iso_now(), run_id))
            db.commit()

    def finish(self, run_id, token):
        with closing(self.connect()) as db:
            db.execute("UPDATE agent_continuations SET status='consumed',updated_at=? WHERE run_id=? AND token=? AND status='executing'", (iso_now(), run_id, token))
            db.commit()
