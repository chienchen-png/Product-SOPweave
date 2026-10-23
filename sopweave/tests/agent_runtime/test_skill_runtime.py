from __future__ import annotations

import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from agents.testing import ScriptedModel, ModelStep, assistant_message, function_call
from agent_runtime.service import RunService, RunRequest, ResumeCommand
from agent_runtime.skill_runtime import SkillRuntime
from agent_runtime.definitions.skill_registry import SkillNotFoundError, SkillRegistry
from services.skills.contracts import SopSelection, SkillConflict
from tests.agent_runtime.skill_fixtures import SkillTestFixture
from tests.agent_runtime.test_run_service import _collect


class SkillRuntimeTests(SkillTestFixture, unittest.TestCase):
    def runtime(self):
        return SkillRuntime(self.library, SkillRegistry(library=self.library))

    def bind(self, skill_id="", mode="auto", run_id="run"):
        with self.library.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO runs(id,agent_id,status,created_at,updated_at) VALUES(?,'general-agent','starting','now','now')",
                (run_id,),
            )
            snapshot = self.runtime().resolve_start(
                SopSelection(mode=mode, skill_id=skill_id),
                run_id=run_id,
                agent_id="general-agent",
                connection=db,
            )
            db.commit()
            return snapshot

    def test_explicit_run_pins_revision_through_approval(self):
        draft = self.ready()
        spec = draft["spec"]
        spec["required_tools"] = ["write_text"]
        spec["steps"][0]["tool_ids"] = ["write_text"]
        spec["steps"][0]["instruction"] = "UNIQUE_V1 输出报告"
        spec["execution_policy"]["require_approval_for"] = ["write_text"]
        spec["execution_policy"]["write_paths"] = ["report.md"]
        draft = self.library.update_draft(
            draft["draft_id"], expected_revision=draft["draft_revision"], spec=spec
        )
        ref = self.publish(draft)
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "write_text",
                            {
                                "path": "report.md",
                                "content": "done",
                                "expected_version": "missing",
                            },
                            call_id="write",
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("done")]),
            ]
        )
        service = RunService(
            db_path=self.db_path,
            skill_library=self.library,
            model_provider=lambda _: model,
        )
        request = RunRequest(
            "sdk-run",
            "执行 SOP",
            "",
            "conv",
            "general-agent",
            "test",
            self.root,
            work_mode="auto",
            sop_mode="explicit",
            skill_id=ref.skill_id,
        )
        waiting = asyncio.run(_collect(service.start(request)))
        self.assertEqual(waiting[-1].type, "run.waiting")
        with self.assertRaises(SkillConflict):
            self.library.delete(ref.skill_id, expected_version=1)
        edit = self.ready(base=ref, title="新版")
        self.publish(edit, "v2")
        from services.conversation_history import history_run
        from storage.run_store import RunStore

        with self.library.connection() as db:
            store = RunStore(db)
            history = history_run(db, store, store.get_run(request.run_id))
        self.assertEqual(history["sop_ref"]["version"], 1)
        self.assertEqual(history["sop_ref"]["skill_id"], ref.skill_id)
        restored = RunService(
            db_path=self.db_path,
            skill_library=self.library,
            model_provider=lambda _: model,
        )
        agent, _ = restored._build_agent(request)
        self.assertIn("UNIQUE_V1", agent.instructions)
        interaction = next(e.payload for e in waiting if e.type == "approval.required")
        command = ResumeCommand(
            action="approve",
            interaction_id=interaction["interaction_id"],
            version=interaction["version"],
        )
        resumed = asyncio.run(_collect(restored.resume(request.run_id, command)))
        self.assertEqual(resumed[-1].type, "run.completed")
        self.assertEqual((self.root / "report.md").read_text(), "done")

    def test_none_disables_user_sop_but_keeps_office(self):
        ref = self.publish(self.ready())
        snapshot = self.bind(mode="none")
        ids = [r.skill_id for r in snapshot.advertised_refs]
        self.assertIn("office-files", ids)
        self.assertNotIn(ref.skill_id, ids)
        with self.assertRaises(ValueError):
            self.runtime().load_for_run("run", ref.skill_id)

    def test_disabled_sop_keeps_bound_run_but_blocks_new_run(self):
        draft = self.ready()
        spec = draft["spec"]
        spec["required_tools"] = ["write_text"]
        spec["steps"][0]["tool_ids"] = ["write_text"]
        spec["steps"][0]["instruction"] = "DISABLED_BOUND_V1 输出报告"
        spec["execution_policy"]["require_approval_for"] = ["write_text"]
        spec["execution_policy"]["write_paths"] = ["report.md"]
        draft = self.library.update_draft(
            draft["draft_id"],
            expected_revision=draft["draft_revision"],
            spec=spec,
        )
        ref = self.publish(draft)
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "write_text",
                            {
                                "path": "report.md",
                                "content": "done",
                                "expected_version": "missing",
                            },
                            call_id="write",
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("done")]),
            ]
        )
        service = RunService(
            db_path=self.db_path,
            skill_library=self.library,
            model_provider=lambda _: model,
        )
        request = RunRequest(
            "bound-run",
            "执行 SOP",
            "",
            "conv",
            "general-agent",
            "test",
            self.root,
            work_mode="auto",
            sop_mode="explicit",
            skill_id=ref.skill_id,
        )
        waiting = asyncio.run(_collect(service.start(request)))
        self.assertEqual(waiting[-1].type, "run.waiting")
        disabled = self.library.set_enabled(
            ref.skill_id,
            enabled=False,
            expected_version=1,
            expected_generation=self.library.generation(),
        )
        self.assertFalse(disabled["enabled"])
        def unexpected_model(_):
            raise AssertionError("disabled SOP must be rejected before model creation")

        blocked_service = RunService(
            db_path=self.db_path,
            skill_library=self.library,
            model_provider=unexpected_model,
        )
        new_request = RunRequest(
            "new-blocked-run",
            "使用已停用 SOP",
            "",
            "conv-2",
            "general-agent",
            "test",
            self.root,
            work_mode="auto",
            sop_mode="explicit",
            skill_id=ref.skill_id,
        )
        with self.assertRaises(SkillNotFoundError):
            asyncio.run(_collect(blocked_service.start(new_request)))
        interaction = next(
            event.payload
            for event in waiting
            if event.type == "approval.required"
        )
        resume_model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "write_text",
                            {
                                "path": "report.md",
                                "content": "done",
                                "expected_version": "missing",
                            },
                            call_id="write",
                        )
                    ]
                ),
                ModelStep(output=[assistant_message("done")]),
            ]
        )
        restored = RunService(
            db_path=self.db_path,
            skill_library=self.library,
            model_provider=lambda _: resume_model,
        )
        resumed = asyncio.run(
            _collect(
                restored.resume(
                    request.run_id,
                    ResumeCommand(
                        action="approve",
                        interaction_id=interaction["interaction_id"],
                        version=interaction["version"],
                    ),
                )
            )
        )
        self.assertEqual(resumed[-1].type, "run.completed", resumed)
        self.assertEqual((self.root / "report.md").read_text(), "done")

    def test_first_lazy_load_creates_lease_and_delete_is_blocked(self):
        ref = self.publish(self.ready())
        self.bind()
        loaded = self.runtime().load_for_run("run", ref.skill_id)
        self.assertIn("读取指定表范围", loaded.content)
        with self.assertRaises(SkillConflict):
            self.library.delete(ref.skill_id, expected_version=1)

    def test_new_turn_filters_skill_instructions_and_preserves_facts(self):
        from agent_runtime.session import WorkbenchSession

        session = WorkbenchSession(
            "conv", db_path=self.db_path, filter_skill_history=True
        )
        items = [
            {"role": "user", "content": "业务月份：十月"},
            {
                "type": "function_call",
                "name": "read_skill",
                "call_id": "old",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "old",
                "output": "UNIQUE_DELETED_INSTRUCTION",
            },
            {"role": "assistant", "content": "报告差额为100元"},
        ]
        asyncio.run(session.add_items(items))
        result = asyncio.run(session.get_items())
        self.assertNotIn("UNIQUE_DELETED_INSTRUCTION", str(result))
        self.assertIn("业务月份", str(result))
        self.assertIn("差额为100", str(result))
        self.assertEqual(
            len(
                asyncio.run(WorkbenchSession("conv", db_path=self.db_path).get_items())
            ),
            4,
        )

    def test_large_catalog_has_bounded_prompt_and_search(self):
        import json

        ref = self.publish(self.ready())
        with self.library.connection() as db:
            for number in range(120):
                skill_id = f"user-{number:032x}"
                db.execute(
                    "INSERT INTO user_skills("
                    "skill_id,title,status,enabled,current_version,created_at,updated_at,deleted_at"
                    ") SELECT ?,title,status,enabled,current_version,created_at,updated_at,"
                    "deleted_at FROM user_skills WHERE skill_id=?",
                    (skill_id, ref.skill_id),
                )
                row = db.execute(
                    "SELECT * FROM user_skill_versions WHERE skill_id=?",
                    (ref.skill_id,),
                ).fetchone()
                values = list(row)
                values[0] = skill_id
                values[3] = "说明" * 450
                summary = json.loads(values[4])
                summary["name"] = skill_id
                values[4] = json.dumps(summary)
                db.execute(
                    "INSERT INTO user_skill_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
            db.commit()
        self.bind()
        prompt = self.runtime().directory_prompt("run")
        self.assertLessEqual(len(prompt.split("\n", 1)[1]), 8000)
        self.assertLessEqual(
            len(self.runtime().search_for_run("run", query="月度")), 20
        )

    def test_delete_races_with_start_and_load(self):
        from services.skills.contracts import SkillProblem

        for number in range(12):
            ref = self.publish(self.ready(), f"publish-{number}")
            run_id = f"race-{number}"
            self.bind(run_id=run_id)

            def remove():
                try:
                    self.library.delete(ref.skill_id, expected_version=1)
                    return "deleted"
                except SkillProblem:
                    return "blocked"

            def load():
                try:
                    self.runtime().load_for_run(run_id, ref.skill_id)
                    return "loaded"
                except SkillProblem:
                    return "blocked"

            with ThreadPoolExecutor(max_workers=2) as pool:
                deleting = pool.submit(remove)
                loading = pool.submit(load)
                outcome = (deleting.result(), loading.result())
            self.assertIn(outcome, {("deleted", "blocked"), ("blocked", "loaded")})
