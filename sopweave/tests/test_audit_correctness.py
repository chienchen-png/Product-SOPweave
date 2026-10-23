"""Behavior regressions from the open source readiness audit (isolated data)."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pandas as pd

from agent_runtime.context import WorkbenchRunContext
from agent_runtime.tools.catalog import ToolCatalog
from storage.project_store import ProjectStore
from tools.project_data_importer import ProjectDataImporter
from tools.financial_analysis_tools import financial_metrics
from tools._finmod_models import _calc_f1, run_model


class AuditCorrectnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="审计 回归 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "test.db"
        with closing(self.connect()) as db:
            db.executescript(Path("storage/schema.sql").read_text(encoding="utf-8"))
            self.project = ProjectStore(db).create("audit", str(self.root), default_model="model")

    def connect(self):
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        return db

    def test_filtered_aggregation_uses_where_before_grouping(self):
        book = openpyxl.Workbook()
        for row in [["部门", "区域", "收入"], ["销售", "A", 10], ["销售", "B", 20], ["其他", None, 5]]:
            book.active.append(row)
        book.save(self.root / "data.xlsx")
        book.close()
        pid = self.project["id"]
        with closing(self.connect()) as db:
            ProjectDataImporter(db).import_workbook(pid, "data.xlsx", str(self.root), anchor_rows={"Sheet": 0})
        context = WorkbenchRunContext(project_id=pid, work_dir=self.root, run_id="r", conversation_id="c", work_mode="auto", agent_id="general-agent", database_factory=self.connect)
        spec = ToolCatalog.default()._specs["query_dataset"]
        result = asyncio.run(spec.invoke(context, {"file": "data.xlsx", "filters": [{"field": "区域", "value": "A"}], "group_by": ["部门"], "metrics": [{"function": "sum", "field": "收入"}]}))
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"], [{"部门": "销售", "sum(收入)": 10}])

    def test_ccc_requires_payables_and_matches_integrated_calculation(self):
        frame = pd.DataFrame({"营业收入": [365], "营业成本": [365], "应收账款": [10], "存货": [20]})
        kwargs = dict(metrics=["ccc"], revenue_col="营业收入", cost_col="营业成本", receivables_col="应收账款", inventory_col="存货")
        with patch("tools.financial_analysis_tools._load_dataframe", return_value=frame):
            result = financial_metrics("data.xlsx", **kwargs)["result"]["ccc"]
        self.assertIsNone(result["现金周期CCC"])
        self.assertIn("应付", result["说明"])
        self.assertEqual(result, _calc_f1(frame, {})["summary"])
        frame["应付账款"] = [5]
        with patch("tools.financial_analysis_tools._load_dataframe", return_value=frame):
            result = financial_metrics("data.xlsx", payables_col="应付账款", **kwargs)["result"]["ccc"]
        self.assertEqual(result["现金周期CCC"], 25)
        self.assertEqual(result, _calc_f1(frame, {})["summary"])

    def test_finmod_invalid_inputs_do_not_return_empty_success(self):
        cases = [
            (
                "a2-income-forecast",
                pd.DataFrame({"无收入": [100]}),
                {},
            ),
            (
                "a3-cashflow-forecast",
                pd.DataFrame({"经营活动净流量": [100]}),
                {},
            ),
            ("b1-budget", pd.DataFrame({"收入": [pd.NA]}), {}),
            (
                "e4-scenario",
                pd.DataFrame({"收入": [100], "成本": [60]}),
                {},
            ),
        ]
        for model_id, frame, params in cases:
            with self.subTest(model_id=model_id):
                result = run_model(model_id, frame, params)
                self.assertFalse(result["ok"], result)
                self.assertTrue(result["summary"].get("错误"), result)

    def test_finmod_outputs_are_strict_json_and_undefined_values_are_null(self):
        cases = [
            (
                "c4-product-profitability",
                pd.DataFrame({"产品线": ["甲"], "收入": [0], "成本": [10]}),
                {},
                "盈利Top",
                "毛利率",
            ),
            (
                "d4-yoy-mom",
                pd.DataFrame({"期间": ["Q1", "Q2"], "收入": [0, 100]}),
                {},
                "最新环比/同比/占比",
                "收入_环比",
            ),
            (
                "e1-npv-irr",
                pd.DataFrame({"现金流": [100, 110]}),
                {"discount_rate": 0.1},
                None,
                "IRR",
            ),
        ]
        for model_id, frame, params, container_key, value_key in cases:
            with self.subTest(model_id=model_id):
                result = run_model(model_id, frame, params)
                self.assertTrue(result["ok"], result)
                json.dumps(result["summary"], allow_nan=False, ensure_ascii=False)
                values = (
                    result["summary"][container_key]
                    if container_key
                    else result["summary"]
                )
                row = values[0] if isinstance(values, list) else values
                self.assertIsNone(row[value_key], result)
        constant = run_model(
            "g2-correlation",
            pd.DataFrame({"x": [1, 1, 1], "y": [2, 4, 6]}),
            {},
        )
        json.dumps(constant["summary"], allow_nan=False, ensure_ascii=False)
        self.assertIsNone(constant["summary"]["matrix"][0][0])

    def test_finmod_budget_variance_uses_four_ratio_decimals(self):
        result = run_model(
            "d3-budget-variance",
            pd.DataFrame({"实际值": [110, 180], "预算值": [100, 200]}),
            {},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["summary"]["总差异"], -10)
        self.assertEqual(result["summary"]["差异率"], -0.0333)

    def test_reconciliation_report_cannot_claim_zero_unmatched_when_c_items_exist(self):
        from services.reconciliation import (
            _fallback_report,
            _report_facts_consistent,
        )

        result = {
            "summary": {
                "total": 5,
                "matched": 4,
                "ok": 1,
                "a1": 1,
                "a2": 1,
                "b": 1,
                "c": 1,
                "unmatched": 0,
                "audit": 1,
            },
            "differences": [{"level": "B", "key1": "C004", "source_amount": 400, "target_amount": 450, "diff": -50, "reason": "超差"}],
            "unmatched": [],
            "audit_items": [{"key": "C005", "reason": "目标表无对应记录"}],
        }
        params = {
            "baseFile": "依据表.xlsx",
            "detailFile": "分表.xlsx",
            "matchKeys": {"k1": "编号"},
            "variables": ["金额"],
            "baseAmountCol": "金额",
            "tolerance": 1,
        }
        self.assertFalse(
            _report_facts_consistent("匹配4行，未匹配0行。本次无匹配不上的记录。", result)
        )
        fallback = _fallback_report(params, result)
        self.assertIn("未匹配（C）1", fallback)
        self.assertIn("C005", fallback)
        self.assertTrue(_report_facts_consistent(fallback, result))

    def test_reconciliation_fact_correction_does_not_claim_model_unavailable(self):
        from services.reconciliation import _fallback_report

        report = _fallback_report(
            {
                "baseFile": "依据表.xlsx",
                "detailFile": "分表.xlsx",
                "matchKeys": {"k1": "编号"},
                "variables": ["金额"],
                "baseAmountCol": "金额",
                "tolerance": 1,
            },
            {
                "summary": {
                    "total": 5,
                    "matched": 4,
                    "ok": 1,
                    "a1": 1,
                    "a2": 1,
                    "b": 1,
                    "c": 1,
                },
                "differences": [],
                "unmatched": [],
                "audit_items": [{"key": "C005", "reason": "目标表无对应记录"}],
            },
            reason="fact_correction",
        )

        self.assertIn("AI 报告与核对引擎事实不一致", report)
        self.assertIn("AI 事实校验未通过", report)
        self.assertNotIn("未接入 AI 归因", report)
        self.assertNotIn("当前模型不可用", report)
        self.assertIn("C005", report)

    def test_zero_discount_rate_is_not_defaulted(self):
        with patch("tools.financial_analysis_tools._load_dataframe", return_value=pd.DataFrame({"现金流": [-100, 110]})):
            result = financial_metrics("data.xlsx", metrics=["capital"], cashflow_col="现金流", discount_rate=0)["result"]["capital"]
        self.assertEqual(result["NPV"], 10)
        self.assertEqual(result["折现率"], 0)

    def test_project_default_model_can_be_cleared_and_omitted(self):
        with closing(self.connect()) as db:
            store = ProjectStore(db)
            pid = self.project["id"]
            self.assertEqual(store.update(pid, name="renamed")["default_model"], "model")
            self.assertIsNone(store.update(pid, default_model=None)["default_model"])

    def test_compound_chart_names_survive_suggestions(self):
        from services.finmod_charts import _finmod_chart_suggest
        result = _finmod_chart_suggest(["g5-forecasting"], ["error-bar-range", "dual-axis"], "L3", {})
        templates = {item["template"] for item in result["suggestions"]}
        self.assertIn("error-bar-range", templates)
        self.assertIn("dual-axis", templates)

    def test_deleted_project_removes_runtime_state_but_preserves_working_files(self):
        from agent_runtime.service import RunService
        from storage.run_store import RunStore
        RunService(db_path=self.database)
        path = self.root / "keep.txt"
        path.write_text("user data", encoding="utf-8")
        with closing(self.connect()) as db:
            pid = self.project["id"]
            RunStore(db).create_run("r", project_id=pid, conversation_id="c")
            RunStore(db).update_run_status("r", "completed")
            db.execute("INSERT INTO agent_run_requests VALUES (?, ?)", ("r", "{}"))
            db.execute("INSERT INTO agent_session_items VALUES (?, ?, ?, ?)", ("c:general-agent:fake", 1, "{}", "now"))
            db.commit()
            self.assertTrue(ProjectStore(db).delete(pid))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM runs WHERE project_id=?", (pid,)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM agent_run_requests").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM agent_session_items").fetchone()[0], 0)
        self.assertEqual(path.read_text(encoding="utf-8"), "user data")

    def test_resume_retry_after_disconnected_acknowledgement_completes_once(self):
        from agents.testing import ScriptedModel, ModelStep, function_call, assistant_message
        from agent_runtime.service import RunService, RunRequest, ResumeCommand

        async def scenario():
            model = ScriptedModel([
                ModelStep(output=[function_call("ask_user", {"questions": [{"id": "q", "title": "期间", "kind": "text"}]}, call_id="ask")]),
                ModelStep(output=[assistant_message("done")]),
            ])
            service = RunService(db_path=self.database, model_provider=lambda _: model)
            request = RunRequest("resume-gap", "ask", self.project["id"], "c", "general-agent", "fake", self.root)
            events = [e async for e in service.start(request)]
            question = next(e.payload for e in events if e.type == "input.required")
            command = ResumeCommand(action="respond", interaction_id=question["interaction_id"], answers=[{"question_id": "q", "text": "本期"}])
            iterator = service.resume(request.run_id, command)
            await anext(iterator)
            await iterator.aclose()
            restarted = RunService(db_path=self.database, model_provider=lambda _: model)
            await restarted.recover_pending()
            with closing(self.connect()) as db:
                self.assertEqual(db.execute("SELECT status FROM runs WHERE id=?", (request.run_id,)).fetchone()[0], "completed")
            events = [e.type async for e in restarted.resume(request.run_id, command)]
            self.assertIn("run.completed", events)
            repeated = [e async for e in restarted.resume(request.run_id, command)]
            self.assertTrue(repeated[-1].payload.get("reason") == "already_submitted")
            with closing(self.connect()) as db:
                self.assertEqual(db.execute("SELECT status FROM runs WHERE id=?", (request.run_id,)).fetchone()[0], "completed")
        asyncio.run(scenario())

    def test_continuation_claim_has_one_owner_and_crash_is_not_replayed(self):
        from agent_runtime.continuations import ContinuationStore
        from storage.run_store import RunStore
        store = ContinuationStore(self.database)
        with closing(self.connect()) as db:
            RunStore(db).create_run("owned", project_id=self.project["id"])
            db.execute("INSERT INTO agent_continuations VALUES (?,'pending',NULL,NULL,?)", ("owned", "now"))
            db.commit()
        token = store.claim("owned")
        self.assertIsNotNone(token)
        self.assertIsNone(ContinuationStore(self.database).claim("owned"))
        with patch("agent_runtime.continuations.process_alive", return_value=False):
            recovered = ContinuationStore(self.database)
        self.assertIsNone(recovered.claim("owned"))
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT status FROM runs WHERE id='owned'").fetchone()[0], "failed")

    def test_active_project_cannot_be_deleted(self):
        from storage.run_store import RunStore
        with closing(self.connect()) as db:
            RunStore(db).create_run("active", project_id=self.project["id"])
            with self.assertRaisesRegex(ValueError, "正在执行"):
                ProjectStore(db).delete(self.project["id"])
            self.assertIsNotNone(ProjectStore(db).get_by_id(self.project["id"]))

    def test_3d_run_endpoints_do_not_crash_on_missing_pandas_binding(self):
        from flask import Flask
        from routes import apps
        import storage.tmp_db as tmp_module
        book = openpyxl.Workbook()
        for row in [["类别", "金额"], ["A", 10], ["B", 20]]:
            book.active.append(row)
        book.save(self.root / "data.xlsx")
        book.close()
        with closing(self.connect()) as db:
            ProjectDataImporter(db).import_workbook(self.project["id"], "data.xlsx", str(self.root), anchor_rows={"Sheet": 0})
            web = Flask(__name__)
            web.register_blueprint(apps.finmod_bp)
            with patch("storage.db.get_db", return_value=db), patch.object(tmp_module, "TMP_RUNS_DIR", self.root / "tmp_runs"):
                try:
                    for template in ["bar3d", "waterfall3d"]:
                        response = web.test_client().post("/api/apps/finmod/run", json={"project_id": self.project["id"], "file_name": "data.xlsx", "model_ids": ["g1-descriptive-stats"], "chart_manifest": [{"id": "chart", "template": template}]})
                        self.assertEqual(response.status_code, 200, response.get_json())
                finally:
                    for rid in tmp_module.list_runs():
                        run = tmp_module.get_run(rid)
                        if run and run.dir.is_relative_to(self.root):
                            tmp_module.unregister_run(rid)
                            run.cleanup()

    def test_file_editor_rejects_stale_or_missing_version(self):
        from flask import Flask
        from routes.files import files_bp
        path = self.root / "report.md"
        path.write_text("original", encoding="utf-8")
        web = Flask(__name__)
        web.register_blueprint(files_bp)
        with closing(self.connect()) as db, patch("routes.files.get_db", return_value=db):
            client = web.test_client()
            args = {"project_id": self.project["id"], "path": "report.md"}
            loaded = client.get("/api/files/read", query_string=args).get_json()["data"]
            self.assertIn("version", loaded)
            path.write_text("external edit", encoding="utf-8")
            stale = client.put("/api/files/write", json={**args, "content": "overwrite", "expected_version": loaded["version"]})
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(path.read_text(encoding="utf-8"), "external edit")
            missing = client.put("/api/files/write", json={**args, "content": "overwrite"})
            self.assertEqual(missing.status_code, 400)
            latest = client.get("/api/files/read", query_string=args).get_json()["data"]
            saved = client.put("/api/files/write", json={**args, "content": "saved", "expected_version": latest["version"]})
            self.assertEqual(saved.status_code, 200)
            self.assertNotEqual(saved.get_json()["data"]["version"], latest["version"])
            self.assertEqual(path.read_text(encoding="utf-8"), "saved")


if __name__ == "__main__":
    unittest.main()
