"""Transactional project history cleanup, shared by reset and deletion."""
from __future__ import annotations


def clear_project_history(db, project_id: str) -> int:
    """Caller owns transaction. Work directory files are never removed."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    active = db.execute(
        "SELECT 1 FROM runs WHERE project_id=? "
        "AND status IN ('starting','running','resuming','waiting') LIMIT 1",
        (project_id,),
    ).fetchone()
    if active:
        raise ValueError("项目存在正在执行的任务，请停止任务后再清理")
    conversations = {row[0] for row in db.execute("SELECT id FROM conversations WHERE project_id=?", (project_id,))}
    runs = db.execute("SELECT id,conversation_id FROM runs WHERE project_id=?", (project_id,)).fetchall()
    conversations.update(row[1] for row in runs if row[1])
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for conversation in conversations:
        # substr uses literal prefixes (LIKE would treat '_' and '%' as wildcards).
        db.execute("DELETE FROM agent_session_items WHERE session_id=? OR substr(session_id,1,?)=?", (conversation, len(conversation) + 1, conversation + ":"))
        db.execute("DELETE FROM messages WHERE conversation_id=?", (conversation,))
    for run_id, _ in runs:
        for table in ("agent_interactions", "agent_run_requests", "agent_continuations", "agent_run_checkpoints", "run_events", "interactions"):
            if table in tables:
                db.execute(f'DELETE FROM "{table}" WHERE run_id=?', (run_id,))
        db.execute("DELETE FROM plan_steps WHERE plan_id IN (SELECT id FROM plans WHERE run_id=?)", (run_id,))
        db.execute("DELETE FROM plans WHERE run_id=?", (run_id,))
    db.execute("DELETE FROM runs WHERE project_id=?", (project_id,))
    db.execute("DELETE FROM conversations WHERE project_id=?", (project_id,))
    db.execute("DELETE FROM tool_calls WHERE project_id=?", (project_id,))
    return len(conversations)
