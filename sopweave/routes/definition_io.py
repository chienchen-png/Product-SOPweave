from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from flask import current_app

from config.settings import BASE_DIR, DATA_DIR


def replace_definition_files(files: Mapping[Path, str]) -> None:
    temp_paths: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    try:
        for target, content in files.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent,
            )
            temp_path = Path(temp_name)
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temp_paths[target] = temp_path
            backup = target.with_suffix(target.suffix + ".bak")
            if target.exists():
                shutil.copy2(target, backup)
                backups[target] = backup
        replaced: list[Path] = []
        try:
            for target, temp_path in temp_paths.items():
                os.replace(temp_path, target)
                replaced.append(target)
        except OSError:
            for target in reversed(replaced):
                backup = backups.get(target)
                if backup and backup.exists():
                    shutil.copy2(backup, target)
            raise
    finally:
        for temp_path in temp_paths.values():
            temp_path.unlink(missing_ok=True)


def record_definition_audit(kind: str, definition_id: str, files: list[Path]) -> None:
    configured = current_app.config.get("DEFINITION_AUDIT_PATH")
    audit_path = Path(configured) if configured else DATA_DIR / "definition-audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "definition.save",
        "kind": kind,
        "definition_id": definition_id,
        "files": [_portable_audit_path(path) for path in files],
    }
    with audit_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _portable_audit_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(resolved)
