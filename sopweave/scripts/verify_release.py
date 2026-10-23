"""Extract hash-verified candidates into a Chinese path, install offline and test."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]


def extract_verified(archive_path: Path, target: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        manifests = [name for name in names if name.endswith("/MANIFEST.json") or name == "MANIFEST.json"]
        if len(manifests) != 1 or len(names) != len(set(names)):
            raise ValueError("Expected one manifest and no duplicate members")
        manifest = json.loads(archive.read(manifests[0]))
        if set(manifest) != set(names) - {manifests[0]}:
            raise ValueError("Manifest membership mismatch")
        for name in names:
            if (name.startswith("/") or "\\" in name or ".." in Path(name).parts
                    or PureWindowsPath(name).drive or ":" in name):
                raise ValueError("Unsafe archive member")
            content = archive.read(name)
            if name in manifest:
                expected = manifest[name]
                if len(content) != expected["bytes"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
                    raise ValueError(f"Hash mismatch: {name}")
        archive.extractall(target)


def live_smoke(app: Path, python: Path, environment: dict[str, str], log: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with log.open("wb") as output:
        process = subprocess.Popen([str(python), "-m", "uvicorn", "asgi:app", "--host", "127.0.0.1", "--port", str(port)],
                                   cwd=app, env=environment, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("ASGI process exited before startup")
                try:
                    urllib.request.urlopen(base + "/api/projects", timeout=1).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise TimeoutError("ASGI startup")
            def request(path: str, payload=None):
                data = None if payload is None else json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as response:
                    return json.load(response)
            assert request("/api/projects")["data"] == []
            for kind in ("recon", "finmod"):
                project = request("/api/projects", {"name": f"__app_{kind}__"})["data"]
                assert project["id"]
            assert request("/api/projects")["data"] == []
            assert len(request("/api/projects?include_hidden=1")["data"]) == 2
            assert len(request("/api/models/provider-templates")["data"]) == 4
            with urllib.request.urlopen(base + "/", timeout=15) as response:
                assert b"<html" in response.read().lower()
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, default=ROOT / "data/releases")
    args = parser.parse_args()
    report = json.loads((args.release_dir / "RELEASE.json").read_text(encoding="utf-8"))
    source = next(name for name in report["artifacts"] if name.endswith("-source.zip"))
    wheels = next(name for name in report["artifacts"] if "-wheels-" in name)
    for name, meta in report["artifacts"].items():
        assert hashlib.sha256((args.release_dir / name).read_bytes()).hexdigest() == meta["sha256"]
    results = []
    def run(label: str, command: list[str], cwd: Path, env: dict[str, str]):
        with (args.release_dir / f"verify-{label}.log").open("wb") as output:
            subprocess.run(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=900)
        results.append(label)
        print(f"PASS {label}", flush=True)
    with tempfile.TemporaryDirectory(prefix="中文 空格 验收 ") as directory:
        base = Path(directory)
        extract_verified(args.release_dir / source, base)
        app = next(base.glob("sopweave-*")) / "sopweave"
        extract_verified(args.release_dir / wheels, app)
        env = {**os.environ, "SOPWEAVE_DATA_DIR": str(app / "data"), "PYTHONIOENCODING": "utf-8"}
        python = app / ".runtime/Scripts/python.exe"
        run("venv", [sys.executable, "-m", "venv", str(app / ".runtime")], app, env)
        run("offline-install", [str(python), "-m", "pip", "install", "--no-index", "--find-links", "offline_packages", "--require-hashes", "-r", "requirements.lock"], app, env)
        run("pip-check", [str(python), "-m", "pip", "check"], app, env)
        # Simulate the user's separately prepared Office dependency, without
        # redistributing its executable in our source/wheel artifacts.
        pandoc = ROOT / "offline_packages/pandoc/pandoc.exe"
        if not pandoc.is_file():
            raise RuntimeError("Prepare Pandoc before the full Office acceptance gate")
        destination = app / "offline_packages/pandoc"
        destination.mkdir(exist_ok=True)
        for name in ("pandoc.exe", "reference-template.docx", "COPYING.rtf", "COPYRIGHT.txt"):
            path = pandoc.parent / name
            if path.exists():
                shutil.copy2(path, destination / name)
        run("runtime", [str(python), "scripts/check_runtime.py"], app, env)
        run("backend", [str(python), "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"], app, env)
        run("sample", [str(python), "scripts/create_demo.py", "examples"], app, env)
        live_smoke(app, python, env, args.release_dir / "verify-http.log")
        results.append("live-http-hidden-apps")
        print("PASS live-http-hidden-apps", flush=True)
    (args.release_dir / "VERIFICATION.json").write_text(json.dumps({"passed": True, "checks": results,
        "environment": "Current Windows x64 host, fresh virtualenv, Chinese/space extraction path",
        "source_sha256": report["artifacts"][source]["sha256"], "remote_ci": "not_run"}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
