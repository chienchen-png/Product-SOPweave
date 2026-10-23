from __future__ import annotations

import json
import asyncio
import sqlite3
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storage._utils import iso_now


@dataclass(frozen=True)
class WorkbenchEvent:
    run_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "type": self.type,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class EventWriter:
    def __init__(self, db_path: str | Path, run_id: str) -> None:
        self.db_path = Path(db_path)
        self.run_id = run_id
        self._lock = threading.RLock()
        self._connection = self._connect()
        self._finalizer = weakref.finalize(self, self._connection.close)

    def close(self):
        with self._lock:
            self._finalizer()
            self._connection = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await asyncio.to_thread(self.close)

    async def awrite(self, event_type, payload=None):
        return await asyncio.to_thread(self.write, event_type, payload)

    def write(
        self, event_type: str, payload: dict[str, Any] | None = None
    ) -> WorkbenchEvent:
        created_at = iso_now()
        safe_payload = _jsonable(payload or {})
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("EventWriter 已关闭")
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                seq = connection.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 FROM run_events WHERE run_id=?",
                    (self.run_id,),
                ).fetchone()[0]
                connection.execute(
                    """INSERT INTO run_events
                       (run_id, seq, event_type, payload_json, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        self.run_id,
                        seq,
                        event_type,
                        json.dumps(safe_payload, ensure_ascii=False),
                        created_at,
                    ),
                )
        return WorkbenchEvent(
            run_id=self.run_id,
            seq=seq,
            type=event_type,
            payload=safe_payload,
            created_at=created_at,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=10, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection


def map_sdk_event(event: Any) -> list[tuple[str, dict[str, Any]]]:
    if getattr(event, "type", "") == "raw_response_event":
        data = event.data
        if getattr(data, "type", "") == "response.output_text.delta":
            return [("message.delta", {"delta": str(getattr(data, "delta", ""))})]
        if getattr(data, "type", "") in {
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        }:
            return [("reasoning.delta", {"delta": str(getattr(data, "delta", ""))})]
        return []
    if getattr(event, "type", "") != "run_item_stream_event":
        return []
    item = event.item
    if event.name == "tool_called":
        raw = item.raw_item
        return [
            (
                "tool.started",
                {
                    "tool_id": str(
                        getattr(item, "call_id", None)
                        or _field(raw, "call_id")
                        or _field(raw, "id")
                        or ""
                    ),
                    "name": str(
                        getattr(item, "tool_name", None) or _field(raw, "name") or ""
                    ),
                    "arguments": _parse_json(_field(raw, "arguments")),
                },
            )
        ]
    if event.name == "tool_output":
        output = _jsonable(getattr(item, "output", None))
        mapped = [
            (
                "tool.completed",
                {
                    "tool_id": str(getattr(item, "call_id", None) or ""),
                    "output": output,
                },
            )
        ]
        if isinstance(output, dict) and output.get("status") == "input_required":
            mapped.append(
                (
                    "input.required",
                    {
                        **output,
                        "tool_call_id": str(
                            getattr(item, "call_id", None)
                            or _field(item.raw_item, "call_id")
                            or ""
                        ),
                    },
                )
            )
        return mapped
    return []


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value or {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}


def _jsonable(value: Any) -> Any:
    def default(item: Any):
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if hasattr(item, "item"):
            return item.item()
        return str(item)

    return json.loads(json.dumps(value, ensure_ascii=False, default=default))
