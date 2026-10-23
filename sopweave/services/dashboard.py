"""Read-only homepage aggregation over the shared usage ledger and runtime tables."""

from datetime import datetime, time, timedelta, timezone
import logging
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def _parse(value):
    if not value:
        return None
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def _top(items):
    ordered = sorted(
        items,
        key=lambda item: (
            -item["tokens"],
            item.get("id", item.get("provider_id", "")),
            item.get("model_id", ""),
        ),
    )
    if len(ordered) <= 8:
        return ordered
    return ordered[:8] + [
        {
            "id": "",
            "provider_id": "",
            "model_id": "",
            "provider_name": "",
            "name": "其他",
            "other": True,
            "tokens": sum(item["tokens"] for item in ordered[8:]),
        }
    ]


def _tokens(db, boundaries, now):
    tracking = db.execute(
        "SELECT started_at FROM token_usage_state WHERE id=1"
    ).fetchone()
    tracking_at = _parse(tracking["started_at"]) if tracking else None
    case_parts = []
    params: list[object] = []
    for day, start, end in boundaries:
        case_parts.append("WHEN created_at>=? AND created_at<? THEN ?")
        params.extend(
            (
                start.isoformat(),
                min(end, now + timedelta(microseconds=1)).isoformat(),
                day,
            )
        )
    range_start = boundaries[0][1]
    range_end = min(boundaries[-1][2], now + timedelta(microseconds=1))
    params.extend((range_start.isoformat(), range_end.isoformat()))
    query = f"""
        SELECT CASE {' '.join(case_parts)} END AS day,
               provider_id,model_id,COUNT(*) AS requests,
               SUM(total_tokens) AS tokens,COUNT(total_tokens) AS known,MAX(id) AS latest_id
        FROM token_usage
        WHERE created_at>=? AND created_at<? AND call_id IS NOT NULL
        GROUP BY day,provider_id,model_id
        """
    # Reduce daily/model groups inside SQLite, so only days and dimensions cross
    # the Python boundary (important when several dashboards refresh together).
    rows = db.execute(
        f"""
        WITH grouped AS MATERIALIZED ({query})
        SELECT 'day' AS kind,day,'' AS provider_id,'' AS model_id,
               SUM(requests) AS requests,SUM(known) AS known,SUM(tokens) AS tokens,MAX(latest_id) AS latest_id
        FROM grouped GROUP BY day
        UNION ALL
        SELECT 'provider','',provider_id,'',SUM(requests),SUM(known),SUM(tokens),MAX(latest_id)
        FROM grouped GROUP BY provider_id
        UNION ALL
        SELECT 'model','',provider_id,model_id,SUM(requests),SUM(known),SUM(tokens),MAX(latest_id)
        FROM grouped GROUP BY provider_id,model_id
        """,
        params,
    ).fetchall()
    days = {
        day: {"tokens": 0, "missing_requests": 0, "requests": 0, "known": 0}
        for day, _, _ in boundaries
    }
    providers, models = {}, {}
    for row in rows:
        if row["kind"] == "day":
            days[row["day"]] = {
                "requests": row["requests"],
                "known": row["known"],
                "tokens": row["tokens"] or 0,
                "missing_requests": row["requests"] - row["known"],
            }
        else:
            groups, key = (
                (providers, row["provider_id"])
                if row["kind"] == "provider"
                else (models, (row["provider_id"], row["model_id"]))
            )
            groups[key] = {
                "tokens": row["tokens"] or 0,
                "known": row["known"],
                "latest_id": row["latest_id"],
            }
    provider_items = _top(
        [
            {"id": key, "tokens": item["tokens"], "latest_id": item["latest_id"]}
            for key, item in providers.items()
            if item["known"]
        ]
    )
    model_items = _top(
        [
            {
                "provider_id": key[0],
                "model_id": key[1],
                "tokens": item["tokens"],
                "latest_id": item["latest_id"],
            }
            for key, item in models.items()
            if item["known"]
        ]
    )
    # Only fetch labels for visible groups; avoid an unbounded IN clause as models grow.
    ids = {
        item["latest_id"]
        for items in (provider_items, model_items)
        for item in items
        if not item.get("other")
    }
    labels = (
        {
            row["id"]: row
            for row in db.execute(
                f"SELECT id,provider_name,model_name FROM token_usage WHERE id IN ({','.join('?' for _ in ids)})",
                tuple(ids),
            )
        }
        if ids
        else {}
    )
    for items, is_model in ((provider_items, False), (model_items, True)):
        for item in items:
            if item.get("other"):
                continue
            label = labels[item.pop("latest_id")]
            item["name"] = label["model_name" if is_model else "provider_name"] or (
                "未命名模型" if is_model else "自定义供应商"
            )
            if is_model:
                item["provider_name"] = label["provider_name"] or "自定义供应商"
    trend = []
    for day, _, end in boundaries:
        point = days[day]
        tokens = point["tokens"]
        if not point["known"] and (
            point["requests"] or tracking_at is None or tracking_at >= end
        ):
            tokens = None
        trend.append(
            {
                "day": day,
                "tokens": tokens,
                "missing_requests": point["missing_requests"],
            }
        )
    return (
        {
            "tokens": trend[-1]["tokens"],
            "ai_requests": days[boundaries[-1][0]]["requests"],
        },
        trend,
        {"providers": provider_items, "models": model_items},
        {
            "tracking_started_at": tracking_at.isoformat() if tracking_at else None,
            "today_missing": trend[-1]["missing_requests"],
            "range_missing": sum(point["missing_requests"] for point in trend),
        },
    )


def _runtime(db, start, now):
    counts = db.execute(
        """SELECT COUNT(*) AS runs,
        COALESCE(SUM(status='completed'),0) AS success,COALESCE(SUM(status='failed'),0) AS failed
        FROM runs WHERE created_at>=? AND created_at<=? AND status<>'legacy' """,
        (start.isoformat(), now.isoformat()),
    ).fetchone()
    intervals = []
    for row in db.execute(
        """SELECT start_at,end_at FROM app_sessions
        WHERE start_at<? AND (end_at>=? OR (end_at IS NULL AND status='open'))""",
        (now.isoformat(), start.isoformat()),
    ):
        a, b = max(_parse(row["start_at"]), start), min(
            _parse(row["end_at"]) or now, now
        )
        if a < b:
            intervals.append((a, b))
    duration = 0.0
    previous = None
    for a, b in sorted(intervals):
        if previous is not None:
            a = max(a, previous)
        if a < b:
            duration += (b - a).total_seconds()
        previous = max(previous, b) if previous else b
    return {**dict(counts), "duration_sec": int(duration)}


def dashboard_summary(
    db, *, days=7, timezone_name="Asia/Shanghai", now=None, collector_warning=False
):
    if days not in (7, 30):
        raise ValueError("days 只能选择 7 或 30")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError("timezone 必须是有效的 IANA 时区") from None
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    today = now.astimezone(zone).date()
    boundaries = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(
            timezone.utc
        )
        boundaries.append((day.isoformat(), start, end))
    data = {
        "ts": now.isoformat(),
        "timezone": timezone_name,
        "range": {"days": days, "start": boundaries[0][0], "end": boundaries[-1][0]},
        "today": dict.fromkeys(
            ("tokens", "ai_requests", "runs", "success", "failed", "duration_sec")
        ),
        "token_trend": [],
        "token_ranking": {"providers": [], "models": []},
        "quality": {
            "tokens_available": False,
            "runtime_available": False,
            "collector_warning": bool(collector_warning),
            "tracking_started_at": None,
            "today_missing": None,
            "range_missing": None,
        },
    }
    # SAVEPOINT nests safely in an existing read transaction without committing caller writes.
    db.execute("SAVEPOINT dashboard_read")
    try:
        try:
            totals, trend, ranking, quality = _tokens(db, boundaries, now)
            data["today"].update(totals)
            data.update(token_trend=trend, token_ranking=ranking)
            data["quality"].update(quality, tokens_available=True)
        except (sqlite3.Error, ValueError, OverflowError):
            logger.warning("Dashboard token aggregation unavailable", exc_info=False)
        try:
            data["today"].update(_runtime(db, boundaries[-1][1], now))
            data["quality"]["runtime_available"] = True
        except (sqlite3.Error, ValueError, TypeError, OverflowError):
            logger.warning("Dashboard runtime aggregation unavailable", exc_info=False)
    finally:
        db.execute("RELEASE dashboard_read")
    return data
