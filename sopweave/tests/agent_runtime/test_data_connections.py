from __future__ import annotations

import asyncio
import sqlite3
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from agent_runtime.context import WorkbenchRunContext
from agent_runtime.definitions.agent_registry import AgentRegistry
from agent_runtime.tools.catalog import ToolCatalog
from tests.agent_runtime.test_tool_catalog import _invoke


class DataConnectionTests(unittest.TestCase):
    def test_asgi_sdk_query_and_transaction_rollback_use_real_project_data(self):
        import openpyxl
        from fastapi.testclient import TestClient
        from agents.testing import (
            ScriptedModel,
            ModelStep,
            function_call,
            assistant_message,
        )
        from agent_runtime.api import create_agent_api
        from agent_runtime.service import RunService
        from agent_runtime.definitions.agent_registry import (
            AgentDefinition,
            AgentManifest,
        )
        from tests.agent_runtime.test_run_service import _Registry
        from tools.project_data_importer import ProjectDataImporter

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data.xlsx"
            book = openpyxl.Workbook()
            book.active.append(["code", "amount"])
            book.active.append(["A", 10])
            book.save(path)
            book.close()
            database = root / "test.db"
            db = sqlite3.connect(database)
            db.row_factory = sqlite3.Row
            db.executescript(Path("storage/schema.sql").read_text(encoding="utf-8"))
            ProjectDataImporter(db).import_workbook(
                "p1", path.name, str(root), anchor_rows={"Sheet": 0}
            )
            db.close()
            manifest = AgentManifest(
                id="general-agent",
                name="test",
                version="1",
                tools=["query_dataset"],
                toolsets=["project-data"],
            )
            registry = _Registry(AgentDefinition(manifest, "Use tools.", root))
            model = ScriptedModel(
                [
                    ModelStep(
                        output=[
                            function_call(
                                "query_dataset", {"file": path.name}, call_id="q"
                            )
                        ]
                    ),
                    ModelStep(output=[assistant_message("done")]),
                ]
            )
            service = RunService(
                db_path=database,
                agent_registry=registry,
                model_provider=lambda _: model,
            )
            client = TestClient(create_agent_api(run_service=service, db_path=database))
            response = client.post(
                "/api/agent/runs",
                json={
                    "run_id": "data-run",
                    "prompt": "query",
                    "project_id": "p1",
                    "conversation_id": "c",
                    "model_id": "fake",
                    "work_dir": str(root),
                },
            )
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            output = next(
                e["payload"]["output"] for e in events if e["type"] == "tool.completed"
            )
            self.assertEqual(output["rows"][0]["amount"], 10)

            def factory():
                db = sqlite3.connect(database)
                db.row_factory = sqlite3.Row
                return db

            context = WorkbenchRunContext(
                project_id="p1",
                work_dir=root,
                run_id="r",
                conversation_id="c",
                work_mode="auto",
                agent_id="general-agent",
                database_factory=factory,
            )
            definition = AgentDefinition(
                manifest.model_copy(
                    update={"tools": ["query_dataset", "apply_data_changes"]}
                ),
                "",
                root,
            )
            tools = {
                t.name: t for t in ToolCatalog.default().build(definition, context)
            }
            result = asyncio.run(
                _invoke(tools["query_dataset"], context, {"file": path.name})
            )
            change = {
                "file": path.name,
                "operation": "update",
                "expected_revision": result["revision"],
                "filters": [{"field": "code", "value": "A"}],
                "values": [{"field": "amount", "value": 20}],
            }
            with patch(
                "storage.project_data_store.ProjectDataStore.log_change",
                side_effect=ValueError("audit failed"),
            ):
                failed = asyncio.run(
                    _invoke(tools["apply_data_changes"], context, change)
                )
            self.assertFalse(failed["success"])
            unchanged = asyncio.run(
                _invoke(tools["query_dataset"], context, {"file": path.name})
            )
            self.assertEqual(unchanged["revision"], result["revision"])
            changed = asyncio.run(_invoke(tools["apply_data_changes"], context, change))
            self.assertTrue(changed["success"], changed)
            again = asyncio.run(_invoke(tools["apply_data_changes"], context, change))
            self.assertFalse(again["success"])
            other = context.__class__(**{**context.__dict__, "project_id": "other"})
            isolated = asyncio.run(
                _invoke(tools["query_dataset"], other, {"file": path.name})
            )
            self.assertFalse(isolated["success"])

    def test_tool_uses_injected_database_without_flask(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "test.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                Path("storage/schema.sql").read_text(encoding="utf-8")
            )
            connection.close()

            def factory():
                db = sqlite3.connect(database)
                db.row_factory = sqlite3.Row
                return db

            context = WorkbenchRunContext(
                project_id="p1",
                work_dir=root,
                run_id="r",
                conversation_id="c",
                work_mode="manual",
                agent_id="general-agent",
                database_factory=factory,
            )
            tools = ToolCatalog.default().build(
                AgentRegistry().get("general-agent"), context
            )
            tool = next(item for item in tools if item.name == "list_datasets")
            result = asyncio.run(_invoke(tool, context, {}))
            self.assertTrue(result["success"], result)
            self.assertEqual(result["files"], [])

    def test_connection_scope_rolls_back_and_closes(self):
        from storage.connections import connection_scope, get_connection

        db = sqlite3.connect(":memory:")
        db.execute("create table sample(value text)")
        with self.assertRaises(ValueError):
            with connection_scope(lambda: db):
                get_connection().execute("insert into sample values ('x')")
                raise ValueError("rollback")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("select 1")


if __name__ == "__main__":
    unittest.main()
