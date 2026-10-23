from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_runtime.tools.catalog import ToolCatalog
from agent_runtime.context import WorkbenchRunContext


class ToolDocumentationTests(unittest.TestCase):
    def test_invalid_chart_template_can_be_corrected_without_changing_source_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {'template':'invented','title':'核验','data':{
                'categories':['A'],'series':[{'name':'金额','data':[123.45]}],
            },'data_table':[{'项目':'A','金额':123.45}]}
            path = root/'chart.json'
            context = WorkbenchRunContext(project_id='p',work_dir=root,run_id='r',
                conversation_id='c',work_mode='auto',agent_id='general-agent')
            spec = ToolCatalog.default()._specs['generate_chart']
            path.write_text(json.dumps(payload),encoding='utf8')
            denied = asyncio.run(spec.invoke(context,{'chart_file':'chart.json'}))
            self.assertFalse(denied['success'])
            self.assertIn('未知模板',denied['error'])
            payload['template']='bar'
            path.write_text(json.dumps(payload),encoding='utf8')
            corrected = asyncio.run(spec.invoke(context,{'chart_file':'chart.json'}))
            self.assertTrue(corrected['success'],corrected)
            self.assertEqual(corrected['result']['option']['series'][0]['data'],[123.45])

    def test_documented_analysis_examples_produce_real_results(self):
        import openpyxl
        from tools.project_data_importer import ProjectDataImporter

        source = Path(__file__).resolve().parents[2]
        document = (
            source / "skills/financial-modeling/references/tool-examples.md"
        ).read_text(encoding="utf-8")
        examples = [
            json.loads(arguments)
            for name, arguments in re.findall(
                r"```json tool=(\w+)\s*\n(.*?)\n```", document, re.DOTALL
            )
            if name == "analyze_dataset"
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = openpyxl.Workbook()
            book.active.append(["收入", "净利润"])
            book.active.append([100, 20])
            book.save(root / "报表.xlsx")
            book.close()

            def factory():
                connection = sqlite3.connect(root / "data.db")
                connection.row_factory = sqlite3.Row
                return connection

            with closing(factory()) as db:
                db.executescript(
                    (source / "storage/schema.sql").read_text(encoding="utf-8")
                )
                ProjectDataImporter(db).import_workbook(
                    "p", "报表.xlsx", str(root), anchor_rows={"Sheet": 0}
                )
            context = WorkbenchRunContext(
                project_id="p",
                work_dir=root,
                run_id="r",
                conversation_id="c",
                work_mode="auto",
                agent_id="general-agent",
                database_factory=factory,
            )
            spec = ToolCatalog.default()._specs["analyze_dataset"]
            results = [
                asyncio.run(spec.invoke(context, arguments)) for arguments in examples
            ]
            self.assertTrue(all(result["success"] for result in results), results)
            self.assertEqual(results[0]["result"]["ratios"]["净利率"], 0.2)
            self.assertEqual(results[1]["result"]["base_result"], 10000)
            quantity = next(
                item
                for item in results[1]["result"]["one_way"]
                if item["variable"] == "qty"
            )
            self.assertEqual(
                [item["result"] for item in quantity["changes"]], [6000, 10000, 14000]
            )
            self.assertEqual(results[2]["result"]["scenarios"]["悲观"], 0)

    def test_sensitivity_explicit_values_and_existing_app_multiplier_ranges(self):
        from tools.financial_analysis_tools import sensitivity_analysis

        explicit = sensitivity_analysis(
            model_type="custom_formula",
            formula="x+2*y",
            base_inputs={"x": 0, "y": 2},
            variable_ranges={"x": {"values": [0, 2]}, "y": {"values": [2, 4]}},
            two_way_vars=["x", "y"],
        )
        self.assertEqual(explicit["result"]["two_way"]["grid"], [[4, 8], [6, 10]])
        self.assertEqual(explicit["result"]["two_way"]["x"]["values"], [0, 2])
        existing = sensitivity_analysis(
            model_type="custom_formula",
            formula="x+y",
            base_inputs={"x": 2, "y": 4},
            variable_ranges={"x": {"steps": [0.5, 1.5]}},
        )
        changes = existing["result"]["one_way"][0]["changes"]
        self.assertEqual([item["result"] for item in changes], [5, 7])

    def test_runtime_markdown_has_no_retired_public_tool_references(self):
        retired = {
            "import_excel_to_db",
            "query_table",
            "table_stats",
            "join_tables",
            "update_rows",
            "insert_rows",
            "delete_rows",
            "export_project_data",
            "list_data_files",
            "get_table_index",
            "get_sheet_info",
            "read_excel",
            "write_excel",
            "analyze_excel",
            "compare_excel_sheets",
            "read_file",
            "write_file",
            "list_directory",
            "run_command",
            "run_python_code",
            "financial_metrics",
            "regression_analysis",
            "correlation_matrix",
            "time_series_analysis",
            "sensitivity_analysis",
            "read_skill_resource",
        }
        pattern = re.compile(r"\b(?:" + "|".join(retired) + r")\b")
        root = Path(__file__).resolve().parents[2]
        for directory in ("agent_profiles", "skills"):
            for path in (root / directory).rglob("*.md"):
                self.assertEqual(
                    pattern.findall(path.read_text(encoding="utf-8")), [], str(path)
                )

    def test_documented_tool_arguments_validate_against_live_schemas(self):
        catalog = ToolCatalog.default()
        root = Path(__file__).resolve().parents[2] / "skills"
        examples = 0
        for path in root.rglob("*.md"):
            for name, arguments in re.findall(
                r"```json tool=(\w+)\s*\n(.*?)\n```",
                path.read_text(encoding="utf-8"),
                re.DOTALL,
            ):
                self.assertIn(name, catalog.names, str(path))
                catalog._specs[name].input_model.model_validate(json.loads(arguments))
                examples += 1
        self.assertGreaterEqual(examples, 5)
