from __future__ import annotations

import io
import sqlite3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from routes.files import files_bp
from storage.project_store import ProjectStore


class FileOperationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(Path("storage/schema.sql").read_text(encoding="utf-8"))
        self.project = ProjectStore(self.db).create("file operations", str(self.root))
        patcher = patch("routes.files.get_db", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(files_bp)
        self.client = app.test_client()
        (self.root / "source").mkdir()
        (self.root / "source/data.txt").write_bytes(b"original\x00bytes")

    def body(self, **values):
        return {"project_id": self.project["id"], **values}

    def test_invalid_portable_destination_is_rejected_before_any_disk_mutation(self):
        invalid = ("bad?.txt", "CON.txt", "nul", "COM1.xlsx", "aux/data.txt",
                   "folder./data.txt", "bad|name.txt", "bad\x00name.txt")
        for endpoint in ("create", "rename", "copy", "upload", "write"):
            for name in invalid:
                with self.subTest(endpoint=endpoint, name=name), \
                     patch("routes.files.atomic_bytes") as write, \
                     patch("pathlib.Path.mkdir") as mkdir, \
                     patch("pathlib.Path.rename") as rename, \
                     patch("werkzeug.datastructures.FileStorage.save") as upload:
                    if endpoint == "upload":
                        result = self.client.post("/api/files/upload", data={
                            **self.body(), "relative_path": name,
                            "file": (io.BytesIO(b"new"), "upload.txt"),
                        })
                    else:
                        values = {
                            "create": {"path": name},
                            "rename": {"path": "source/data.txt", "new_path": name},
                            "copy": {"path": "source/data.txt", "target_path": name},
                            "write": {"path": name, "expected_version": "missing", "content": "new"},
                        }[endpoint]
                        method = self.client.put if endpoint in {"rename", "write"} else self.client.post
                        result = method(f"/api/files/{endpoint}", json=self.body(**values))
                    self.assertEqual(result.status_code, 400, result.get_json())
                    self.assertIn("名称", result.get_json()["message"])
                    for operation in (write, mkdir, rename, upload):
                        operation.assert_not_called()
        self.assertEqual((self.root / "source/data.txt").read_bytes(), b"original\x00bytes")
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["source"])

    def test_copy_and_move_reject_own_descendant_before_creating_directories(self):
        for endpoint, target_field in (("copy", "target_path"), ("rename", "new_path")):
            with self.subTest(endpoint=endpoint), patch(
                "routes.files.shutil.copytree"
            ) as copy:
                method = self.client.post if endpoint == "copy" else self.client.put
                result = method(
                    f"/api/files/{endpoint}",
                    json=self.body(path="source", **{target_field: "source/child/new"}),
                )
                self.assertEqual(result.status_code, 400, result.get_json())
                copy.assert_not_called()
                self.assertFalse((self.root / "source/child").exists())
                self.assertEqual(
                    (self.root / "source/data.txt").read_bytes(), b"original\x00bytes"
                )

    def test_failed_copy_removes_partial_destination_and_keeps_source(self):
        def fail_copy(source, destination, **kwargs):
            destination.mkdir()
            (destination / "partial.txt").write_bytes(b"partial")
            raise OSError("injected disk full")

        with patch("routes.files.shutil.copytree", side_effect=fail_copy):
            result = self.client.post(
                "/api/files/copy",
                json=self.body(path="source", target_path="destination"),
            )
        self.assertEqual(result.status_code, 500)
        self.assertFalse((self.root / "destination").exists())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["source"])

    def test_failed_upload_has_no_partial_or_overwritten_file(self):
        def fail_save(upload, destination, *args, **kwargs):
            Path(destination).write_bytes(b"partial")
            raise OSError("injected disk full")

        with patch("werkzeug.datastructures.FileStorage.save", new=fail_save):
            result = self.client.post(
                "/api/files/upload",
                data={**self.body(), "file": (io.BytesIO(b"complete"), "upload.txt")},
            )
        self.assertEqual(result.status_code, 500)
        self.assertFalse((self.root / "upload.txt").exists())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["source"])

    def test_upload_rejects_traversal_and_alternate_data_stream_names(self):
        for path in (
            "../escaped.txt",
            "/escaped.txt",
            "C:/escaped.txt",
            "file.txt:secret",
        ):
            with self.subTest(path=path):
                result = self.client.post(
                    "/api/files/upload",
                    data={
                        **self.body(),
                        "relative_path": path,
                        "file": (io.BytesIO(b"data"), "upload.txt"),
                    },
                )
                self.assertEqual(result.status_code, 400, result.get_json())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["source"])

    def test_copy_then_move_preserves_bytes_and_rejects_overwrite(self):
        copy = self.client.post(
            "/api/files/copy", json=self.body(path="source", target_path="copied")
        )
        self.assertEqual(copy.status_code, 201, copy.get_json())
        move = self.client.put(
            "/api/files/rename", json=self.body(path="copied", new_path="nested/moved")
        )
        self.assertEqual(move.status_code, 200, move.get_json())
        self.assertFalse((self.root / "copied").exists())
        self.assertEqual(
            (self.root / "nested/moved/data.txt").read_bytes(), b"original\x00bytes"
        )
        conflict = self.client.put(
            "/api/files/rename", json=self.body(path="source", new_path="nested/moved")
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertTrue((self.root / "source/data.txt").exists())

    def test_create_in_a_removed_parent_does_not_recreate_the_directory(self):
        for kind in ("file", "directory"):
            with self.subTest(kind=kind):
                result = self.client.post(
                    "/api/files/create",
                    json=self.body(path=f"removed-{kind}/new", type=kind),
                )
                self.assertEqual(result.status_code, 404, result.get_json())
                self.assertFalse((self.root / f"removed-{kind}").exists())

    def make_directory_link(self, link, target):
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
            )
            if result.returncode:
                self.skipTest("directory junction creation unavailable")
        else:
            link.symlink_to(target, target_is_directory=True)

    def test_recursive_copy_never_follows_a_nested_directory_link(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (Path(outside.name) / "private.txt").write_bytes(b"outside-workspace")
        self.make_directory_link(self.root / "source/linked", Path(outside.name))
        result = self.client.post(
            "/api/files/copy", json=self.body(path="source", target_path="destination")
        )
        self.assertEqual(result.status_code, 400, result.get_json())
        self.assertFalse((self.root / "destination").exists())

    def test_recursive_tree_skips_links_with_an_explicit_warning(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (Path(outside.name) / "private.txt").write_bytes(b"outside-workspace")
        self.make_directory_link(self.root / "source/linked", Path(outside.name))
        result = self.client.get(
            "/api/files/tree", query_string=self.body(path="source", recursive="1")
        )
        self.assertEqual(result.status_code, 200)
        data = result.get_json()["data"]
        self.assertNotIn("linked", [entry["name"] for entry in data["entries"]])
        self.assertTrue(
            any("source/linked" in warning for warning in data.get("warnings", []))
        )

    def test_file_tree_paginates_large_direct_directories_without_losing_entries(self):
        for index in range(205):
            (self.root / "source" / f"bulk-{index:03d}.txt").touch()
        seen = []
        cursor = ""
        pages = []
        while True:
            query = self.body(path="source", limit=100)
            if cursor:
                query["offset"] = cursor
            result = self.client.get("/api/files/tree", query_string=query)
            self.assertEqual(result.status_code, 200, result.get_json())
            data = result.get_json()["data"]
            pages.append(len(data["entries"]))
            seen.extend(entry["path"] for entry in data["entries"])
            cursor = data.get("next_cursor") or ""
            if not cursor:
                break

        self.assertEqual(pages, [100, 100, 6])
        self.assertEqual(len(seen), 206)
        self.assertEqual(len(set(seen)), 206)
        self.assertIn("source/bulk-204.txt", seen)

    def test_explicit_identical_upload_reuse_never_substitutes_different_bytes(self):
        target = self.root / "same.txt"
        target.write_bytes(b"same")
        for content, expected in ((b"same", 200), (b"new", 409)):
            with self.subTest(content=content):
                result = self.client.post(
                    "/api/files/upload",
                    data={
                        **self.body(),
                        "reuse_if_identical": "1",
                        "file": (io.BytesIO(content), "same.txt"),
                    },
                )
                self.assertEqual(result.status_code, expected, result.get_json())
                if expected == 200:
                    self.assertTrue(result.get_json()["data"]["reused"])
                self.assertEqual(target.read_bytes(), b"same")
                self.assertFalse(
                    any(
                        path.name.startswith(".upload-") for path in self.root.iterdir()
                    )
                )

    def test_external_open_uses_authorized_path_and_surfaces_os_failure(self):
        workbook = self.root / "report.xlsx"
        workbook.write_bytes(b"test workbook")
        with patch("routes.files.os.startfile") as startfile:
            opened = self.client.post(
                "/api/files/open-external",
                json=self.body(path="report.xlsx"),
            )
        self.assertEqual(opened.status_code, 200, opened.get_json())
        self.assertTrue(opened.get_json()["data"]["opened"])
        startfile.assert_called_once_with(str(workbook.resolve()))

        with patch(
            "routes.files.os.startfile",
            side_effect=OSError("no associated application"),
        ):
            failed = self.client.post(
                "/api/files/open-external",
                json=self.body(path="report.xlsx"),
            )
        self.assertEqual(failed.status_code, 500, failed.get_json())
        self.assertIn("外部打开失败", failed.get_json()["message"])
        self.assertNotIn("Traceback", failed.get_json()["message"])

    def test_external_open_rejects_unsupported_and_outside_paths_before_startfile(self):
        (self.root / "unsupported.txt").write_bytes(b"text")
        for path in ("unsupported.txt", "../outside.xlsx", "C:/Windows/win.ini"):
            with self.subTest(path=path), patch("routes.files.os.startfile") as startfile:
                result = self.client.post(
                    "/api/files/open-external", json=self.body(path=path)
                )
                self.assertEqual(result.status_code, 400, result.get_json())
                startfile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
