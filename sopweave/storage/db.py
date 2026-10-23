import json
import sqlite3
from pathlib import Path
from typing import Set

from flask import Flask, g

from config.settings import DB_PATH
from storage.path_migration import migrate_portable_paths
from storage.provider_migration import migrate_provider_configuration
from storage.skill_store import ensure_skill_availability_schema

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _table_columns(db: sqlite3.Connection, table_name: str) -> Set[str]:
    rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _ensure_model_schema(db: sqlite3.Connection) -> None:
    model_columns = _table_columns(db, "models")
    migrations = {
        "provider_id": "ALTER TABLE models ADD COLUMN provider_id TEXT",
        "capabilities": "ALTER TABLE models ADD COLUMN capabilities TEXT",
        "context_window": "ALTER TABLE models ADD COLUMN context_window INTEGER DEFAULT 8192",
        "api_model": "ALTER TABLE models ADD COLUMN api_model TEXT",
        "capabilities_json": "ALTER TABLE models ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '{}'",
        "request_options_json": "ALTER TABLE models ADD COLUMN request_options_json TEXT NOT NULL DEFAULT '{}'",
    }
    for column, sql in migrations.items():
        if column not in model_columns:
            db.execute(sql)
    # 阶段 7.9.2：models.icon 字段（模型个性化 logo，AI 配置页唯一源头）
    if "icon" not in model_columns:
        db.execute("ALTER TABLE models ADD COLUMN icon TEXT")
    # 阶段 7.9.2：model_providers.icon 字段（供应商个性化 logo）
    provider_columns = _table_columns(db, "model_providers")
    if "icon" not in provider_columns:
        db.execute("ALTER TABLE model_providers ADD COLUMN icon TEXT")
    provider_migrations = {
        "provider_kind": "ALTER TABLE model_providers ADD COLUMN provider_kind TEXT NOT NULL DEFAULT 'openai_compatible'",
        "api_mode": "ALTER TABLE model_providers ADD COLUMN api_mode TEXT NOT NULL DEFAULT 'chat_completions'",
        "options_json": "ALTER TABLE model_providers ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'",
    }
    for column, sql in provider_migrations.items():
        if column not in provider_columns:
            db.execute(sql)

    db.execute(
        "UPDATE models SET api_model = name WHERE api_model IS NULL OR TRIM(api_model) = ''"
    )
    capability_aliases = {
        "对话": "chat",
        "chat": "chat",
        "工具": "tools",
        "tool": "tools",
        "tools": "tools",
        "视觉": "vision",
        "vision": "vision",
        "知识库嵌入": "embeddings",
        "embedding": "embeddings",
        "embeddings": "embeddings",
        "知识库排序": "rerank",
        "reranker": "rerank",
        "rerank": "rerank",
    }
    rows = db.execute(
        """SELECT id, capabilities FROM models
           WHERE COALESCE(capabilities, '') <> ''
             AND (capabilities_json IS NULL OR capabilities_json = '' OR capabilities_json = '{}')"""
    ).fetchall()
    for row in rows:
        flags: dict[str, bool] = {}
        for value in str(row["capabilities"] or "").replace("，", ",").split(","):
            normalized = capability_aliases.get(value.strip().lower())
            if normalized:
                flags[normalized] = True
        db.execute(
            "UPDATE models SET capabilities_json = ? WHERE id = ?",
            (json.dumps(flags, ensure_ascii=False, sort_keys=True), row["id"]),
        )
    # Ensure deleted_presets table exists
    db.execute(
        """CREATE TABLE IF NOT EXISTS deleted_presets (
            id TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS model_capability_checks (
            model_id TEXT NOT NULL,
            check_name TEXT NOT NULL,
            status TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}',
            checked_at TEXT NOT NULL,
            PRIMARY KEY (model_id, check_name)
        )"""
    )
    # Phase 17: token_usage.reasoning_tokens 列（DeepSeek/GLM 思考模式
    # token 计数，2026-08-17 全量审计发现 core.py 收集但从未持久化）
    from storage.token_store import ensure_usage_schema
    ensure_usage_schema(db)

    # Phase 17 P5：project_data_changes.col_name / key_value 列（字段级修改日志）
    change_columns = _table_columns(db, "project_data_changes")
    if "col_name" not in change_columns:
        db.execute("ALTER TABLE project_data_changes ADD COLUMN col_name TEXT")
    if "key_value" not in change_columns:
        db.execute("ALTER TABLE project_data_changes ADD COLUMN key_value TEXT")

    # 阶段 G：projects.work_mode 列（工作模式 manual=人工审批 / auto=全自动）
    project_columns = _table_columns(db, "projects")
    if "work_mode" not in project_columns:
        db.execute("ALTER TABLE projects ADD COLUMN work_mode TEXT NOT NULL DEFAULT 'manual'")

    migrate_provider_configuration(db)


def _ensure_agent_runtime_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """CREATE TABLE IF NOT EXISTS agent_session_items (
               session_id TEXT NOT NULL,
               seq INTEGER NOT NULL,
               item_json TEXT NOT NULL,
               created_at TEXT NOT NULL,
               PRIMARY KEY (session_id, seq)
           );
           CREATE INDEX IF NOT EXISTS idx_agent_session_items_latest
               ON agent_session_items(session_id, seq DESC);
           CREATE TABLE IF NOT EXISTS agent_run_checkpoints (
               run_id TEXT PRIMARY KEY,
               checkpoint_json TEXT NOT NULL,
               sdk_version TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'ready',
               lease_token TEXT,
               updated_at TEXT NOT NULL
           );"""
    )


def get_db() -> sqlite3.Connection:
    """Return a per-request SQLite connection.  Created on first access.

    Phase 17 P14: smolagents 用 ThreadPoolExecutor 执行工具，工作线程会继承
    app context（contextvars 复制），导致 g.db 连接被工具线程跨线程使用 →
    sqlite3 抛 "objects created in a thread can only be used in that same thread"。
    这里显式 check_same_thread=False（WAL 模式下 SQLite 支持多线程访问，
    且同一 run 内工具串行执行，无并发写风险）。
    """
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(_error: object = None) -> None:
    """Close the request-scoped database connection."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app: Flask) -> None:
    """Run schema.sql inside an app context and register teardown."""
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        ensure_skill_availability_schema(db)
        _ensure_model_schema(db)
        _ensure_agent_runtime_schema(db)
        db.commit()
        migrate_portable_paths(
            db,
            backup_path=Path(DB_PATH).with_name("app.pre-portable-paths.sqlite3"),
        )
        db.commit()
    app.teardown_appcontext(close_db)
