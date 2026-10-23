"""Portable path rules for projects and built-in application workspaces.

Paths stored inside the repository are persisted relative to ``BASE_DIR``.
Absolute paths are reserved for directories explicitly chosen outside the
application folder.  Callers receive resolved absolute paths at runtime so
file operations never depend on the process working directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from config.settings import BASE_DIR, DATA_DIR


APP_WORK_DIRS: dict[str, str] = {
    f"__app_{kind}__": ((DATA_DIR / "app-workspaces" / kind).relative_to(BASE_DIR).as_posix()
                         if DATA_DIR.is_relative_to(BASE_DIR)
                         else str(DATA_DIR / "app-workspaces" / kind))
    for kind in ("recon", "finmod")
}


def _relative_to_base(path: Path) -> str | None:
    """Return a normalized repository-relative path when possible."""
    try:
        return path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return None


def portable_work_dir(project_name: str, work_dir: str | os.PathLike[str] = "") -> str:
    """Return the canonical value to persist in ``projects.work_dir``.

    Built-in applications always use their managed workspace.  Other paths
    inside the application folder are stored relatively; user-selected paths
    outside it remain absolute.
    """
    managed = APP_WORK_DIRS.get(str(project_name or "").strip())
    if managed:
        return managed

    raw = os.fspath(work_dir).strip()
    if not raw:
        raise ValueError("工作目录不能为空")

    candidate = Path(raw).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (BASE_DIR / candidate).resolve()
    return _relative_to_base(resolved) or str(resolved)


def resolve_work_dir(work_dir: str | os.PathLike[str]) -> Path:
    """Resolve a stored project path without relying on ``cwd``."""
    raw = os.fspath(work_dir).strip()
    if not raw:
        raise ValueError("工作目录不能为空")
    candidate = Path(raw).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (BASE_DIR / candidate).resolve()


def managed_work_dir(project_name: str) -> str | None:
    """Return a built-in application's portable workspace, if applicable."""
    return APP_WORK_DIRS.get(str(project_name or "").strip())
