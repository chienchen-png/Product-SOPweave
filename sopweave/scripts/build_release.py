"""Build a local source candidate and hash-locked Windows wheel bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.dependency_lock import parse_dependency_lock
from config.brand import APP_SLUG
MODULES = ("agent_runtime", "agent_profiles", "config", "launcher", "routes", "services", "storage", "tools", "skills", "tests", "static")
EXCLUDED = {"__pycache__", "node_modules", ".git", ".runtime", ".venv", ".test-build", ".ruff_cache"}
BLOCKED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pfx", ".pyc", ".log"}


def eligible(path: Path, base: Path) -> bool:
    relative = path.relative_to(base)
    return (path.is_file() and not path.is_symlink()
            and not any(part in EXCLUDED for part in relative.parts)
            and path.suffix.lower() not in BLOCKED_SUFFIXES
            and not path.name.lower().startswith((".env", "apikey", "api-key")))


def source_files(root: Path = ROOT) -> list[tuple[Path, str]]:
    files: dict[str, Path] = {}

    def add(path: Path, base: Path, prefix: str = "") -> None:
        if eligible(path, base):
            files[prefix + path.relative_to(base).as_posix()] = path

    def add_app(path: Path) -> None:
        add(path, root, f"{APP_SLUG}/")

    for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "LICENSE", ".gitignore", ".gitattributes"):
        add(root.parent / name, root.parent)
    for path in (root.parent / ".github/workflows").glob("*.yml"):
        add(path, root.parent)
    for name in ("app.py", "asgi.py", "SOPweave.exe", "SOPweave.bat", "README.md", ".gitignore", "requirements.txt", "requirements.lock", "requirements-agent.in", "requirements-agent.lock"):
        add_app(root / name)
    for directory in MODULES:
        for path in (root / directory).rglob("*"):
            add_app(path)
    for name in ("人工样例.xlsx", "人工样例.xls", "人工样例.docx", "报告.md"):
        add_app(root / "examples" / name)
    for path in (root / "scripts").iterdir():
        if not path.name.startswith("_") and path.suffix in {".py", ".ps1", ".bat"}:
            add_app(path)
    for directory in ("frontend/src", "frontend/tests", "frontend/public", "frontend/dist", "docs"):
        for path in (root / directory).rglob("*"):
            parts = path.relative_to(root / directory).parts
            if directory == "docs" and parts and parts[0] in {"archive", "promotion", "superpowers"}:
                continue
            add_app(path)
    for path in (root / "frontend").iterdir():
        if path.suffix in {".json", ".ts", ".html", ".md"}:
            add_app(path)
    return [(path, name) for name, path in sorted(files.items())]


def locked_wheels(root: Path = ROOT) -> list[Path]:
    text = (root / "requirements.lock").read_text(encoding="utf-8")
    result = []
    normalize = lambda value: re.sub(r"[-_.]+", "-", value).lower()
    for dependency in parse_dependency_lock(text):
        name, version = dependency.name, dependency.version
        allowed_hashes = dependency.hashes
        candidates = [p for p in (root / "offline_packages").glob("*.whl")
                      if normalize(p.name.split("-")[0]) == normalize(name)
                      and p.name.split("-")[1] == version]
        verified = [p for p in candidates if hashlib.sha256(p.read_bytes()).hexdigest() in allowed_hashes]
        if not verified:
            raise ValueError(f"No hash-verified wheel for {name}=={version}")
        result.extend(verified)
    if not result:
        raise ValueError("The complete dependency lock is empty")
    return sorted(set(result))


def write_archive(output: Path, files: list[tuple[Path, str]], *, prefix: str = "", secrets: tuple[bytes, ...] = ()) -> dict:
    manifest = {}
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, name in files:
            if (name.startswith("/") or ".." in Path(name).parts or "\\" in name
                    or PureWindowsPath(name).drive or ":" in name):
                raise ValueError(f"Unsafe archive member: {name}")
            content = path.read_bytes()
            if any(secret in content for secret in secrets if secret):
                raise ValueError(f"A configured credential was found in: {name}")
            member = prefix + name
            archive.writestr(member, content)
            manifest[member] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        archive.writestr(prefix + "MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.0.0-rc.2")
    parser.add_argument("--output", type=Path, default=ROOT / "data/releases")
    parser.add_argument("--key-file", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.version):
        parser.error("version must be a filename-safe identifier")
    args.output.mkdir(parents=True, exist_ok=True)
    files = source_files()
    required = (ROOT / "frontend/dist/index.html", ROOT / "docs/THIRD_PARTY_NOTICES.md", ROOT / "SOPweave.exe")
    if any(not path.is_file() for path in required):
        raise ValueError("Build the frontend and native launcher and generate third-party notices first")
    secrets: tuple[bytes, ...] = ()
    if args.key_file:
        from check_tool_rebuild_providers import read_credentials
        secrets = tuple(block["api_key"].encode() for block in read_credentials(args.key_file) if block.get("api_key"))
        if not secrets:
            raise ValueError("The credential exclusion check could not read any keys")
    source = args.output / f"sopweave-{args.version}-source.zip"
    source_manifest = write_archive(source, files, prefix=f"sopweave-{args.version}/", secrets=secrets)
    wheels = [(p, f"offline_packages/{p.name}") for p in locked_wheels()]
    wheels += [(ROOT / "requirements.lock", "requirements.lock"),
               (ROOT / "docs/OFFLINE.md", "OFFLINE.md")]
    bundle = args.output / f"sopweave-{args.version}-wheels-win-py314.zip"
    wheel_manifest = write_archive(bundle, wheels, secrets=secrets)
    # Include new source files from this working tree; do not rely on git archive.
    listed = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                            cwd=ROOT.parent, check=True, capture_output=True).stdout.decode("utf-8").split("\0")
    included = {name for _, name in files}
    untracked = sorted(name for name in listed if name in included)
    report = {"version": args.version, "status": "local_candidate",
              "license_present": (ROOT.parent / "LICENSE").is_file(),
              "remote_ci": "not_run", "source_files": len(source_manifest),
              "wheel_files": len(wheel_manifest), "included_untracked_sources": untracked,
              "artifacts": {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                            for p in (source, bundle)}}
    (args.output / "RELEASE.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "SHA256SUMS.txt").write_text("".join(f"{meta['sha256']}  {name}\n" for name, meta in report["artifacts"].items()), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "included_untracked_sources"}, ensure_ascii=False))
    print(f"Included {len(untracked)} not-yet-tracked source files; review RELEASE.json before committing.")


if __name__ == "__main__":
    main()
