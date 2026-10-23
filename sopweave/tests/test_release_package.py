import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.build_release import source_files, write_archive, locked_wheels
from scripts.verify_release import extract_verified


class ReleasePackageTests(unittest.TestCase):
    def test_extra_dependencies_are_checked_and_packaged(self):
        from config.dependency_lock import parse_dependency_lock
        from scripts.check_runtime import dependency_errors
        dependency = parse_dependency_lock('pyjwt[crypto]==2.15.1 \\\n    --hash=sha256:' + 'a' * 64)[0]
        self.assertEqual(dependency.name, 'pyjwt')
        self.assertEqual(dependency.hashes, frozenset({'a' * 64}))
        self.assertTrue(dependency_errors('pyjwt[crypto]==2.15.1', lambda name: '1.0'))
        if (Path(__file__).resolve().parents[1] / "offline_packages").is_dir():
            self.assertTrue(any(path.name.lower().startswith('pyjwt-') for path in locked_wheels()))
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            parse_dependency_lock('unlocked>=1')

    def test_source_selection_includes_new_code_but_excludes_data_and_secrets(self):
        names = {name for _, name in source_files()}
        self.assertIn("sopweave/services/finmod_compute.py", names)
        self.assertIn("sopweave/launcher/Launcher.cs", names)
        self.assertIn("sopweave/launcher/Program.cs", names)
        self.assertIn("sopweave/launcher/WindowsJob.cs", names)
        self.assertIn("sopweave/launcher/app.manifest", names)
        self.assertIn("sopweave/scripts/build_launcher.ps1", names)
        self.assertIn("sopweave/scripts/serve.py", names)
        if (Path(__file__).resolve().parents[1] / 'SOPweave.exe').is_file():
            self.assertIn("sopweave/SOPweave.exe", names)
        self.assertTrue("sopweave/agent_profiles/general-agent/instructions.md" in names)
        self.assertTrue("sopweave/agent_profiles/general-agent/agent.yaml" in names)
        self.assertTrue("sopweave/static/img/favicon.ico" in names)
        self.assertIn("LICENSE", names)
        self.assertIn("sopweave/docs/ARCHITECTURE.md", names)
        self.assertIn("sopweave/docs/AGENT_RUNTIME.md", names)
        self.assertIn("sopweave/docs/USER_GUIDE.md", names)
        self.assertFalse(
            any(
                "/docs/superpowers/" in name
                or "/docs/audits/" in name
                or "docs/RELEASE_NOTES.md" in name
                for name in names
            )
        )
        self.assertFalse(any("/data/" in name or "node_modules" in name or "apikey" in name.lower() for name in names))

    def test_known_credential_and_tampered_member_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "content.txt"
            payload.write_bytes(b"private-test-token")
            with self.assertRaisesRegex(ValueError, "credential"):
                write_archive(root / "secret.zip", [(payload, "file.txt")], secrets=(b"private-test-token",))
            payload.write_bytes(b"safe")
            write_archive(root / "safe.zip", [(payload, "file.txt")])
            extract_verified(root / "safe.zip", root / "good")
            self.assertEqual((root / "good/file.txt").read_bytes(), b"safe")
            with zipfile.ZipFile(root / "bad.zip", "w") as archive:
                archive.writestr("file.txt", b"changed")
                archive.writestr("MANIFEST.json", json.dumps({"file.txt": {"bytes": 4, "sha256": hashlib.sha256(b"safe").hexdigest()}}))
            with self.assertRaisesRegex(ValueError, "Hash mismatch"):
                extract_verified(root / "bad.zip", root / "bad")

    def test_path_escape_and_windows_drive_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("../outside", "C:/outside", "file.txt:stream"):
                with self.subTest(name=name):
                    with zipfile.ZipFile(root / "bad.zip", "w") as archive:
                        archive.writestr(name, b"safe")
                        archive.writestr("MANIFEST.json", json.dumps({name: {"bytes": 4, "sha256": hashlib.sha256(b"safe").hexdigest()}}))
                    with self.assertRaisesRegex(ValueError, "Unsafe"):
                        extract_verified(root / "bad.zip", root / "bad")
