"""Persisted context occupancy statistics for the Agents SDK runtime."""

from __future__ import annotations

import json

from flask import Blueprint, request

from agent_runtime.context_budget import ContextBudget, ContextLimitError, estimate_tokens, without_skill_transcripts
from routes._utils import success
from storage.db import get_db
from storage.model_store import ModelStore
from storage.project_store import ProjectStore
from storage.token_store import TokenStore


context_bp = Blueprint("context_api", __name__, url_prefix="/api/context")
def _resolve_window_for_project(project_id: str) -> tuple[int, str]:
    db = get_db()
    project = ProjectStore(db).get_by_id(project_id) or {}
    model = ModelStore(db).get_available_model(project.get("default_model") or "") or {}
    budget = ContextBudget.from_record(model)
    return budget.context_window, budget.window_source


@context_bp.get("/stats")
def context_stats():
    project_id = request.args.get("project_id", "").strip()
    db = get_db()
    context_window, context_window_source = _resolve_window_for_project(project_id)
    budget = ContextBudget(context_window, window_source=context_window_source)
    reserve = budget.response_reserve
    project = ProjectStore(db).get_by_id(project_id) or {}
    model_id = project.get("default_model") or ""
    agent_id = project.get("default_agent") or "general-agent"

    latest_run = db.execute(
        "SELECT conversation_id FROM runs WHERE project_id=? "
        "AND (?='' OR model_id=?) AND agent_id=? "
        "ORDER BY updated_at DESC, id DESC LIMIT 1",
        (project_id, model_id, model_id, agent_id),
    ).fetchone()
    if latest_run:
        conversation_id = latest_run["conversation_id"] or project_id
    else:
        conversation = db.execute(
            "SELECT id FROM conversations WHERE project_id=? ORDER BY updated_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        conversation_id = conversation["id"] if conversation else project_id
    session_rows = []
    current_tokens = 0
    omitted_items = 0
    sdk_message_count = 0
    tool_result_count = 0
    context_files_count = 0
    partitions = {"messages": 0, "reasoning": 0, "tools": 0}
    if conversation_id:
        session_id = f"{conversation_id}:{agent_id}:{model_id}"
        if not model_id:
            latest = db.execute(
                "SELECT session_id FROM agent_session_items WHERE session_id=? OR substr(session_id,1,?)=? "
                "ORDER BY created_at DESC,seq DESC LIMIT 1",
                (conversation_id,len(conversation_id)+1,conversation_id+":"),
            ).fetchone()
            session_id = latest["session_id"] if latest else session_id
        session_rows = db.execute(
            "SELECT item_json FROM agent_session_items WHERE session_id=? ORDER BY seq",
            (session_id,),
        ).fetchall()
        items = without_skill_transcripts([json.loads(row['item_json']) for row in session_rows])
        if items:
            try:
                selected = budget.select(items)
                items = selected.items
                current_tokens,omitted_items = selected.estimated_tokens,selected.omitted_items
            except ContextLimitError:
                current_tokens = sum(estimate_tokens(item)+12 for item in items)+256
        for item in items:
            item_tokens = estimate_tokens(item) + 12
            kind = str(item.get("type") or "")
            role = str(item.get("role") or "")
            content = item.get("content")
            if kind == "reasoning":
                partitions["reasoning"] += item_tokens
            elif kind in {"function_call", "function_call_output"} or role == "tool":
                tool_result_count += kind == "function_call_output"
                partitions["tools"] += item_tokens
            elif kind == "message" or role in {"user", "assistant"} or (
                not kind and content
            ):
                sdk_message_count += 1
                partitions["messages"] += item_tokens
                text = str(content or "")
                context_files_count += text.count("@file:") + text.count("@folder:")

    estimate_scope = "selected_session_history"
    snapshot_row = db.execute(
        "SELECT e.payload_json FROM run_events e JOIN runs r ON r.id=e.run_id "
        "WHERE r.project_id=? AND (?='' OR r.model_id=?) AND r.agent_id=? "
        "AND r.conversation_id=? AND e.event_type='context.updated' ORDER BY r.updated_at DESC,e.seq DESC LIMIT 1",
        (project_id,model_id,model_id,agent_id,conversation_id),
    ).fetchone()
    if snapshot_row:
        snapshot = json.loads(snapshot_row["payload_json"])
        if snapshot.get("context_window") == context_window:
            current_tokens = snapshot["current_tokens"]
            reserve = snapshot["response_reserve"]
            omitted_items = snapshot["omitted_items"]
            sdk_message_count = snapshot["message_count"]
            estimate_scope = "last_request"
    effective_budget = context_window - reserve
    usage_pct = min(
        100,
        round(current_tokens / effective_budget * 100, 1)
        if effective_budget > 0
        else 100,
    )
    try:
        consumed = TokenStore(db).project_summary(project_id, limit_hours=24)
    except Exception:
        consumed = {}
    return success({
        "current_tokens": current_tokens,
        "context_window": context_window,
        "context_window_source": context_window_source,
        "response_reserve": reserve,
        "effective_budget": effective_budget,
        "usage_pct": usage_pct,
        "message_count": sdk_message_count,
        "omitted_items": omitted_items,
        "estimate_scope": estimate_scope,
        "estimate_method": "conservative_utf8_bytes",
        "context_files_count": context_files_count,
        "tool_result_count": tool_result_count,
        "partitions": partitions,
        "estimated": True,
        "consumed_tokens": int(consumed.get("total_tokens") or 0),
        "consumed_calls": int(consumed.get("calls") or 0),
    })
