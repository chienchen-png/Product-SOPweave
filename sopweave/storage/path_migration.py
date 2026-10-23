"""One-time-compatible migration of persisted paths to portable values."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from config.paths import portable_work_dir, resolve_work_dir


_MANAGED_SOURCE_MARKERS = (
    "/data/app-workspaces/recon/",
    "/data/app-workspaces/finmod/",
)


def _relative_inside(path: str, root: Path) -> str | None:
    """Return ``path`` relative to ``root`` using Windows-safe comparison."""
    if not path or not os.path.isabs(path):
        return None
    child_path = os.path.normpath(path)
    parent_path = os.path.normpath(str(root))
    child = os.path.normcase(child_path)
    parent = os.path.normcase(parent_path)
    try:
        if os.path.commonpath((child, parent)) != parent:
            return None
    except ValueError:
        return None
    relative = os.path.relpath(child_path, parent_path)
    return Path(relative).as_posix()


def _relative_managed_source(path: str) -> str | None:
    """Recover a relative filename from an old managed-workspace path."""
    normalized = str(path or "").replace("\\", "/")
    lowered = normalized.casefold()
    for marker in _MANAGED_SOURCE_MARKERS:
        index = lowered.find(marker)
        if index >= 0:
            return normalized[index + len(marker):].lstrip("/") or None
    return None


def _backup_database(db: sqlite3.Connection, backup_path: Path) -> None:
    """Create a consistent pre-migration snapshot once."""
    if backup_path.exists():
        return
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(backup_path)) as backup:
        db.backup(backup)


def migrate_portable_paths(
    db: sqlite3.Connection,
    *,
    backup_path: Path | None = None,
) -> int:
    """Normalize internal project paths and return the number of changed rows.

    The migration is idempotent.  It changes operational path columns only;
    historical log and message text remains an immutable audit record.
    """
    projects = db.execute("SELECT id, name, work_dir FROM projects").fetchall()
    project_updates: list[tuple[str, str]] = []
    source_updates: list[tuple[str, str]] = []
    backup_updates: list[tuple[str, int]] = []

    has_writeback = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'writeback_history'"
    ).fetchone()

    migrated_file_ids: set[str] = set()
    for project in projects:
        project_id = project["id"]
        project_name = project["name"] or ""
        old_work_dir = project["work_dir"] or ""
        new_work_dir = portable_work_dir(project_name, old_work_dir)
        if new_work_dir != old_work_dir:
            project_updates.append((new_work_dir, project_id))

        old_root = resolve_work_dir(old_work_dir)
        files = db.execute(
            "SELECT id, source_path FROM project_data_files WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        for file_row in files:
            relative = _relative_inside(file_row["source_path"] or "", old_root)
            if relative is not None:
                source_updates.append((relative, file_row["id"]))
                migrated_file_ids.add(file_row["id"])

            if has_writeback:
                histories = db.execute(
                    "SELECT id, backup_path FROM writeback_history WHERE file_id = ?",
                    (file_row["id"],),
                ).fetchall()
                for history in histories:
                    backup_relative = _relative_inside(history["backup_path"] or "", old_root)
                    if backup_relative is not None:
                        backup_updates.append((backup_relative, history["id"]))

    # Old versions could leave imported-file rows after deleting a hidden
    # project.  They are not operational, but normalizing them prevents stale
    # machine paths from being carried into copied databases or diagnostics.
    all_sources = db.execute("SELECT id, source_path FROM project_data_files").fetchall()
    for file_row in all_sources:
        if file_row["id"] in migrated_file_ids:
            continue
        relative = _relative_managed_source(file_row["source_path"] or "")
        if relative is not None:
            source_updates.append((relative, file_row["id"]))

    changed = len(project_updates) + len(source_updates) + len(backup_updates)
    if not changed:
        return 0

    if backup_path is not None:
        _backup_database(db, backup_path)
    db.executemany("UPDATE projects SET work_dir = ? WHERE id = ?", project_updates)
    db.executemany("UPDATE project_data_files SET source_path = ? WHERE id = ?", source_updates)
    if backup_updates:
        db.executemany("UPDATE writeback_history SET backup_path = ? WHERE id = ?", backup_updates)
    return changed
