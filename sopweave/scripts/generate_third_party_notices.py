"""Collect redistribution notices from the exact locked wheels and npm packages."""
from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.dependency_lock import parse_dependency_lock


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> None:
    output = ROOT / "docs/third-party"
    output.mkdir(parents=True, exist_ok=True)
    locked = {pin.name: pin.version for pin in parse_dependency_lock(
        (ROOT / "requirements.lock").read_text(encoding="utf-8"))}
    rows = []
    for name, version in sorted(locked.items()):
        wheels = [p for p in (ROOT / "offline_packages").glob("*.whl")
                  if normalized(p.name.split("-")[0]) == normalized(name)
                  and p.name.split("-")[1] == version]
        if not wheels:
            raise RuntimeError(f"Missing locked wheel: {name}=={version}")
        dest = output / "python" / f"{name}-{version}"
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(wheels[0]) as wheel:
            meta_name = next(n for n in wheel.namelist() if n.endswith(".dist-info/METADATA"))
            metadata_bytes = wheel.read(meta_name)
            (dest / "METADATA.txt").write_bytes(metadata_bytes)
            metadata = BytesParser().parsebytes(metadata_bytes)
            for entry in wheel.namelist():
                base = Path(entry).name
                if not entry.endswith("/") and (
                    ".dist-info/licenses/" in entry
                    or base.upper().startswith(("LICENSE", "COPYING", "NOTICE", "COPYRIGHT"))
                ):
                    # Flatten path separators, keeping each upstream file distinct.
                    (dest / entry.replace("/", "__")).write_bytes(wheel.read(entry))
            license_name = metadata.get("License-Expression") or metadata.get("License") or "See metadata"
            license_name = str(license_name).splitlines()[0][:160].replace("|", "/")
        rows.append(f"| {name} {version} | {license_name} | [原文](third-party/python/{dest.name}/METADATA.txt) |")

    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    npm_count = 0
    for location, package in sorted(lock["packages"].items()):
        if not location or not package.get("version"):
            continue
        source = ROOT / "frontend" / location
        if not source.is_dir():
            if package.get("optional"):
                continue
            raise RuntimeError(f"Run npm ci before collecting notices: {location}")
        ident = location.replace("node_modules/", "").replace("/", "__")
        dest = output / "npm" / f"{ident}-{package['version']}"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "package.json", dest / "package.json")
        for candidate in source.iterdir():
            if candidate.is_file() and candidate.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "COPYRIGHT")):
                shutil.copy2(candidate, dest / candidate.name)
        npm_count += 1
    vendor = ROOT / "frontend/public/vditor"
    for candidate in vendor.rglob("*"):
        if candidate.is_file() and candidate.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "COPYRIGHT")):
            dest = output / "vendor" / candidate.relative_to(vendor)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, dest)
    pandoc = output / "pandoc"
    pandoc.mkdir(exist_ok=True)
    for name in ("COPYING.rtf", "COPYRIGHT.txt"):
        shutil.copy2(ROOT / "offline_packages/pandoc" / name, pandoc / name)
    (ROOT / "docs/THIRD_PARTY_NOTICES.md").write_text(
        "# 第三方组件与许可\n\n"
        "Python 依赖按完整锁收集，npm 依赖按已安装的 package-lock 收集；原始许可、NOTICE 和包元数据保留在 `third-party`。\n\n"
        "Pandoc 作为独立进程调用，由用户从官方发行版单独安装；其 GPL 与第三方版权原文见 [Pandoc notices](third-party/pandoc/COPYRIGHT.txt)。上游源码获取方式及 GPL 条款见该文件与 COPYING.rtf。项目自身授权不替代第三方条款。\n\n"
        f"已收集 {len(rows)} 个 Python 包、{npm_count} 个已安装 npm 包及 Vditor 静态资源自带 notices。npm 各包版本及声明见 `third-party/npm` 的 package.json。\n\n"
        "| Python 包 | 声明 | 元数据 |\n| --- | --- | --- |\n"
        + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"Collected {len(rows)} Python and {npm_count} npm package notices")


if __name__ == "__main__":
    main()
