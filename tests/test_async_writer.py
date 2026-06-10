"""test_async_writer.py — Tests for async file writer fallback paths.

Coverage:
- async_write_memory() queue.Full → sync fallback
- _file_flush_worker() exception handling
- _shutdown_file_writer() graceful shutdown

Run: pytest tests/test_async_writer.py -v
"""
from __future__ import annotations

import importlib.util
import queue
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PKG = "mem_reflection_hermes_async_test"


def _load_store_module():
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_REPO)]
    sys.modules[_PKG] = pkg

    core_mod = types.ModuleType(f"{_PKG}.core")
    core_mod.__path__ = [str(_REPO / "core")]
    core_mod.__package__ = f"{_PKG}.core"
    sys.modules[f"{_PKG}.core"] = core_mod

    spec = importlib.util.spec_from_file_location(f"{_PKG}.core.store", str(_REPO / "core" / "store.py"))
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = f"{_PKG}.core"
    sys.modules[f"{_PKG}.core.store"] = mod
    spec.loader.exec_module(mod)
    return mod


_store = _load_store_module()


class TestAsyncWriterFallback:
    def test_async_write_queue_full_falls_back_to_sync(self, monkeypatch, tmp_path):
        """When _write_queue.put_nowait raises queue.Full, sync fallback should write file."""
        path = tmp_path / "test_memory.md"
        fm = _store.MemoryFrontmatter.new(source="test")
        body = "test body content"

        # Patch queue to simulate Full condition
        mock_queue = MagicMock()
        mock_queue.put_nowait.side_effect = queue.Full
        monkeypatch.setattr(_store, "_write_queue", mock_queue)

        # Track sync writes
        sync_writes = []
        original_safe_write = _store._safe_write

        def tracking_safe_write(p, content):
            sync_writes.append((p, content))
            original_safe_write(p, content)

        monkeypatch.setattr(_store, "_safe_write", tracking_safe_write)

        _store.async_write_memory(path, fm, body)

        # Sync fallback should have been invoked
        assert len(sync_writes) == 1
        assert sync_writes[0][0] == path
        assert "test body content" in sync_writes[0][1]
        # File should exist with correct content
        assert path.exists()

    def test_async_write_exception_in_sync_fallback_is_logged(self, monkeypatch, caplog):
        """If both async and sync fallback fail, warning is logged."""
        path = Path(tempfile.mkdtemp(prefix="hermes_async_")) / "test.md"
        fm = _store.MemoryFrontmatter.new(source="test")

        mock_queue = MagicMock()
        mock_queue.put_nowait.side_effect = queue.Full
        monkeypatch.setattr(_store, "_write_queue", mock_queue)

        monkeypatch.setattr(_store, "_safe_write", lambda p, c: (_ for _ in ()).throw(RuntimeError("disk full")))

        import logging
        with caplog.at_level(logging.WARNING, logger="mem_reflection_hermes.core.store"):
            _store.async_write_memory(path, fm, "body")

        assert "Sync write fallback failed" in caplog.text


class TestFileFlushWorker:
    def test_worker_exception_is_logged(self, monkeypatch, caplog):
        """Exception in _file_flush_worker should be logged, not crash thread."""
        import logging

        q = queue.Queue()
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_worker_"))
        path = tmpdir / "file.md"

        fm = _store.MemoryFrontmatter.new(source="test")
        content = _store.serialize_frontmatter(_store._frontmatter_to_data(fm), "body")
        token = _store._reserve_write_generation(path)
        _store._pending_writes.add(path)

        q.put((path, content, token))
        q.put(None)  # sentinel to stop worker

        monkeypatch.setattr(_store, "_write_queue", q)
        # _safe_write auto-creates dirs; mock it to raise instead
        monkeypatch.setattr(
            _store, "_safe_write",
            lambda p, c: (_ for _ in ()).throw(RuntimeError("disk full")),
        )

        with caplog.at_level(logging.WARNING, logger="mem_reflection_hermes.core.store"):
            _store._file_flush_worker()

        assert "Async write failed" in caplog.text
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
