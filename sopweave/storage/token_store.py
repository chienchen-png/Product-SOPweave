"""Idempotent model-call ledger and retained context usage reader."""

from __future__ import annotations

from sqlite3 import Connection

from storage._utils import iso_now, row_to_dict


def ensure_usage_schema(db):
    """Upgrade old ledgers without attributing their incomplete rows to new statistics."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS token_usage(id INTEGER PRIMARY KEY AUTOINCREMENT)"
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(token_usage)")}
    fields = {
        **{
            name: "TEXT"
            for name in (
                "project_id",
                "conversation_id",
                "message_id",
                "agent_id",
                "model_id",
                "created_at",
                "call_id",
                "run_id",
                "execution_id",
                "boot_id",
                "provider_id",
                "provider_name",
                "provider_kind",
                "model_name",
                "api_model",
                "request_state",
                "usage_state",
                "provider_request_id",
                "finished_at",
                "usage_json",
            )
        },
        **{
            name: "INTEGER"
            for name in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
                "duration_ms",
            )
        },
    }
    for name, kind in fields.items():
        if name not in columns:
            db.execute(f"ALTER TABLE token_usage ADD COLUMN {name} {kind}")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_token_usage_call ON token_usage(call_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_token_usage_call_time ON token_usage(created_at,provider_id,model_id,total_tokens) WHERE call_id IS NOT NULL"
    )
    db.execute("DROP INDEX IF EXISTS idx_token_usage_dashboard")
    db.execute("DROP INDEX IF EXISTS idx_token_usage_time")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_token_usage_pending ON token_usage(boot_id,request_state)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS token_usage_state(id INTEGER PRIMARY KEY CHECK(id=1), started_at TEXT NOT NULL)"
    )


class TokenStore:
    def __init__(self, db: Connection) -> None:
        self.db = db

    def begin_call(
        self,
        call_id,
        identity,
        *,
        boot_id,
        created_at=None,
        run_id="",
        execution_id="",
        project_id="",
    ):
        self.db.execute(
            """INSERT OR IGNORE INTO token_usage
            (call_id,project_id,run_id,execution_id,boot_id,provider_id,provider_name,provider_kind,
             model_id,model_name,api_model,input_tokens,output_tokens,total_tokens,reasoning_tokens,request_state,usage_state,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,'pending','pending',?)""",
            (
                call_id,
                project_id or None,
                run_id,
                execution_id,
                boot_id,
                identity.provider_id,
                identity.provider_name,
                identity.provider_kind,
                identity.model_id,
                identity.model_name,
                identity.api_model,
                created_at or iso_now(),
            ),
        )
        self.db.commit()

    def finish_call(
        self, call_id, state, usage, *, provider_request_id=None, duration_ms=None
    ):
        if state not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("Invalid usage request state")
        self.db.execute(
            """UPDATE token_usage SET request_state=?,usage_state=?,input_tokens=?,output_tokens=?,
            total_tokens=?,reasoning_tokens=?,usage_json=?,provider_request_id=?,duration_ms=?,finished_at=?
            WHERE call_id=? AND request_state='pending' """,
            (
                state,
                usage.state,
                usage.input_tokens,
                usage.output_tokens,
                usage.total_tokens,
                usage.reasoning_tokens,
                usage.raw_json,
                provider_request_id,
                duration_ms,
                iso_now(),
                call_id,
            ),
        )
        self.db.commit()

    def activate(self):
        self.db.execute(
            "INSERT OR IGNORE INTO token_usage_state(id,started_at) VALUES(1,?)",
            (iso_now(),),
        )
        self.db.commit()

    def recover(self, boot_id):
        self.db.execute(
            "UPDATE token_usage SET request_state='interrupted',usage_state='missing',finished_at=? WHERE request_state='pending' AND boot_id<>?",
            (iso_now(), boot_id),
        )
        self.db.commit()

    def interrupt_execution(self, execution_id, state="interrupted"):
        if not execution_id:
            return
        self.db.execute(
            "UPDATE token_usage SET request_state=?,usage_state='missing',finished_at=? WHERE execution_id=? AND request_state='pending'",
            (state, iso_now(), execution_id),
        )
        self.db.commit()

    def project_summary(self, project_id: str, limit_hours: int = 24) -> dict:
        """Aggregate token usage for a project over the last N hours."""
        row = self.db.execute(
            f"""SELECT COUNT(*) AS calls,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens
               FROM token_usage
               WHERE project_id=?
                 AND created_at >= datetime('now', '-{int(limit_hours)} hours')""",
            (project_id,),
        ).fetchone()
        return row_to_dict(row)
