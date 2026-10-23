from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from agent_runtime.definitions.agent_registry import AgentRegistry
from agent_runtime.definitions.skill_registry import SkillNotFoundError, SkillRegistry
from services.skills.contracts import SkillConflict, SkillMissing
from services.skills.library import SkillLibrary
from tests.agent_runtime.test_skill_contracts import sample_spec


from tests.agent_runtime.skill_fixtures import SkillTestFixture


class SkillLifecycleTests(SkillTestFixture, unittest.TestCase):
    def test_availability_migration_is_idempotent_and_preserves_existing_skills(self):
        from storage.skill_store import ensure_skill_availability_schema

        legacy = self.root / "legacy.db"
        with closing(sqlite3.connect(legacy)) as db:
            db.execute(
                "CREATE TABLE user_skills("
                "skill_id TEXT PRIMARY KEY,title TEXT NOT NULL,status TEXT NOT NULL,"
                "current_version INTEGER NOT NULL,created_at TEXT NOT NULL,"
                "updated_at TEXT NOT NULL,deleted_at TEXT)"
            )
            db.execute(
                "INSERT INTO user_skills VALUES(?,?,?,?,?,?,NULL)",
                ("user-" + "a" * 32, "旧 SOP", "active", 3, "now", "now"),
            )
            ensure_skill_availability_schema(db)
            ensure_skill_availability_schema(db)
            columns = {row[1] for row in db.execute("PRAGMA table_info(user_skills)")}
            self.assertIn("enabled", columns)
            self.assertEqual(
                db.execute(
                    "SELECT enabled FROM user_skills WHERE skill_id=?",
                    ("user-" + "a" * 32,),
                ).fetchone()[0],
                1,
            )

    def test_disable_is_cas_guarded_hidden_from_execution_and_persists(self):
        ref = self.publish(self.ready())
        generation = self.library.generation()
        result = self.library.set_enabled(
            ref.skill_id,
            enabled=False,
            expected_version=ref.version,
            expected_generation=generation,
        )
        self.assertFalse(result["enabled"])
        self.assertEqual(result["generation"], generation + 1)
        self.assertEqual(self.library.list(), [])
        managed = self.library.list(include_disabled=True)
        self.assertEqual([(item["name"], item["enabled"]) for item in managed], [
            (ref.skill_id, False)
        ])
        registry = SkillRegistry(
            agent_registry=AgentRegistry(), library=self.library
        )
        with self.assertRaises(SkillNotFoundError):
            registry.get(ref.skill_id)
        with self.assertRaises(SkillNotFoundError):
            registry.read_skill("general-agent", ref.skill_id)
        self.assertNotIn(
            ref.skill_id,
            [item.name for item in registry.search_for_agent("general-agent")],
        )
        self.assertEqual(
            registry.get(ref.skill_id, include_disabled=True).manifest.name,
            ref.skill_id,
        )
        reopened = SkillLibrary(self.db_path, data_dir=self.root)
        self.assertEqual(reopened.list(), [])
        self.assertFalse(reopened.list(include_disabled=True)[0]["enabled"])
        with self.assertRaises(SkillConflict):
            reopened.set_enabled(
                ref.skill_id,
                enabled=True,
                expected_version=ref.version,
                expected_generation=generation,
            )
        enabled = reopened.set_enabled(
            ref.skill_id,
            enabled=True,
            expected_version=ref.version,
            expected_generation=result["generation"],
        )
        self.assertTrue(enabled["enabled"])
        self.assertEqual(reopened.list()[0]["name"], ref.skill_id)

    def test_enabled_http_contract_uses_management_view_and_rejects_builtin(self):
        from flask import Flask
        from routes.skills import skills_bp

        ref = self.publish(self.ready())
        app = Flask(__name__)
        app.config.update(
            SKILL_LIBRARY=self.library,
            SKILL_REGISTRY=SkillRegistry(library=self.library),
        )
        app.register_blueprint(skills_bp)
        client = app.test_client()
        generation = client.get("/api/skills").get_json()["data"]["generation"]
        response = client.patch(
            f"/api/skills/{ref.skill_id}/enabled",
            json={
                "enabled": False,
                "expected_version": 1,
                "expected_generation": generation,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["data"],
            {"skill_id": ref.skill_id, "enabled": False, "generation": generation + 1},
        )
        self.assertEqual(client.get(f"/api/skills/{ref.skill_id}").status_code, 404)
        management = client.get(
            "/api/skills?include_disabled=true"
        ).get_json()["data"]["skills"]
        self.assertEqual(
            [
                (item["name"], item["enabled"])
                for item in management
                if item["origin"] == "user"
            ],
            [(ref.skill_id, False)],
        )
        detail = client.get(
            f"/api/skills/{ref.skill_id}?include_disabled=true"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.get_json()["data"]["enabled"])
        self.assertEqual(
            client.patch(
                "/api/skills/data-reconcile/enabled",
                json={
                    "enabled": False,
                    "expected_version": 1,
                    "expected_generation": generation + 1,
                },
            ).status_code,
            403,
        )

    def test_global_switch_disables_user_sop_creation_but_keeps_builtins(self):
        from flask import Flask
        from routes.skills import skills_bp

        app = Flask(__name__)
        app.config["SKILL_REGISTRY"] = SkillRegistry()
        app.register_blueprint(skills_bp)
        client = app.test_client()
        page = client.get("/api/skills").get_json()["data"]
        self.assertFalse(page["user_skills_enabled"])
        self.assertIn("data-reconcile", [item["name"] for item in page["skills"]])
        self.assertEqual(
            client.post("/api/skills/drafts", json={"text": "不能创建"}).status_code,
            422,
        )
        self.assertEqual(
            client.get("/api/skills/data-reconcile").status_code,
            200,
        )

    def test_publish_requires_matching_revision_and_digest(self):
        draft = self.ready()
        with self.assertRaises(SkillConflict):
            self.library.publish(
                draft["draft_id"],
                expected_revision=draft["draft_revision"],
                confirmed_digest="wrong",
                idempotency_key="one",
            )
        self.assertEqual(self.library.list(), [])

    def test_publish_retry_is_idempotent(self):
        draft = self.ready()
        with ThreadPoolExecutor(max_workers=8) as pool:
            refs = list(pool.map(lambda _: self.publish(draft), range(16)))
        self.assertEqual(len({r.skill_id for r in refs}), 1)
        self.assertEqual(len(self.library.list()), 1)

    def test_two_editors_only_one_base_version_wins(self):
        base = self.publish(self.ready())
        first, second = self.ready(base=base), self.ready(base=base)
        updated = self.publish(first, "two")
        self.assertEqual(updated.version, 2)
        with self.assertRaises(SkillConflict):
            self.publish(second, "three")
        self.assertEqual(self.library.get(base.skill_id)["version"], 2)

    def test_crash_before_metadata_commit_is_not_discoverable(self):
        draft = self.ready()
        self.library.after_package_commit = lambda: (_ for _ in ()).throw(
            OSError("injected failure")
        )
        with self.assertRaises(OSError):
            self.publish(draft)
        self.assertEqual(self.library.list(), [])
        self.library.after_package_commit = None
        self.assertEqual(self.publish(draft).version, 1)

    def test_delete_with_active_reference_returns_409(self):
        ref = self.publish(self.ready())
        with self.library.connection() as db:
            db.execute(
                "INSERT INTO runs(id,status,created_at,updated_at) VALUES('run','waiting','now','now')"
            )
            db.execute(
                "INSERT INTO skill_run_refs(run_id,skill_id,version,origin,content_digest,created_at) VALUES('run',?,?, 'user',?,'now')",
                (ref.skill_id, str(ref.version), ref.content_digest),
            )
            db.commit()
        with self.assertRaises(SkillConflict):
            self.library.delete(ref.skill_id, expected_version=1)

    def test_deleted_files_never_resurrect(self):
        ref = self.publish(self.ready())
        self.library.delete(ref.skill_id, expected_version=1)
        self.assertEqual(self.library.list(), [])
        with self.assertRaises(SkillMissing):
            self.library.get(ref.skill_id)
        self.assertTrue(
            (
                self.root / "skills" / "packages" / ref.skill_id / "1" / "SKILL.md"
            ).exists()
        )

    def test_one_corrupt_user_package_does_not_break_others(self):
        first = self.publish(self.ready(), "first")
        second = self.publish(self.ready(title="另一个"), "second")
        (
            self.root / "skills" / "packages" / first.skill_id / "1" / "SKILL.md"
        ).write_text("bad", encoding="utf-8")
        registry = SkillRegistry(library=self.library)
        self.assertEqual(registry.get(second.skill_id).manifest.name, second.skill_id)
        with self.assertRaises(ValueError):
            self.library.get(first.skill_id)

    def test_new_user_skill_is_discovered_without_agent_rewrite(self):
        agents = AgentRegistry()
        ref = self.publish(self.ready())
        registry = SkillRegistry(agent_registry=agents, library=self.library)
        self.assertIn(
            "读取指定表范围", registry.read_skill("general-agent", ref.skill_id)
        )

    def test_http_lifecycle_uses_same_revision_and_details(self):
        from flask import Flask
        from routes.skills import skills_bp

        app = Flask(__name__)
        app.config.update(
            SKILL_LIBRARY=self.library,
            SKILL_REGISTRY=SkillRegistry(library=self.library),
        )
        app.register_blueprint(skills_bp)
        client = app.test_client()
        self.assertEqual(
            client.get(
                "/api/skills", headers={"Content-Type": "application/json"}
            ).status_code,
            200,
        )
        for invalid in [
            [],
            {"base_skill_id": 123},
            {"text": "x", "unexpected": True},
            {"base_skill_id": "user-" + "a" * 32},
        ]:
            self.assertEqual(
                client.post("/api/skills/drafts", json=invalid).status_code, 422
            )
        draft = client.post("/api/skills/drafts", json={"text": "月度核对"}).get_json()[
            "data"
        ]
        draft = client.patch(
            "/api/skills/drafts/" + draft["draft_id"],
            json={"expected_revision": 1, "spec": sample_spec().model_dump()},
        ).get_json()["data"]
        response = client.post(
            "/api/skills/drafts/" + draft["draft_id"] + "/publish",
            json={
                "expected_revision": draft["draft_revision"],
                "confirmed_digest": draft["generated_digest"],
                "idempotency_key": "http",
            },
        )
        self.assertEqual(response.status_code, 200)
        skill_id = response.get_json()["data"]["ref"]["skill_id"]
        self.assertEqual(
            client.delete(
                "/api/skills/" + skill_id, json={"expected_version": "invalid"}
            ).status_code,
            422,
        )
        detail = client.get("/api/skills/" + skill_id).get_json()["data"]
        self.assertFalse(detail["readonly"])
        self.assertEqual(detail["version"], 1)
        self.assertEqual(detail["required_tools"], ["read_sheet_range"])
        self.assertEqual(
            client.delete(
                "/api/skills/" + skill_id, json={"expected_version": 2}
            ).status_code,
            409,
        )
        self.assertEqual(
            client.delete(
                "/api/skills/" + skill_id, json={"expected_version": 1}
            ).status_code,
            200,
        )
        self.assertEqual(client.get("/api/skills/" + skill_id).status_code, 404)
