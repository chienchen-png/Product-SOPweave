"""Shared bounded ZIP inspection for SOP documents and imported skill bundles."""

from __future__ import annotations
import re
import stat
from pathlib import PurePosixPath
from zipfile import ZipFile
from services.skills.contracts import SkillProblem


def checked_entries(archive: ZipFile, *, max_files=256, max_bytes=50 * 1024 * 1024):
    entries = archive.infolist()
    if len(entries) > max_files or sum(e.file_size for e in entries) > max_bytes:
        raise SkillProblem("压缩包展开大小或文件数量超过限制")
    seen = set()
    for entry in entries:
        name = entry.filename.replace("\\", "/").rstrip("/")
        path = PurePosixPath(name)
        parts = path.parts
        if (
            not parts
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or ".." in parts
            or ":" in name
            or any(p.endswith((" ", ".")) for p in parts)
        ):
            raise SkillProblem("压缩包包含无效或越界路径")
        if any(
            re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", p, re.I)
            for p in parts
        ):
            raise SkillProblem("压缩包包含 Windows 保留名称")
        normalized = name.casefold()
        if normalized in seen or stat.S_ISLNK(entry.external_attr >> 16):
            raise SkillProblem("压缩包存在重名或符号链接")
        seen.add(normalized)
        if entry.file_size > max(1, entry.compress_size) * 100:
            raise SkillProblem("压缩包压缩比超过限制")
    return entries
