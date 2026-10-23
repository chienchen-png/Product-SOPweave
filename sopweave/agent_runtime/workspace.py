"""Canonical workspace paths and optimistic, atomic file writes."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from storage.operations import check_cancelled

_locks_guard = threading.Lock()
_locks: dict[str, tuple[threading.RLock, int]] = {}


@contextmanager
def path_lock(path: Path):
    key = os.path.normcase(str(path.resolve()))
    with _locks_guard:
        lock, count = _locks.get(key, (threading.RLock(), 0))
        _locks[key] = (lock, count + 1)
    try:
        with lock:
            yield
    finally:
        with _locks_guard:
            _, count = _locks[key]
            if count == 1:
                del _locks[key]
            else:
                _locks[key] = (lock, count - 1)


def resolve_path(root: Path, value: str) -> Path:
    root = Path(root).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root) or ":" in str(path.relative_to(root)):
        raise ValueError("路径超出授权工作目录")
    return path


def file_version(path: Path) -> str:
    if not path.exists():
        return "missing"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text_snapshot(path: Path, encoding: str = "utf-8") -> tuple[str, str]:
    """Version the bytes actually shown in the editor, even during external writes."""
    with path_lock(path):
        raw = path.read_bytes()
    return raw.decode(encoding), hashlib.sha256(raw).hexdigest()


class FileVersionConflict(ValueError):
    """The content no longer matches the snapshot being edited."""


def check_version(path: Path, expected: str) -> None:
    if file_version(path) != expected:
        raise FileVersionConflict("文件版本冲突，请重新读取后编辑")


def atomic_bytes(path: Path, content: bytes, expected: str) -> str:
    with path_lock(path):
        return _atomic_bytes(path, content, expected)


def _atomic_bytes(path: Path, content: bytes, expected: str) -> str:
    from tools.file_lock import ensure_file_writable

    if path.exists():
        error = ensure_file_writable(str(path))
        if error:
            raise ValueError(str(error))
    check_version(path, expected)
    check_cancelled()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".agent-", suffix=path.suffix, dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        check_version(path, expected)
        check_cancelled()
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return file_version(path)
