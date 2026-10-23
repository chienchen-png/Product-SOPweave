import sqlite3
import unittest
from pathlib import Path

from agent_runtime.models.usage import UsageIdentity, normalize_usage
from storage.db import _ensure_model_schema
from storage.token_store import TokenStore


class TokenStoreTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            (Path(__file__).parents[1] / "storage/schema.sql").read_text(
                encoding="utf-8"
            )
        )
        _ensure_model_schema(self.db)
        self.store = TokenStore(self.db)
        self.identity = UsageIdentity(
            "p1", "官方", "deepseek", "m1", "模型", "api-model"
        )
        self.addCleanup(self.db.close)

    def begin(self, call_id="call-1", **kwargs):
        self.store.begin_call(
            call_id,
            self.identity,
            boot_id="boot",
            created_at="2026-10-18T01:00:00+00:00",
            **kwargs
        )
        return call_id

    def test_zero_and_missing_are_distinct_and_finish_is_idempotent(self):
        self.begin()
        self.store.finish_call(
            "call-1",
            "completed",
            normalize_usage(
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            ),
        )
        self.store.finish_call("call-1", "cancelled", normalize_usage(None))
        row = dict(self.db.execute("SELECT * FROM token_usage").fetchone())
        self.assertEqual(
            (row["total_tokens"], row["usage_state"], row["request_state"]),
            (0, "reported", "completed"),
        )
        self.begin("missing")
        self.store.finish_call("missing", "failed", normalize_usage(None))
        self.assertIsNone(
            self.db.execute(
                "SELECT total_tokens FROM token_usage WHERE call_id='missing'"
            ).fetchone()[0]
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0], 2
        )

    def test_duplicate_begin_and_old_boot_recovery(self):
        self.begin(execution_id="round-1")
        self.begin(execution_id="round-1")
        self.store.recover("new-boot")
        row = self.db.execute(
            "SELECT request_state,usage_state FROM token_usage"
        ).fetchone()
        self.assertEqual(tuple(row), ("interrupted", "missing"))
        self.store.begin_call("live", self.identity, boot_id="new-boot")
        self.store.recover("new-boot")
        self.assertEqual(
            self.db.execute(
                "SELECT request_state FROM token_usage WHERE call_id='live'"
            ).fetchone()[0],
            "pending",
        )

    def test_execution_cleanup_does_not_touch_another_execution(self):
        self.begin(execution_id="a")
        self.begin("other", execution_id="b")
        self.store.interrupt_execution("a", "cancelled")
        self.assertEqual(
            [
                tuple(r)
                for r in self.db.execute(
                    "SELECT request_state,usage_state FROM token_usage ORDER BY id"
                )
            ],
            [("cancelled", "missing"), ("pending", "pending")],
        )

    def test_migration_is_repeatable_and_legacy_stays_legacy(self):
        self.db.execute(
            "INSERT INTO token_usage(total_tokens,created_at) VALUES(99,'2026-10-17T00:00:00+00:00')"
        )
        _ensure_model_schema(self.db)
        _ensure_model_schema(self.db)
        self.assertEqual(
            tuple(
                self.db.execute(
                    "SELECT call_id,total_tokens FROM token_usage"
                ).fetchone()
            ),
            (None, 99),
        )

    def test_project_delete_detaches_global_usage(self):
        from storage.project_store import ProjectStore

        self.db.execute(
            "INSERT INTO projects(id,name,work_dir,created_at,updated_at) VALUES('p','示例','.','now','now')"
        )
        self.db.commit()
        self.begin(project_id="p")
        self.store.finish_call(
            "call-1", "completed", normalize_usage({"total_tokens": 150})
        )
        ProjectStore(self.db).delete("p")
        self.assertEqual(
            tuple(
                self.db.execute(
                    "SELECT project_id,total_tokens FROM token_usage"
                ).fetchone()
            ),
            (None, 150),
        )


class UsageNormalizationTests(unittest.TestCase):
    def test_three_vendors_and_reasoning_are_not_added_twice(self):
        for vendor in ["deepseek", "zhipu", "siliconflow", "openai_compatible"]:
            value = normalize_usage(
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "total_tokens": 140,
                    "completion_tokens_details": {"reasoning_tokens": 10},
                },
                vendor,
            )
            self.assertEqual(
                (value.total_tokens, value.reasoning_tokens, value.state),
                (140, 10, "reported"),
            )

    def test_only_valid_present_counts_are_used(self):
        self.assertEqual(
            normalize_usage({"input_tokens": 120, "output_tokens": 30}).total_tokens,
            150,
        )
        self.assertEqual(
            normalize_usage({"input_tokens": 120, "output_tokens": 30}).state, "derived"
        )
        value = normalize_usage({"total_tokens": 55})
        self.assertEqual(value.total_tokens, 55)
        self.assertIsNone(value.input_tokens)
        for raw in [
            None,
            {},
            {"total_tokens": True},
            {"total_tokens": -1},
            {"total_tokens": "15"},
            {"total_tokens": 2**64},
            {"prompt_tokens": 1},
        ]:
            self.assertEqual(normalize_usage(raw).state, "missing")

    def test_raw_usage_is_bounded_and_contains_no_arbitrary_strings(self):
        value = normalize_usage(
            {"total_tokens": 15, "api_key": "secret", "nested": {"password": "secret"}}
        )
        self.assertNotIn("secret", value.raw_json)
        self.assertNotIn("api_key", value.raw_json)


class UsageIdentityTests(unittest.TestCase):
    def test_unsaved_provider_checks_have_distinct_stable_vendor_and_model_identity(
        self,
    ):
        from agent_runtime.models.factory import resolve_model_config

        def identity(vendor, model):
            config = resolve_model_config(
                dict(
                    id="connection-test",
                    api_model=model,
                    api_url="https://example.com/v1",
                    provider_kind=vendor,
                )
            )
            return UsageIdentity.from_config(config)

        first = identity("deepseek", "m1")
        self.assertEqual(first, identity("deepseek", "m1"))
        self.assertNotEqual(first.provider_id, identity("zhipu", "m1").provider_id)
        self.assertNotEqual(first.model_id, identity("deepseek", "m2").model_id)
        self.assertEqual(first.provider_name, "DeepSeek 官方")


class LegacyPendingUsageTests(unittest.TestCase):
    def test_old_zero_defaults_do_not_turn_pending_calls_into_confirmed_zero(self):
        from storage.token_store import ensure_usage_schema

        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute(
            "CREATE TABLE token_usage(id INTEGER PRIMARY KEY, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,total_tokens INTEGER DEFAULT 0)"
        )
        ensure_usage_schema(db)
        TokenStore(db).begin_call(
            "pending", UsageIdentity("p", "P", "deepseek", "m", "M", "api"), boot_id="b"
        )
        self.assertEqual(
            db.execute(
                "SELECT input_tokens,output_tokens,total_tokens FROM token_usage"
            ).fetchone(),
            (None, None, None),
        )
