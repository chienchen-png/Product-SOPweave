"""Canonical history reconstruction shared by UI and SOP extraction."""

from __future__ import annotations
import json
from typing import Any
from storage.run_store import RunStore


def history_run(db, store: RunStore, run: dict[str, Any]) -> dict[str, Any]:
    reasoning: list[str] = []
    text: list[str] = []
    tool_cards: list[dict[str, Any]] = []
    open_tools: dict[str, dict[str, Any]] = {}
    plan = None
    context_omitted_items = 0
    error_message = run.get("error_message") or ""
    events = store.get_events_after(run["id"], 0)
    for event in events:
        event_type = str(event.get("event_type") or event.get("type") or "")
        canonical = event_type.replace("_", ".")
        payload = event.get("payload") or {}
        if canonical == "context.updated":
            context_omitted_items = max(context_omitted_items, int(payload.get("omitted_items") or 0))
        if canonical == "reasoning.delta":
            reasoning.append(str(payload.get("delta") or payload.get("text") or ""))
        elif canonical == "message.delta":
            text.append(str(payload.get("delta") or payload.get("text") or ""))
        elif canonical == "tool.started":
            tool_id = str(
                payload.get("tool_id") or payload.get("id") or f"t{len(tool_cards)}"
            )
            open_tools[tool_id] = {
                "id": tool_id,
                "name": str(payload.get("name") or ""),
                "title": str(payload.get("title") or payload.get("name") or ""),
                "input": _render(payload.get("arguments") or payload.get("input")),
                "output": "",
                "status": "running",
            }
        elif canonical == "tool.completed":
            tool_id = str(payload.get("tool_id") or payload.get("id") or "")
            card = open_tools.pop(
                tool_id,
                {
                    "id": tool_id,
                    "name": str(payload.get("name") or ""),
                    "title": str(payload.get("name") or ""),
                    "input": "",
                },
            )
            card.update(
                {
                    "output": _render(payload.get("output")),
                    "status": (
                        "failed"
                        if isinstance(payload.get("output"), dict)
                        and payload["output"].get("success") is False
                        else str(payload.get("status") or "completed")
                    ),
                    "duration_ms": int(payload.get("duration_ms") or 0),
                }
            )
            tool_cards.append(card)
        elif canonical in {"plan.proposed", "plan.updated"}:
            plan = {
                "steps": payload.get("steps") or [],
                "summary": payload.get("summary") or "",
            }
        elif canonical == "run.failed":
            error_message = str(
                payload.get("message") or error_message or "任务执行失败"
            )
    tool_cards.extend(
        {
            **card,
            "status": (
                "waiting"
                if run.get("status") == "waiting"
                else (
                    "running"
                    if run.get("status") in {"starting", "running", "resuming"}
                    else "failed"
                )
            ),
        }
        for card in open_tools.values()
    )
    if plan:
        saved_plan = store.get_latest_plan(run["id"])
        if saved_plan and saved_plan.get("steps"):
            plan["steps"] = saved_plan["steps"]
    user_prompt = str(run.get("title") or "")
    if run.get("user_message_id"):
        row = db.execute(
            "SELECT content FROM messages WHERE id=?", (run["user_message_id"],)
        ).fetchone()
        if row and row["content"]:
            user_prompt = str(row["content"])
    if db.execute(
        "SELECT name FROM sqlite_master WHERE name='agent_run_requests'"
    ).fetchone():
        request = db.execute(
            "SELECT request_json FROM agent_run_requests WHERE run_id=?", (run["id"],)
        ).fetchone()
        if request:
            user_prompt = str(json.loads(request[0]).get("prompt") or user_prompt)
    snapshot = db.execute(
        "SELECT snapshot_json FROM skill_run_contexts WHERE run_id=?", (run["id"],)
    ).fetchone()
    sop_ref = json.loads(snapshot[0]).get("explicit_ref") if snapshot else None
    return {
        "run_id": run["id"],
        "last_seq": events[-1]["seq"] if events else 0,
        "sop_ref": sop_ref,
        "title": run.get("title") or "",
        "status": run.get("status") or "",
        "error_message": error_message,
        "created_at": run.get("created_at") or "",
        "conversation_id": run.get("conversation_id") or "",
        "user_prompt": user_prompt,
        "reasoning": "".join(reasoning),
        "context_omitted_items": context_omitted_items,
        "tool_cards": tool_cards,
        "assistant_text": "".join(text),
        "plan": plan,
    }


def _render(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def conversation_source(db, project_id, conversation_id, run_ids=None):
    from services.skills.contracts import SkillProblem
    from services.skills.authoring import redact_source

    rows = db.execute(
        "SELECT * FROM runs WHERE project_id=? AND status='completed'"
        + (" AND conversation_id=?" if conversation_id else "")
        + " ORDER BY created_at DESC LIMIT 100",
        (project_id, conversation_id) if conversation_id else (project_id,),
    ).fetchall()
    selected = set(run_ids or [])
    rows = [
        dict(row) for row in reversed(rows) if not selected or row["id"] in selected
    ]
    if not rows or selected - set(row["id"] for row in rows):
        raise SkillProblem("所选对话不存在或不属于当前项目")
    history = [history_run(db, RunStore(db), row) for row in rows]
    text = redact_source(
        "\n\n".join(
            "用户：" + item["user_prompt"] + "\n助手：" + item["assistant_text"]
            for item in history
        )
    )
    if len(text) > 100000:
        raise SkillProblem("选中对话超过100000字符，请缩小范围")
    return {
        "kind": "conversation",
        "text": text,
        "conversation_id": conversation_id,
        "run_ids": [row["id"] for row in rows],
    }
