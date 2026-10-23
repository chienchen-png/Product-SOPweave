"""Persistent, validated human input; consuming an answer never executes a tool."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from pydantic import Field
from agent_runtime.tools.contracts import Arguments
from storage._utils import iso_now


class InteractionError(ValueError):
    pass


class Answer(Arguments):
    question_id: str
    selected: list[str] = Field(default_factory=list, max_length=20)
    text: str = Field(default="", max_length=10000)
    confirmed: bool | None = None


class InteractionStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        with closing(self.connect()) as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS agent_interactions(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
                kind TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                response_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(run_id,tool_call_id,kind));
                CREATE INDEX IF NOT EXISTS idx_agent_interactions_pending ON agent_interactions(run_id,status);""")
            db.commit()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def create(self, run_id, tool_call_id, kind, payload):
        with closing(self.connect()) as db:
            identifier = f"interaction-{uuid.uuid4().hex}"
            now = iso_now()
            db.execute(
                "INSERT OR IGNORE INTO agent_interactions(id,run_id,tool_call_id,kind,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    identifier,
                    run_id,
                    tool_call_id,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            db.commit()
            row = db.execute(
                "SELECT * FROM agent_interactions WHERE run_id=? AND tool_call_id=? AND kind=?",
                (run_id, tool_call_id, kind),
            ).fetchone()
        return self.record(row)

    @staticmethod
    def record(row):
        return {
            **json.loads(row["payload_json"]),
            "interaction_id": row["id"],
            "run_id": row["run_id"],
            "tool_call_id": row["tool_call_id"],
            "kind": row["kind"],
            "version": row["version"],
            "status": row["status"],
            "response": json.loads(row["response_json"])
            if row["response_json"]
            else None,
        }

    def list(self, run_id, status=None):
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT * FROM agent_interactions WHERE run_id=?"
                + (" AND status=?" if status else "")
                + " ORDER BY created_at,id",
                (run_id, status) if status else (run_id,),
            ).fetchall()
        return [self.record(row) for row in rows]

    def validate(self, run_id, command, db=None):
        if db is None:
            with closing(self.connect()) as connection:
                return self.validate(run_id, command, connection)
        row = db.execute(
            "SELECT * FROM agent_interactions WHERE id=? AND run_id=?",
            (command.interaction_id, run_id),
        ).fetchone()
        if row is None or row["version"] != command.version:
            raise InteractionError("交互不存在或版本已变化，请刷新待处理列表")
        record = self.record(row)
        reply = {
            "action": command.action,
            "answers": command.answers,
            "disposition": command.disposition,
            "note": command.note,
        }
        if row["status"] == "resolved":
            if record["response"] != reply:
                raise InteractionError("该交互已经提交过不同答复")
            return record, reply, True
        if row["status"] != "pending":
            raise InteractionError("该交互已取消")
        run = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run or run["status"] != "waiting":
            raise InteractionError("运行当前不在等待交互状态")
        if record["kind"] == "approval":
            if command.disposition != "accept":
                raise InteractionError("审批只支持批准或拒绝")
            if command.action not in {"approve", "reject"}:
                raise InteractionError("此交互需要批准或拒绝")
            if command.tool_call_id and command.tool_call_id != record["tool_call_id"]:
                raise InteractionError("工具调用 ID 不匹配")
            if command.answers:
                raise InteractionError("审批不接受问题答案")
        else:
            if command.action != "respond":
                raise InteractionError("此交互需要回答问题")
            if command.disposition == "cancel":
                return record, reply, False
            if command.disposition == "skip":
                if any(q["required"] for q in record["questions"]):
                    raise InteractionError("必答问题不能跳过")
                return record, reply, False
            try:
                answers = [Answer.model_validate(answer) for answer in command.answers]
            except ValueError as exc:
                raise InteractionError("回答格式无效") from exc
            by_id = {answer.question_id: answer for answer in answers}
            if len(by_id) != len(answers):
                raise InteractionError("问题答案不能重复")
            questions = {question["id"]: question for question in record["questions"]}
            if set(by_id) - set(questions):
                raise InteractionError("回答包含未知问题")
            for identifier, question in questions.items():
                answer = by_id.get(identifier)
                if answer is None:
                    if question["required"]:
                        raise InteractionError("请填写所有必答问题")
                    continue
                options = {option["id"] for option in question["options"]}
                if (
                    not question["required"]
                    and not answer.selected
                    and not answer.text.strip()
                    and answer.confirmed is None
                ):
                    continue
                if set(answer.selected) - options or len(set(answer.selected)) != len(
                    answer.selected
                ):
                    raise InteractionError("回答包含无效或重复选项")
                kind = question["kind"]
                if kind == "text" and (answer.selected or answer.confirmed is not None):
                    raise InteractionError("文本题只能填写文本")
                if kind == "confirm" and (
                    answer.confirmed is None or answer.selected or answer.text
                ):
                    raise InteractionError("确认题需要明确的是/否")
                if kind in {"single", "multiple"}:
                    if answer.confirmed is not None or (
                        kind == "single" and len(answer.selected) > 1
                    ):
                        raise InteractionError("选择数量无效")
                    if answer.text and not question["allow_custom"]:
                        raise InteractionError("此问题不接受自定义补充")
                if (
                    question["required"]
                    and kind != "confirm"
                    and not answer.selected
                    and not answer.text.strip()
                ):
                    raise InteractionError("必答问题不能为空")
        return record, reply, False

    def resolve(self, run_id, command):
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                record, reply, duplicate = self.validate(run_id, command, db)
                if not duplicate:
                    db.execute(
                        "UPDATE agent_interactions SET status='resolved',response_json=?,updated_at=? WHERE id=? AND status='pending'",
                        (
                            json.dumps(reply, ensure_ascii=False),
                            iso_now(),
                            record["interaction_id"],
                        ),
                    )
                remaining = db.execute(
                    "SELECT COUNT(*) FROM agent_interactions WHERE run_id=? AND status='pending'",
                    (run_id,),
                ).fetchone()[0]
                if not duplicate and not remaining:
                    db.execute(
                        "UPDATE runs SET status='resuming' WHERE id=? AND status='waiting'",
                        (run_id,),
                    )
                    db.execute(
                        """INSERT INTO agent_continuations(run_id,status,updated_at)
                           VALUES(?,'pending',?) ON CONFLICT(run_id) DO UPDATE SET
                           status='pending',owner_pid=NULL,token=NULL,updated_at=excluded.updated_at""",
                        (run_id, iso_now()),
                    )
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return record, duplicate, remaining

    def cancel(self, run_id):
        with closing(self.connect()) as db:
            db.execute(
                "UPDATE agent_interactions SET status='cancelled',updated_at=? WHERE run_id=? AND status='pending'",
                (iso_now(), run_id),
            )
            db.commit()
