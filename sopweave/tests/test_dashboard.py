import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from agent_runtime.models.usage import UsageIdentity, normalize_usage
from storage.token_store import TokenStore, ensure_usage_schema
from services.dashboard import dashboard_summary


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript(Path("storage/schema.sql").read_text(encoding="utf-8"))
        ensure_usage_schema(self.db)
        self.db.execute(
            "INSERT INTO token_usage_state VALUES(1,'2026-10-15T00:00:00+00:00')"
        )
        self.db.commit()
        self.now = datetime(2026, 10, 18, 3, tzinfo=timezone.utc)

    def call(self, id, ts, total, provider="p", name="官方"):
        store = TokenStore(self.db)
        store.begin_call(
            id,
            UsageIdentity(provider, name, "deepseek", "m", "模型", "api"),
            boot_id="b",
            created_at=ts,
        )
        store.finish_call(
            id,
            "completed",
            normalize_usage({"total_tokens": total} if total is not None else None),
        )

    def summary(self, **kwargs):
        return dashboard_summary(self.db, now=self.now, **kwargs)

    def test_local_midnight_cohorts_unknown_and_zero(self):
        self.call("y", "2026-10-17T15:59:59+00:00", 100)
        self.call("t", "2026-10-17T16:00:00+00:00", 7)
        self.call("missing", "2026-10-18T02:00:00+00:00", None)
        self.call("zero", "2026-10-16T00:00:00+00:00", 0)
        self.db.execute(
            "INSERT INTO token_usage(created_at,total_tokens) VALUES('2026-10-18T01:00:00+00:00',999)"
        )
        self.db.commit()
        result = self.summary()
        self.assertEqual(
            (result["today"]["tokens"], result["today"]["ai_requests"]), (7, 2)
        )
        self.assertEqual(
            [r["tokens"] for r in result["token_trend"]],
            [None, None, None, 0, 0, 100, 7],
        )
        self.assertEqual(result["quality"]["today_missing"], 1)
        self.assertEqual(
            sum(r["tokens"] for r in result["token_ranking"]["providers"]), 107
        )

    def test_all_missing_day_is_null_and_empty_after_tracking_is_zero(self):
        self.call("x", "2026-10-18T02:00:00+00:00", None)
        result = self.summary(days=30)
        self.assertEqual(len(result["token_trend"]), 30)
        self.assertIsNone(result["today"]["tokens"])
        self.assertEqual(result["token_trend"][-2]["tokens"], 0)

    def test_rankings_keep_stable_ids_latest_names_and_other_sum(self):
        for i in range(10):
            self.call(
                str(i), "2026-10-18T01:00:00+00:00", i + 1, provider=str(i), name="同名"
            )
        self.call("rename", "2026-10-18T02:00:00+00:00", 1, provider="9", name="新名称")
        result = self.summary()
        ranking = result["token_ranking"]
        self.assertEqual(len(ranking["providers"]), 9)
        self.assertEqual(ranking["providers"][0]["name"], "新名称")
        self.assertEqual(sum(r["tokens"] for r in ranking["providers"]), 56)
        self.assertEqual(sum(r["tokens"] for r in ranking["models"]), 56)

    def test_sessions_overlap_and_run_status_are_local_today(self):
        for id, start, end in [
            ("a", "2026-10-17T15:00:00+00:00", "2026-10-17T17:00:00+00:00"),
            ("b", "2026-10-17T16:30:00+00:00", "2026-10-17T18:00:00+00:00"),
        ]:
            self.db.execute(
                "INSERT INTO app_sessions(id,start_at,end_at,status) VALUES(?,?,?,?)",
                (id, start, end, "closed"),
            )
        for id, status in [
            ("a", "completed"),
            ("b", "failed"),
            ("c", "legacy"),
            ("d", "running"),
        ]:
            self.db.execute(
                "INSERT INTO runs(id,status,created_at,updated_at) VALUES(?,?,?,?)",
                (id, status, "2026-10-17T17:00:00+00:00", "2026-10-17T17:00:00+00:00"),
            )
        self.db.commit()
        before = self.db.total_changes
        result = self.summary()["today"]
        self.assertEqual(
            (
                result["runs"],
                result["success"],
                result["failed"],
                result["duration_sec"],
            ),
            (3, 1, 1, 7200),
        )
        self.assertEqual(self.db.total_changes, before)

    def test_daylight_saving_and_invalid_parameters(self):
        result = self.summary(days=7, timezone_name="America/New_York")
        self.assertEqual(result["range"]["end"], "2026-10-17")
        for kwargs in [{"days": 14}, {"timezone_name": "bad/zone"}]:
            with self.assertRaises(ValueError):
                self.summary(**kwargs)

    def test_partial_database_failure_is_not_fake_zero(self):
        self.db.execute("DROP TABLE runs")
        self.db.commit()
        result = self.summary()
        self.assertIsNone(result["today"]["runs"])
        self.assertFalse(result["quality"]["runtime_available"])
        self.assertTrue(result["quality"]["tokens_available"])
        self.assertEqual(result["today"]["tokens"], 0)

    def test_token_query_failure_keeps_runtime(self):
        self.db.execute("DROP TABLE token_usage")
        self.db.commit()
        result = self.summary()
        self.assertFalse(result["quality"]["tokens_available"])
        self.assertIsNone(result["today"]["tokens"])
        self.assertEqual(result["today"]["runs"], 0)

    def test_route_rejects_invalid_range_and_timezone(self):
        from flask import Flask
        from routes.dashboard import dashboard_bp
        from unittest.mock import patch

        app = Flask(__name__)
        app.register_blueprint(dashboard_bp)
        with app.test_client() as client, patch(
            "routes.dashboard.get_db", return_value=self.db
        ):
            self.assertEqual(
                client.get("/api/dashboard/summary?days=14").status_code, 422
            )
            self.assertEqual(
                client.get("/api/dashboard/summary?timezone=bad").status_code, 422
            )
            response = client.get("/api/dashboard/summary?days=7")
            self.assertEqual(response.status_code, 200)
            self.assertIn("today", response.json["data"])

    def test_route_reuses_unchanged_summary_and_invalidates_after_usage_write(self):
        from flask import Flask
        from routes import dashboard as route
        from unittest.mock import patch

        app = Flask(__name__)
        app.register_blueprint(route.dashboard_bp)
        route._SUMMARY_CACHE.clear()
        self.addCleanup(route._SUMMARY_CACHE.clear)

        def aggregate(database, **kwargs):
            return dashboard_summary(database, **kwargs)

        with (
            app.test_client() as client,
            patch("routes.dashboard.get_db", return_value=self.db),
            patch("routes.dashboard.aggregate", side_effect=aggregate) as called,
        ):
            first = client.get("/api/dashboard/summary?days=7")
            second = client.get("/api/dashboard/summary?days=7")
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(called.call_count, 1)

            self.call(
                "after-cache",
                datetime.now(timezone.utc).isoformat(),
                5,
            )
            third = client.get("/api/dashboard/summary?days=7")
            self.assertEqual(third.status_code, 200)
            self.assertEqual(called.call_count, 2)
            self.assertEqual(third.json["data"]["today"]["tokens"], 5)

    def test_dst_days_use_23_and_25_hour_windows(self):
        cases = [
            ("2026-03-08T05:00:00+00:00", "2026-03-09T03:59:59+00:00", 23),
            ("2026-11-01T04:00:00+00:00", "2026-11-02T04:59:59+00:00", 25),
        ]
        for start, end, hours in cases:
            self.db.execute("DELETE FROM app_sessions")
            self.db.execute(
                "INSERT INTO app_sessions(id,start_at,status) VALUES('dst',?,'open')",
                (start,),
            )
            self.db.commit()
            result = dashboard_summary(
                self.db,
                now=datetime.fromisoformat(end),
                timezone_name="America/New_York",
            )
            self.assertEqual(result["today"]["duration_sec"], hours * 3600 - 1)
