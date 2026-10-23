"""Homepage statistics API; aggregation is owned by services.dashboard."""

import threading
import time

from flask import Blueprint, request
from routes._utils import error, success
from storage.db import get_db
from services.dashboard import dashboard_summary as aggregate
from services.model_usage import usage_warning_for_connection

dashboard_bp = Blueprint("dashboard_api", __name__, url_prefix="/api/dashboard")
_SUMMARY_CACHE: dict[tuple[object, ...], dict] = {}
_SUMMARY_CACHE_LOCK = threading.Lock()
_SUMMARY_CACHE_TTL_SECONDS = 1.0


def _database_key(db) -> str:
    rows = db.execute("PRAGMA database_list").fetchall()
    main = next((row for row in rows if row[1] == "main"), None)
    return str(main[2]) if main and main[2] else f"memory:{id(db)}"


def _data_watermark(db) -> tuple:
    token_id = db.execute(
        "SELECT COALESCE(MAX(id),0) FROM token_usage"
    ).fetchone()[0]
    data_version = db.execute("PRAGMA data_version").fetchone()[0]
    return (data_version, db.total_changes, token_id)


@dashboard_bp.get("/summary")
def dashboard_summary():
    try:
        days = int(request.args.get("days", 7))
        db = get_db()
        timezone_name = request.args.get("timezone", "Asia/Shanghai")
        collector_warning = usage_warning_for_connection(db)
        cache_key = (
            _database_key(db),
            days,
            timezone_name,
            bool(collector_warning),
        )
        # A short single-flight cache coalesces the dashboard's 30-second refresh
        # bursts and range switches. The data watermark invalidates it immediately
        # on usage or runtime changes, so this does not turn unknown/failed data into 0.
        with _SUMMARY_CACHE_LOCK:
            watermark = _data_watermark(db)
            now = time.monotonic()
            cached = _SUMMARY_CACHE.get(cache_key)
            if (
                cached
                and cached["watermark"] == watermark
                and now - cached["stored_at"] <= _SUMMARY_CACHE_TTL_SECONDS
            ):
                return success(cached["data"])
            data = aggregate(
                db,
                days=days,
                timezone_name=timezone_name,
                collector_warning=collector_warning,
            )
            _SUMMARY_CACHE[cache_key] = {
                "watermark": watermark,
                "stored_at": now,
                "data": data,
            }
            if len(_SUMMARY_CACHE) > 64:
                oldest = sorted(
                    _SUMMARY_CACHE,
                    key=lambda key: _SUMMARY_CACHE[key]["stored_at"],
                )[:-64]
                for key in oldest:
                    _SUMMARY_CACHE.pop(key, None)
            return success(data)
    except ValueError as exc:
        return error(str(exc), 422)
    return success(data)
