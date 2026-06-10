"""test_graph_distil_failure.py — Test GraphIndex.distill() write failure path.

Coverage:
- When store.put() raises during distillation, exception is caught and logged

Run: pytest tests/test_graph_distil_failure.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent

_spec_graph = importlib.util.spec_from_file_location("_graph_distil", str(_REPO / "core" / "graph.py"))
_graph = importlib.util.module_from_spec(_spec_graph)
sys.modules["_graph_distil"] = _graph
_spec_graph.loader.exec_module(_graph)
GraphIndex = _graph.GraphIndex

_spec_store = importlib.util.spec_from_file_location("_store_distil", str(_REPO / "core" / "store.py"))
_store = importlib.util.module_from_spec(_spec_store)
sys.modules["_store_distil"] = _store
_spec_store.loader.exec_module(_store)
MemoryStore = _store.MemoryStore
MemoryFrontmatter = _store.MemoryFrontmatter


@pytest.fixture
def temp_graph_index():
    with tempfile.TemporaryDirectory(prefix="hermes_graph_") as tmpdir:
        db_path = Path(tmpdir) / "test_graph.db"
        gi = GraphIndex(db_path)
        yield gi
        gi.close()


@pytest.fixture
def temp_mem_store():
    tmpdir = tempfile.mkdtemp(prefix="hermes_store_")
    root = Path(tmpdir) / "memories"
    root.mkdir(parents=True, exist_ok=True)
    db_path = Path(tmpdir) / "memories.db"
    store = MemoryStore(user_root=root, db_path=db_path)
    yield store
    try:
        conn = getattr(store._local, "conn", None)
        if conn is not None:
            conn.close()
    except Exception:
        pass
    import shutil as _shutil
    import time as _time
    for _attempt in range(5):
        try:
            _shutil.rmtree(tmpdir)
            break
        except PermissionError:
            _time.sleep(0.1)
    _shutil.rmtree(tmpdir, ignore_errors=True)


class TestDistillWriteFailure:
    def test_distill_catches_store_put_failure(self, temp_graph_index, temp_mem_store, caplog):
        """If store.put() raises during distillation, the exception is caught and logged."""
        gi = temp_graph_index
        store = temp_mem_store

        # Seed graph: create memories and associate them together to build edges
        ids = []
        for i in range(5):
            fm = MemoryFrontmatter.new(source="test")
            store.put("user", fm, f"memory about topic alpha beta gamma {i}")
            ids.append(fm.id)

        # Associate all together so edges are created (associate needs >=2 IDs)
        gi.associate(ids)
        # Spread activation to build edge weights
        gi.spread(ids[:2], decay=0.9, max_iter=10)

        # Mock store.put to fail
        original_put = store.put
        call_count = {"count": 0}

        def failing_put(scope, fm, body):
            call_count["count"] += 1
            raise RuntimeError("simulated disk full")

        store.put = failing_put

        import logging
        try:
            with caplog.at_level(logging.WARNING, logger="mem_reflection_hermes.core.graph"):
                result = gi.distill(store)
            # Should not crash; returns empty list because all writes failed
            assert isinstance(result, list)
            assert call_count["count"] >= 1
            assert "Distillation write failed" in caplog.text
        finally:
            store.put = original_put
