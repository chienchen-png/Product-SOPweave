from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from config.settings import DB_PATH
from storage._utils import iso_now
from storage.db import _ensure_agent_runtime_schema


class CheckpointError(RuntimeError):
    pass


class CheckpointNotFoundError(CheckpointError):
    pass


class CheckpointConflictError(CheckpointError):
    pass


@dataclass(frozen=True)
class CheckpointRecord:
    run_id: str
    checkpoint_json: str
    sdk_version: str
    status: str
    lease_token: str | None
    updated_at: str


class CheckpointStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or DB_PATH)
        with closing(self._connect()) as connection:
            _ensure_agent_runtime_schema(connection)
            connection.commit()

    def save(
        self,
        run_id: str,
        checkpoint_json: str,
        *,
        sdk_version: str,
        new_round: bool = False,
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status FROM agent_run_checkpoints WHERE run_id=?", (run_id,)
                ).fetchone()
                if row and (
                    row["status"] == "acquired"
                    or (row["status"] == "completed" and not new_round)
                ):
                    raise CheckpointConflictError(
                        f"checkpoint 当前不可覆盖: {run_id} ({row['status']})"
                    )
                connection.execute(
                    """INSERT INTO agent_run_checkpoints
                       (run_id, checkpoint_json, sdk_version, status, lease_token, updated_at)
                       VALUES (?, ?, ?, 'ready', NULL, ?)
                       ON CONFLICT(run_id) DO UPDATE SET
                         checkpoint_json=excluded.checkpoint_json,
                         sdk_version=excluded.sdk_version,
                         status='ready', lease_token=NULL, updated_at=excluded.updated_at""",
                    (run_id, checkpoint_json, sdk_version, iso_now()),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def load(self, run_id: str) -> CheckpointRecord:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM agent_run_checkpoints WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise CheckpointNotFoundError(f"checkpoint 不存在: {run_id}")
        return _record(row)

    def acquire(self, run_id: str) -> CheckpointRecord:
        token = uuid.uuid4().hex
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM agent_run_checkpoints WHERE run_id=?", (run_id,)
                ).fetchone()
                if row is None:
                    raise CheckpointNotFoundError(f"checkpoint 不存在: {run_id}")
                if row["status"] != "ready":
                    raise CheckpointConflictError(
                        f"checkpoint 不可恢复: {run_id} ({row['status']})"
                    )
                connection.execute(
                    """UPDATE agent_run_checkpoints
                       SET status='acquired', lease_token=?, updated_at=?
                       WHERE run_id=? AND status='ready'""",
                    (token, iso_now(), run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.load(run_id)

    def release(self, run_id: str, lease_token: str | None) -> None:
        self._transition(run_id, lease_token, "ready")

    def replace_acquired(
        self,
        run_id: str,
        lease_token: str | None,
        checkpoint_json: str,
        *,
        sdk_version: str,
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """UPDATE agent_run_checkpoints
                       SET checkpoint_json=?, sdk_version=?, status='ready',
                           lease_token=NULL, updated_at=?
                       WHERE run_id=? AND status='acquired' AND lease_token=?""",
                    (checkpoint_json, sdk_version, iso_now(), run_id, lease_token),
                )
                if cursor.rowcount != 1:
                    raise CheckpointConflictError(
                        f"checkpoint lease 无效或状态冲突: {run_id}"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def complete(self, run_id: str, lease_token: str | None) -> None:
        self._transition(run_id, lease_token, "completed")

    def _transition(self, run_id: str, lease_token: str | None, status: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """UPDATE agent_run_checkpoints
                       SET status=?, lease_token=NULL, updated_at=?
                       WHERE run_id=? AND status='acquired' AND lease_token=?""",
                    (status, iso_now(), run_id, lease_token),
                )
                if cursor.rowcount != 1:
                    raise CheckpointConflictError(
                        f"checkpoint lease 无效或状态冲突: {run_id}"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection


def _record(row: sqlite3.Row) -> CheckpointRecord:
    return CheckpointRecord(
        run_id=row["run_id"],
        checkpoint_json=row["checkpoint_json"],
        sdk_version=row["sdk_version"],
        status=row["status"],
        lease_token=row["lease_token"],
        updated_at=row["updated_at"],
    )
