from __future__ import annotations

import json
import asyncio
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from agents import SessionSettings
from agent_runtime.context_budget import without_skill_transcripts

from config.settings import DB_PATH
from storage._utils import iso_now
from storage.db import _ensure_agent_runtime_schema


class WorkbenchSession:
    def __init__(
        self,
        session_id: str,
        *,
        db: sqlite3.Connection | None = None,
        db_path: str | Path | None = None,
        session_settings: SessionSettings | None = None,
        filter_skill_history: bool = False,
    ) -> None:
        if not str(session_id).strip():
            raise ValueError("session_id 不能为空")
        self.session_id = str(session_id)
        self.session_settings = session_settings
        self.filter_skill_history = filter_skill_history
        self._db = db
        self._db_path = Path(db_path or DB_PATH)
        self._lock = threading.RLock()
        if self._db is None:
            with self._connection() as connection:
                _ensure_agent_runtime_schema(connection)
                connection.commit()

    async def get_items(self, limit: int | None = None):
        # Injected connections belong to the caller thread; production uses db_path.
        if self._db is not None:
            return self._get_items(limit)
        return await asyncio.to_thread(self._get_items, limit)

    def _get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None and limit <= 0:
            return []
        with self._lock, self._connection() as connection:
            count = int(limit) if limit is not None else -1
            rows = connection.execute(
                """SELECT item_json FROM (
                       SELECT seq, item_json FROM agent_session_items
                       WHERE session_id=? ORDER BY seq DESC LIMIT ?
                   ) ORDER BY seq""", (self.session_id, count),
            ).fetchall()
            items = [json.loads(row[0]) for row in rows]
            if self.filter_skill_history:
                items = without_skill_transcripts(items)
            if limit is not None:
                return items
            return items

    async def add_items(self, items: list[Any]):
        # Injected connections belong to the caller thread; production uses db_path.
        if self._db is not None:
            return self._add_items(items)
        return await asyncio.to_thread(self._add_items, items)

    def _add_items(self, items: list[Any]) -> None:
        if not items:
            return
        encoded = [json.dumps(_item_value(item), ensure_ascii=False) for item in items]
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM agent_session_items WHERE session_id=?",
                    (self.session_id,),
                ).fetchone()
                start = int(row[0])
                connection.executemany(
                    """INSERT INTO agent_session_items
                       (session_id, seq, item_json, created_at) VALUES (?, ?, ?, ?)""",
                    [
                        (self.session_id, start + index, value, iso_now())
                        for index, value in enumerate(encoded, 1)
                    ],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    async def pop_item(self):
        # Injected connections belong to the caller thread; production uses db_path.
        if self._db is not None:
            return self._pop_item()
        return await asyncio.to_thread(self._pop_item)

    def _pop_item(self) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """SELECT seq, item_json FROM agent_session_items
                       WHERE session_id=? ORDER BY seq DESC LIMIT 1""",
                    (self.session_id,),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                connection.execute(
                    "DELETE FROM agent_session_items WHERE session_id=? AND seq=?",
                    (self.session_id, row["seq"] if isinstance(row, sqlite3.Row) else row[0]),
                )
                connection.commit()
                raw = row["item_json"] if isinstance(row, sqlite3.Row) else row[1]
                return json.loads(raw)
            except Exception:
                connection.rollback()
                raise

    async def clear_session(self):
        # Injected connections belong to the caller thread; production uses db_path.
        if self._db is not None:
            return self._clear_session()
        return await asyncio.to_thread(self._clear_session)

    def _clear_session(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "DELETE FROM agent_session_items WHERE session_id=?", (self.session_id,)
            )
            connection.commit()

    def _connection(self):
        if self._db is not None:
            return _BorrowedConnection(self._db)
        connection = sqlite3.connect(str(self._db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return closing(connection)


class _BorrowedConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self.connection

    def __exit__(self, *_args: object) -> None:
        return None


def _item_value(item: Any) -> Any:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", exclude_none=False)
    if is_dataclass(item):
        return asdict(item)
    return item
