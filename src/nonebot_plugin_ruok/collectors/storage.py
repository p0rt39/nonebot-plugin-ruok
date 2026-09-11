"""Small process-local helpers for safe file-backed persistence."""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from contextlib import contextmanager
from collections.abc import Iterator

_FILE_LOCKS: dict[Path, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def file_lock(path: Path) -> threading.RLock:
    """Return the process-wide lock associated with *path*."""
    key = path.resolve()
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _FILE_LOCKS[key] = lock
        return lock


@contextmanager
def locked_file(path: Path) -> Iterator[None]:
    """Hold the process-local lock for *path* while performing a read/modify/write."""
    with file_lock(path):
        yield


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write content through a same-directory temporary file and replace atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
