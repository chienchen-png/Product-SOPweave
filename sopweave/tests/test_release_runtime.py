import unittest
import tempfile
from pathlib import Path
import subprocess
import os
import sys


class ReleaseRuntimeTests(unittest.TestCase):
    def test_startup_probe_bounds_native_threads_and_reports_process_crashes(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from scripts.check_runtime import main

        for exit_code in (0, -1073741819, 1):
            with self.subTest(exit_code=exit_code), patch(
                "sys.argv", ["check_runtime.py"]
            ), patch("scripts.check_runtime.dependency_errors", return_value=[]), patch(
                "scripts.check_runtime.subprocess.run",
                return_value=SimpleNamespace(returncode=exit_code),
            ) as run:
                self.assertEqual(main(), 0 if exit_code == 0 else 1)
                command = run.call_args.args[0]
                self.assertIn("import asgi", command[-1])
                self.assertIn("import pandas", command[-1])
                self.assertEqual(
                    run.call_args.kwargs["env"]["OPENBLAS_NUM_THREADS"], "1"
                )
                self.assertEqual(run.call_args.kwargs["env"]["OMP_NUM_THREADS"], "1")

    def test_markdown_image_resource_handles_spaces_and_query_symbols_with_workspace_boundary(
        self,
    ):
        import sqlite3
        from contextlib import closing
        from unittest.mock import patch
        from flask import Flask
        from routes.files import files_bp
        from storage.project_store import ProjectStore
        from urllib.parse import quote

        with tempfile.TemporaryDirectory() as directory, closing(
            sqlite3.connect(":memory:")
        ) as db:
            db.row_factory = sqlite3.Row
            db.executescript(Path("storage/schema.sql").read_text(encoding="utf-8"))
            root = Path(directory)
            project = ProjectStore(db).create("images", str(root))
            (root / "图 a&b.svg").write_text("<svg/>", encoding="utf-8")
            (root / "private.txt").write_text("private", encoding="utf-8")
            app = Flask(__name__)
            app.register_blueprint(files_bp)
            with patch("routes.files.get_db", return_value=db):
                prefix = f"/api/files/resource/{project['id']}/"
                with app.test_client().get(prefix + quote("图 a&b.svg")) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data, b"<svg/>")
                self.assertEqual(
                    app.test_client().get(prefix + "private.txt").status_code, 415
                )
                self.assertEqual(
                    app.test_client().get(prefix + "..%2Foutside.svg").status_code, 400
                )

    def test_isolated_data_directory_also_contains_hidden_app_workspaces(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    'from config.paths import managed_work_dir,resolve_work_dir;print(resolve_work_dir(managed_work_dir("__app_finmod__")))',
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={
                    **os.environ,
                    "SOPWEAVE_DATA_DIR": directory,
                    "PYTHONIOENCODING": "utf-8",
                },
                check=True,
            )
            self.assertEqual(
                Path(result.stdout.strip()), (Path(directory) / "app-workspaces/finmod").resolve()
            )

    def test_runtime_reports_missing_and_wrong_versions_without_silently_accepting_them(
        self,
    ):
        from importlib.metadata import PackageNotFoundError
        from scripts.check_runtime import dependency_errors

        def installed(name):
            if name == "missing":
                raise PackageNotFoundError(name)
            return "1.0"

        errors = dependency_errors(
            "correct==1.0 \\\n    --hash=sha256:abc\nwrong==2.0\nmissing==3.0\n",
            installed,
        )
        self.assertEqual(len(errors), 2)
        self.assertIn("wrong", errors[0])
        self.assertIn("missing", errors[1])
