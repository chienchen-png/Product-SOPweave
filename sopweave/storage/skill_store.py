"""SQL operations for skills. Transaction ownership belongs to the caller."""

from __future__ import annotations

import json
import sqlite3
import uuid
from storage._utils import iso_now
from services.skills.contracts import SkillConflict, SkillMissing


def ensure_skill_availability_schema(db: sqlite3.Connection) -> None:
    """Add per-SOP availability without rebuilding or deleting history."""
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_skills'"
    ).fetchone()
    if not exists:
        return
    columns = {row[1] for row in db.execute("PRAGMA table_info(user_skills)")}
    if "enabled" not in columns:
        try:
            db.execute(
                "ALTER TABLE user_skills ADD COLUMN enabled INTEGER NOT NULL "
                "DEFAULT 1 CHECK(enabled IN (0,1))"
            )
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).casefold():
                raise


def decode(row):
    if row is None:
        return None
    result = dict(row)
    for name in list(result):
        if name.endswith("_json"):
            result[name[:-5]] = json.loads(result.pop(name))
    return result


class SkillStore:
    def __init__(self, connection: sqlite3.Connection):
        self.db = connection

    def generation(self) -> int:
        return self.db.execute(
            "SELECT generation FROM skill_catalog_state WHERE id=1"
        ).fetchone()[0]

    def bump_generation(self):
        self.db.execute(
            "UPDATE skill_catalog_state SET generation=generation+1 WHERE id=1"
        )

    def create_draft(
        self, *, base_skill_id=None, base_version=None, source=None
    ) -> dict:
        draft_id = "draft-" + uuid.uuid4().hex
        now = iso_now()
        self.db.execute(
            "INSERT INTO skill_drafts(draft_id,base_skill_id,base_version,source_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (
                draft_id,
                base_skill_id,
                base_version,
                json.dumps(source or [], ensure_ascii=False),
                now,
                now,
            ),
        )
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM skill_drafts WHERE draft_id=?", (draft_id,)
        ).fetchone()
        if row is None:
            raise SkillMissing("草稿不存在")
        return decode(row)

    def list_drafts(self, *, limit=21, after='') -> list[dict]:
        params = []
        predicate = "state<>'published'"
        if after:
            cursor = self.db.execute('SELECT updated_at,draft_id FROM skill_drafts WHERE draft_id=?', (after,)).fetchone()
            if cursor is None:
                raise ValueError('草稿分页位置无效，请刷新列表')
            predicate += ' AND (updated_at,draft_id)<(?,?)'
            params.extend(cursor)
        rows = self.db.execute(
            f"SELECT draft_id,draft_revision,state,updated_at,COALESCE(json_extract(spec_json,'$.title'),substr(json_extract(source_json,'$[0].text'),1,60),'未命名草稿') AS title FROM skill_drafts WHERE {predicate} ORDER BY updated_at DESC,draft_id DESC LIMIT ?",
            (*params,limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_draft_cas(self, draft_id: str, expected_revision: int, **fields) -> dict:
        allowed = {
            "state",
            "spec_json",
            "source_json",
            "questions_json",
            "answers_json",
            "generated_digest",
            "job_id",
            "job_status_json",
            "publication_ref",
        }
        if not fields or set(fields) - allowed:
            raise ValueError("无效的草稿更新字段")
        assignments = ",".join(f"{name}=?" for name in fields)
        cursor = self.db.execute(
            f"UPDATE skill_drafts SET {assignments},draft_revision=draft_revision+1,updated_at=? WHERE draft_id=? AND draft_revision=? AND state<>'published'",
            (*fields.values(), iso_now(), draft_id, expected_revision),
        )
        if cursor.rowcount != 1:
            self.get_draft(draft_id)
            raise SkillConflict("草稿已发生变化，请重新加载最新预览")
        return self.get_draft(draft_id)

    def get_active_version(
        self, skill_id: str, version=None, *, include_disabled: bool = False
    ) -> dict:
        enabled = "" if include_disabled else " AND s.enabled=1"
        row = self.db.execute(
            "SELECT v.*,s.title,s.status,s.enabled FROM user_skills s "
            "JOIN user_skill_versions v ON v.skill_id=s.skill_id "
            "AND v.version=COALESCE(?,s.current_version) "
            f"WHERE s.skill_id=? AND s.status='active'{enabled}",
            (version, skill_id),
        ).fetchone()
        if row is None:
            raise SkillMissing("Skill 不存在或已删除")
        return decode(row)

    def list_active(
        self, *, query="", limit=50, after="", include_disabled=False
    ) -> list[dict]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        enabled = "" if include_disabled else " AND s.enabled=1"
        return [
            decode(row)
            for row in self.db.execute(
                "SELECT s.skill_id,s.title,s.current_version AS version,s.enabled,"
                "v.content_digest,v.summary_json FROM user_skills s "
                "JOIN user_skill_versions v ON v.skill_id=s.skill_id "
                "AND v.version=s.current_version "
                "WHERE s.status='active' AND s.skill_id>? "
                "AND (s.title LIKE ? ESCAPE '\\' OR v.description LIKE ? ESCAPE '\\')"
                f"{enabled} ORDER BY s.skill_id LIMIT ?",
                (after, f"%{escaped}%", f"%{escaped}%", max(1, min(int(limit), 100))),
            )
        ]

    def list_run_refs(self, skill_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT r.* FROM skill_run_refs r JOIN runs ON runs.id=r.run_id WHERE r.skill_id=? AND r.lease_state='active' AND runs.status IN ('pending','starting','running','resuming','waiting')",
                (skill_id,),
            )
        ]
