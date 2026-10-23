from __future__ import annotations

import json
import sqlite3
from urllib.parse import urlsplit


_LEGACY_PROVIDER_IDS = {"provider_tianjian_local", "provider_chat_api"}


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(db, table):
        return set()
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _json_options(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _provider_model_refs(
    db: sqlite3.Connection, provider_id: str
) -> tuple[set[str], set[str]]:
    rows = db.execute(
        "SELECT id, name FROM models WHERE provider_id = ?", (provider_id,)
    ).fetchall()
    return ({str(row[0]) for row in rows}, {str(row[1]) for row in rows})


def _matches_any_reference(
    db: sqlite3.Connection,
    table: str,
    column: str,
    references: set[str],
) -> bool:
    if not references or column not in _table_columns(db, table):
        return False
    placeholders = ",".join("?" for _ in references)
    return db.execute(
        f"SELECT 1 FROM {table} WHERE {column} IN ({placeholders}) LIMIT 1",
        tuple(sorted(references)),
    ).fetchone() is not None


def _is_referenced(db: sqlite3.Connection, provider_id: str) -> bool:
    model_ids, model_names = _provider_model_refs(db, provider_id)
    references = model_ids | model_names
    if _matches_any_reference(db, "projects", "default_model", references):
        return True
    if _matches_any_reference(db, "runs", "model_id", references):
        return True
    if model_ids:
        placeholders = ",".join("?" for _ in model_ids)
        if db.execute(
            f"SELECT 1 FROM models WHERE id IN ({placeholders}) AND is_default = 1 LIMIT 1",
            tuple(sorted(model_ids)),
        ).fetchone() is not None:
            return True
    return False


def _delete_provider(db: sqlite3.Connection, provider_id: str) -> None:
    model_ids, _ = _provider_model_refs(db, provider_id)
    if model_ids and _table_exists(db, "model_capability_checks"):
        placeholders = ",".join("?" for _ in model_ids)
        db.execute(
            f"DELETE FROM model_capability_checks WHERE model_id IN ({placeholders})",
            tuple(sorted(model_ids)),
        )
    db.execute("DELETE FROM models WHERE provider_id = ?", (provider_id,))
    db.execute("DELETE FROM model_providers WHERE id = ?", (provider_id,))


def _convert_to_custom(
    db: sqlite3.Connection, provider_id: str, *, thinking_style: str
) -> None:
    row = db.execute(
        "SELECT options_json FROM model_providers WHERE id = ?", (provider_id,)
    ).fetchone()
    if row is None:
        return
    options = _json_options(row[0])
    options["thinking_style"] = thinking_style
    db.execute(
        """UPDATE model_providers
           SET provider_kind = 'openai_compatible', api_mode = 'chat_completions',
               options_json = ?, is_preset = 0
           WHERE id = ?""",
        (json.dumps(options, ensure_ascii=False, sort_keys=True), provider_id),
    )


def _normalize_official_deepseek(db: sqlite3.Connection) -> None:
    row = db.execute(
        "SELECT id, name, api_url FROM model_providers WHERE id = 'provider_deepseek'"
    ).fetchone()
    if row is None:
        return
    host = (urlsplit(str(row[2] or "")).hostname or "").lower()
    if host != "api.deepseek.com":
        return
    name_conflict = db.execute(
        "SELECT 1 FROM model_providers WHERE name = 'DeepSeek 官方' AND id <> ?",
        (row[0],),
    ).fetchone()
    normalized_name = str(row[1]) if name_conflict else "DeepSeek 官方"
    db.execute(
        """UPDATE model_providers
           SET name = ?, api_url = 'https://api.deepseek.com',
               provider_kind = 'deepseek', api_mode = 'chat_completions'
           WHERE id = ?""",
        (normalized_name, row[0]),
    )


def migrate_provider_configuration(db: sqlite3.Connection) -> None:
    if not _table_exists(db, "model_providers") or not _table_exists(db, "models"):
        return

    _normalize_official_deepseek(db)
    rows = db.execute(
        "SELECT id, provider_kind FROM model_providers ORDER BY id"
    ).fetchall()
    for row in rows:
        provider_id = str(row[0])
        provider_kind = str(row[1] or "")
        is_named_legacy = provider_id in _LEGACY_PROVIDER_IDS
        is_dashscope = provider_kind == "dashscope"
        if not is_named_legacy and not is_dashscope:
            continue
        if _is_referenced(db, provider_id):
            _convert_to_custom(
                db,
                provider_id,
                thinking_style="enable_thinking" if is_dashscope else "none",
            )
        else:
            _delete_provider(db, provider_id)
