"""Compatibility read APIs for persisted Agents SDK conversations and runs."""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import Blueprint, request

from routes._utils import error, success
from storage.db import get_db
from storage.run_store import RunStore

logger = logging.getLogger(__name__)
workspace_bp = Blueprint("workspace_api", __name__, url_prefix="/api/workspace")


@workspace_bp.post("/context/reset")
def reset_context():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not isinstance(data.get("project_id", ""), str):
        return error("请求必须包含有效的 project_id 字符串")
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return error("缺少 project_id")
    try:
        db = get_db()
        from storage.project_lifecycle import clear_project_history

        with db:
            count = clear_project_history(db, project_id)
        return success({"status": "reset", "sessions_cleared": count})
    except ValueError as exc:
        return error(str(exc), 409)
    except Exception as exc:
        logger.exception("context reset failed")
        return error(f"清空上下文失败: {exc}")


@workspace_bp.get("/context/messages")
def load_messages():
    project_id = request.args.get("project_id", "").strip()
    if not project_id:
        return error("缺少 project_id")
    db = get_db()
    row = db.execute(
        "SELECT id FROM conversations WHERE project_id=? ORDER BY updated_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if not row:
        return success({"messages": []})
    messages = [
        dict(item)
        for item in db.execute(
            "SELECT role, content, agent_id, model_id, created_at FROM messages "
            "WHERE conversation_id=? ORDER BY created_at, id",
            (row["id"],),
        ).fetchall()
    ]
    return success({"messages": messages})


@workspace_bp.get("/history")
def workspace_history():
    project_id = request.args.get("project_id", "").strip()
    if not project_id:
        return error("缺少 project_id")
    try:
        db = get_db()
        store = RunStore(db)
        rows = db.execute(
            """SELECT id, title, status, created_at, conversation_id,
                      user_message_id, agent_id, model_id, error_message
               FROM runs WHERE project_id=? ORDER BY created_at ASC""",
            (project_id,),
        ).fetchall()
        return success({"runs": [_history_run(db, store, dict(row)) for row in rows]})
    except Exception as exc:
        logger.exception("workspace history failed")
        return error(f"加载运行历史失败: {exc}")


from services.conversation_history import history_run as _history_run
