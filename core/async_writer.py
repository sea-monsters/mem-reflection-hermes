"""Async file writer utilities for mem-reflection-hermes.

Provides background-thread file flushing with generation tracking so
later writes can supersede stale pending writes.
"""
from __future__ import annotations

import logging
import os
import queue
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Set, Tuple

logger = logging.getLogger(__name__)

_write_queue: "queue.Queue[Tuple[Path, str, int] | None]" = queue.Queue(maxsize=500)
_pending_writes: Set[Path] = set()
_write_guard_lock = threading.Lock()
_write_path_locks: Dict[str, threading.RLock] = {}
_write_generations: Dict[str, int] = {}


def _write_path_key(path: Path) -> str:
    return str(path.resolve(strict=False))


def _write_path_lock(path: Path) -> threading.RLock:
    key = _write_path_key(path)
    with _write_guard_lock:
        lock = _write_path_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _write_path_locks[key] = lock
        return lock


def _reserve_write_generation(path: Path) -> int:
    key = _write_path_key(path)
    with _write_guard_lock:
        token = _write_generations.get(key, 0) + 1
        _write_generations[key] = token
        return token


def _is_current_write_generation(path: Path, token: int) -> bool:
    key = _write_path_key(path)
    with _write_guard_lock:
        return _write_generations.get(key, 0) == token


def _cleanup_write_generations(path: Path) -> None:
    with _write_guard_lock:
        if path not in _pending_writes:
            key = _write_path_key(path)
            _write_generations.pop(key, None)
            _write_path_locks.pop(key, None)


def _cancel_pending_write(path: Path) -> None:
    key = _write_path_key(path)
    with _write_guard_lock:
        _write_generations[key] = _write_generations.get(key, 0) + 1
        _pending_writes.discard(path)
        _cleanup_write_generations(path)


def _safe_write(path: Path, content: str) -> None:
    """Atomically write content to path via unique-temp-file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8",
        dir=path.parent, suffix=".tmp", delete=False,
    )
    try:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    finally:
        f.close()
    for _ in range(5):
        try:
            os.replace(f.name, path)
            return
        except PermissionError:
            time.sleep(0.01)
    os.replace(f.name, path)


def _file_flush_worker() -> None:
    while True:
        try:
            item = _write_queue.get(timeout=1)
        except Exception:
            continue
        if item is None:
            break
        path, content, token = item
        try:
            with _write_path_lock(path):
                if not _is_current_write_generation(path, token):
                    continue
                _safe_write(path, content)
        except Exception:
            logger.warning("Async write failed for %s", path)
        finally:
            _pending_writes.discard(path)
            _cleanup_write_generations(path)


_write_thread = threading.Thread(target=_file_flush_worker, daemon=True)
_write_thread.start()


def _shutdown_file_writer() -> None:
    _write_queue.put(None)
    _write_thread.join(timeout=5)


import atexit as _atexit
_atexit.register(_shutdown_file_writer)


def async_submit(path: Path, content: str) -> None:
    """Submit a file write to the background thread."""
    token = _reserve_write_generation(path)
    _pending_writes.add(path)
    try:
        _write_queue.put_nowait((path, content, token))
    except queue.Full:
        _pending_writes.discard(path)
        try:
            with _write_path_lock(path):
                if _is_current_write_generation(path, token):
                    _safe_write(path, content)
        except Exception as e:
            logger.warning("Sync write fallback failed for %s: %s", path, e)
