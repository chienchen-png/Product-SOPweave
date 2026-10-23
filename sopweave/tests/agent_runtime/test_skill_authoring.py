from __future__ import annotations
import asyncio
import json
import unittest
from agents.testing import ScriptedModel, ModelStep, function_call, assistant_message
from services.skills.authoring import SkillAuthoringService, redact_source
from tests.agent_runtime.skill_fixtures import SkillTestFixture
from tests.agent_runtime.test_skill_contracts import sample_spec


class SkillAuthoringTests(SkillTestFixture, unittest.TestCase):
    def test_conversation_preparation_reads_full_runtime_history(self):
        from agent_runtime.service import RunService, RunRequest
        from agent_runtime.tools.skill_authoring import (
            prepare_skill_draft,
            PrepareSkillDraft,
        )
        from tests.agent_runtime.test_run_service import _collect

        prompt = "规则说明：" + "流程说明" * 30 + " FINAL_RULE 币种人民币"
        model = ScriptedModel([ModelStep(output=[assistant_message("已形成报告")])])
        service = RunService(
            db_path=self.db_path,
            skill_library=self.library,
            model_provider=lambda _: model,
        )
        request = RunRequest(
            "history-proof",
            prompt,
            "project",
            "conversation",
            "general-agent",
            "test",
            self.root,
        )
        asyncio.run(_collect(service.start(request)))
        from agent_runtime.context import WorkbenchRunContext

        context = WorkbenchRunContext(
            project_id="project",
            work_dir=self.root,
            run_id="capture",
            conversation_id="conversation",
            work_mode="auto",
            agent_id="general-agent",
            skill_registry=service.skill_registry,
            skill_runtime=service.skill_runtime,
        )
        result = prepare_skill_draft(
            context,
            PrepareSkillDraft(source_kind="conversation", run_ids=["history-proof"]),
        )
        source = self.library.get_draft(result["draft_id"])["source"][0]
        self.assertIn("FINAL_RULE", source["text"])
        self.assertIn("已形成报告", source["text"])

    def generate(self, submission):
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call("submit_skill_draft", submission, call_id="draft")
                    ]
                )
            ]
        )
        service = SkillAuthoringService(self.library, model_provider=lambda _: model)
        draft = self.library.create_draft(
            source=[{"kind": "text", "text": "请生成月度核对 SOP"}]
        )

        async def run():
            job = await service.begin(
                draft["draft_id"], expected_revision=1, model_id="test"
            )
            return [e async for e in service.events(job["job_id"])]

        events = asyncio.run(run())
        return self.library.get_draft(draft["draft_id"]), events

    def test_missing_currency_produces_question(self):
        draft, events = self.generate(
            {
                "spec": None,
                "questions": [
                    {
                        "question_id": "currency",
                        "prompt": "采用什么币种？",
                        "required": True,
                        "options": ["人民币", "美元"],
                    }
                ],
            }
        )
        self.assertEqual(draft["state"], "needs_input")
        self.assertEqual(draft["questions"][0]["question_id"], "currency")
        self.assertIn("question", [e["type"] for e in events])

    def test_submission_accepts_only_real_tools(self):
        draft, events = self.generate(
            {"spec": sample_spec().model_dump(), "questions": []}
        )
        self.assertEqual(draft["state"], "ready", events)
        self.assertTrue(draft["generated_digest"])
        self.assertEqual(self.library.list(), [])

    def test_sdk_validation_error_is_repaired_once_with_field_feedback(self):
        invalid = sample_spec().model_dump()
        invalid["steps"][0]["id"] = "invalid_step"
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "submit_skill_draft",
                            {"spec": invalid, "questions": []},
                            call_id="bad",
                        )
                    ]
                ),
                ModelStep(
                    output=[
                        function_call(
                            "submit_skill_draft",
                            {"spec": sample_spec().model_dump(), "questions": []},
                            call_id="fixed",
                        )
                    ]
                ),
            ]
        )
        service = SkillAuthoringService(self.library, model_provider=lambda _: model)
        draft = self.library.create_draft(
            source=[{"kind": "text", "text": "读取并汇报"}]
        )

        async def run():
            job = await service.begin(
                draft["draft_id"], expected_revision=1, model_id="test"
            )
            return [event async for event in service.events(job["job_id"])]

        events = asyncio.run(run())
        self.assertEqual(
            self.library.get_draft(draft["draft_id"])["state"], "ready", events
        )
        self.assertEqual(self.library.list(), [])

    def test_terminal_validation_failure_keeps_safe_actionable_feedback(self):
        invalid = sample_spec().model_dump()
        invalid["steps"][0]["id"] = "invalid_step"
        model = ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call(
                            "submit_skill_draft",
                            {"spec": invalid, "questions": []},
                            call_id="bad-one",
                        )
                    ]
                ),
                ModelStep(
                    output=[
                        function_call(
                            "submit_skill_draft",
                            {"spec": invalid, "questions": []},
                            call_id="bad-two",
                        )
                    ]
                ),
            ]
        )
        service = SkillAuthoringService(self.library, model_provider=lambda _: model)
        source_text = "仅用于诊断的月末核对要求"
        draft = self.library.create_draft(
            source=[{"kind": "text", "text": source_text}]
        )

        async def run():
            job = await service.begin(
                draft["draft_id"], expected_revision=1, model_id="test"
            )
            return [event async for event in service.events(job["job_id"])]

        events = asyncio.run(run())
        current = self.library.get_draft(draft["draft_id"])
        self.assertEqual(current["state"], "failed")
        self.assertTrue(current["job_status"]["validation"])
        self.assertTrue(
            current["job_status"]["validation"][0]["field"].startswith("spec")
        )
        self.assertIn("spec.steps.0.id", current["job_status"]["message"])
        self.assertIn("pattern", current["job_status"]["message"])
        self.assertNotIn(
            source_text, json.dumps(current["job_status"], ensure_ascii=False)
        )
        self.assertIn("validation", [event["type"] for event in events])

    def test_unknown_program_check_is_preserved_as_advisory_rule(self):
        submitted = sample_spec().model_dump()
        submitted["execution_policy"]["checks"] = [
            "required_input",
            "按凭证号匹配，差额绝对值不超过0.01元",
        ]
        draft, events = self.generate(
            {"spec": submitted, "questions": []}
        )

        self.assertEqual(draft["state"], "ready", events)
        self.assertEqual(
            draft["spec"]["execution_policy"]["checks"],
            ["required_input"],
        )
        self.assertIn(
            "按凭证号匹配，差额绝对值不超过0.01元",
            draft["spec"]["advisory_rules"],
        )
        self.assertEqual(
            draft["job_status"]["normalizations"],
            [{
                "field": "spec.execution_policy.checks",
                "moved_to": "advisory_rules",
                "count": 1,
            }],
        )

    def test_conversation_extraction_omits_keys_and_sample_values(self):
        text = "key sk-abc12345678901234567890 path D:\\customer\\private.xlsx 金额 1234.56 账号6222021234567890123"
        redacted = redact_source(text)
        self.assertNotIn("sk-abc", redacted)
        self.assertNotIn("D:\\customer", redacted)
        self.assertNotIn("1234.56", redacted)
        self.assertNotIn("6222021234567890123", redacted)
        chinese = redact_source(
            "银行流水270.00，账单170.00，差额100.00。账号6222021234567890123。"
        )
        for value in ["270.00", "170.00", "100.00", "6222021234567890123"]:
            self.assertNotIn(value, chinese)

    def test_cancelled_job_cannot_overwrite_newer_draft(self):
        async def slow(_):
            await asyncio.sleep(5)

        service = SkillAuthoringService(self.library, model_provider=slow)
        draft = self.library.create_draft()

        async def run():
            job = await service.begin(
                draft["draft_id"], expected_revision=1, model_id="test"
            )
            await service.cancel(draft["draft_id"], job_id=job["job_id"])
            return self.library.get_draft(draft["draft_id"])

        self.assertEqual(asyncio.run(run())["state"], "cancelled")

    def test_real_http_adapter_generates_reviewable_draft(self):
        self._assert_http_draft(
            "openai_compatible",
            {"type": "function", "function": {"name": "submit_skill_draft"}},
        )

    def test_glm_http_adapter_uses_auto_and_generates_reviewable_draft(self):
        self._assert_http_draft("zhipu", "auto")

    def _assert_http_draft(self, provider_kind, expected_choice):
        from tests.agent_runtime.fake_provider import FakeProviderServer
        from storage.model_store import ModelStore
        from storage.db import _ensure_model_schema

        with FakeProviderServer(
            skill_submission={"spec": sample_spec().model_dump(), "questions": []}
        ) as server:
            with self.library.connection() as db:
                _ensure_model_schema(db)
                provider = ModelStore(db).create_provider(
                    {
                        "name": "test",
                        "api_url": server.base_url,
                        "api_key": "local",
                        "provider_kind": provider_kind,
                    }
                )
                db.execute(
                    "INSERT INTO models(id,name,api_url,api_key,type,api_model,context_window,status,created_at,updated_at,provider_id) VALUES('http','test',?,'local','chat','skill-http',65536,'available','now','now',?)",
                    (server.base_url, provider["id"]),
                )
                db.commit()
            service = SkillAuthoringService(self.library)
            draft = self.library.create_draft(
                source=[{"kind": "text", "text": "请生成核对流程"}]
            )

            async def run():
                job = await service.begin(
                    draft["draft_id"], expected_revision=1, model_id="http"
                )
                return [event async for event in service.events(job["job_id"])]

            events = asyncio.run(run())
            self.assertEqual(
                self.library.get_draft(draft["draft_id"])["state"], "ready", events
            )
            ready = self.library.get_draft(draft["draft_id"])
            self.library.publish(
                ready["draft_id"],
                expected_revision=ready["draft_revision"],
                confirmed_digest=ready["generated_digest"],
                idempotency_key="http-authoring",
            )
            self.assertEqual(server.request_count("skill-http"), 1)
            self.assertEqual(
                server.last_request("skill-http")["tool_choice"],
                expected_choice,
            )

    def test_backpressure_and_cancellation_restore_capacity(self):
        from services.skills.authoring import SkillBusy

        async def slow(_):
            await asyncio.sleep(5)

        service = SkillAuthoringService(self.library, model_provider=slow)
        drafts = [self.library.create_draft() for _ in range(23)]

        async def run():
            for draft in drafts[:22]:
                await service.begin(
                    draft["draft_id"], expected_revision=1, model_id="test"
                )
            with self.assertRaises(SkillBusy):
                await service.begin(
                    drafts[-1]["draft_id"], expected_revision=1, model_id="test"
                )
            await asyncio.sleep(0.05)
            with self.library.connection() as db:
                count = db.execute(
                    "SELECT count(*) FROM skill_drafts WHERE state='generating' AND json_extract(job_status_json,'$.state')='active'"
                ).fetchone()[0]
            self.assertLessEqual(count, 2)
            await service.shutdown()
            with self.library.connection() as db:
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM skill_drafts WHERE state='generating'"
                    ).fetchone()[0],
                    0,
                )

        asyncio.run(run())

    def test_rate_limit_preserves_source_and_does_not_expose_upstream_body(self):
        from tests.agent_runtime.fake_provider import FakeProviderServer

        with FakeProviderServer() as server:
            with self.library.connection() as db:
                db.execute(
                    "INSERT INTO models(id,name,api_url,api_key,type,api_model,context_window,status,created_at,updated_at) VALUES('limited','test',?,'local','chat','error-429',65536,'available','now','now')",
                    (server.base_url,),
                )
                db.commit()
            service = SkillAuthoringService(self.library)
            draft = self.library.create_draft(
                source=[{"kind": "text", "text": "保留我的核对规则"}]
            )

            async def run():
                job = await service.begin(
                    draft["draft_id"], expected_revision=1, model_id="limited"
                )
                return [event async for event in service.events(job["job_id"])]

            events = asyncio.run(run())
            current = self.library.get_draft(draft["draft_id"])
            self.assertEqual(current["state"], "failed", events)
            self.assertEqual(current["job_status"]["upstream_status"], 429)
            self.assertIn("容量不足", current["job_status"]["message"])
            self.assertEqual(current["source"], draft["source"])
            self.assertNotIn("UPSTREAM-SECRET-BODY", str(events) + str(current))

    def test_prepare_tool_cannot_read_other_conversation(self):
        from agent_runtime.context import WorkbenchRunContext
        from agent_runtime.definitions.skill_registry import SkillRegistry
        from agent_runtime.skill_runtime import SkillRuntime
        from agent_runtime.tools.skill_authoring import (
            prepare_skill_draft,
            PrepareSkillDraft,
        )
        from services.skills.contracts import SkillProblem

        registry = SkillRegistry(library=self.library)
        context = WorkbenchRunContext(
            project_id="p",
            work_dir=self.root,
            run_id="r",
            conversation_id="other",
            work_mode="auto",
            agent_id="general-agent",
            skill_registry=registry,
            skill_runtime=SkillRuntime(self.library, registry),
        )
        with self.assertRaises(SkillProblem):
            prepare_skill_draft(context, PrepareSkillDraft(source_kind="conversation"))
        result = prepare_skill_draft(context, PrepareSkillDraft(text="保存核对流程"))
        self.assertTrue(result["requires_user_confirmation"])
        self.assertEqual(self.library.list(), [])

    def test_nested_json_object_from_compatible_provider_is_strictly_validated(self):
        from pydantic import ValidationError
        from services.skills.authoring import DraftSubmission

        draft, events = self.generate(
            {"spec": json.dumps(sample_spec().model_dump()), "questions": []}
        )
        self.assertEqual(draft["state"], "ready", events)
        for invalid in [
            "{",
            "[]",
            "null",
            json.dumps({"title": "missing required fields"}),
        ]:
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                DraftSubmission.model_validate({"spec": invalid, "questions": []})

    def test_prepare_github_multi_skill_choice_is_forwarded(self):
        from unittest.mock import patch
        from agent_runtime.context import WorkbenchRunContext
        from agent_runtime.definitions.skill_registry import SkillRegistry
        from agent_runtime.skill_runtime import SkillRuntime
        from agent_runtime.tools.skill_authoring import (
            prepare_skill_draft,
            PrepareSkillDraft,
        )

        registry = SkillRegistry(library=self.library)
        context = WorkbenchRunContext(
            project_id="p",
            work_dir=self.root,
            run_id="r",
            conversation_id="c",
            work_mode="auto",
            agent_id="general-agent",
            skill_registry=registry,
            skill_runtime=SkillRuntime(self.library, registry),
        )
        with patch(
            "services.skills.imports.SkillImporter.preview_github",
            return_value={
                "choices": [".", "monthly"],
                "resolved_url": "https://github.com/example/repo/tree/" + "a" * 40,
            },
        ) as preview:
            result = prepare_skill_draft(
                context,
                PrepareSkillDraft(
                    source_kind="github",
                    github_url="https://github.com/example/repo",
                    skill_path=".",
                ),
            )
        self.assertTrue(result["requires_selection"])
        self.assertNotIn("draft_id", result)
        preview.assert_called_once_with(
            "https://github.com/example/repo", skill_path="."
        )
