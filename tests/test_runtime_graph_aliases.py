"""Regression tests for runtime/graph.py public alias functions.

These aliases are imported by __init__.py for tool registration.
They must call existing methods on GraphManagerCompat and return valid JSON.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

# Load runtime/graph.py directly (it imports core.graph lazily inside GraphManagerCompat)
_spec = importlib.util.spec_from_file_location("_runtime_graph", str(_REPO / "runtime" / "graph.py"))
_runtime_graph = importlib.util.module_from_spec(_spec)
sys.modules["_runtime_graph"] = _runtime_graph
_spec.loader.exec_module(_runtime_graph)

GraphManagerCompat = _runtime_graph.GraphManagerCompat

# Also load core/store.py for MemoryStore (used by health alias)
_spec_store = importlib.util.spec_from_file_location("_store", str(_REPO / "core" / "store.py"))
_store = importlib.util.module_from_spec(_spec_store)
sys.modules["_store"] = _store
_spec_store.loader.exec_module(_store)
MemoryStore = _store.MemoryStore


@pytest.fixture
def temp_gm():
    """GraphManagerCompat backed by a temporary SQLite database."""
    with tempfile.TemporaryDirectory(prefix="hermes_graph_alias_") as tmpdir:
        db_path = Path(tmpdir) / "graph.db"
        gm = GraphManagerCompat(db_path)
        yield gm
        gm.close()


@pytest.fixture
def temp_mem_store():
    """MemoryStore backed by a temporary directory and database."""
    tmpdir = tempfile.mkdtemp(prefix="hermes_store_alias_")
    root = Path(tmpdir) / "memories"
    root.mkdir(parents=True, exist_ok=True)
    db_path = Path(tmpdir) / "memories.db"
    store = MemoryStore(user_root=root, db_path=db_path)
    yield store
    # Close SQLite connections before cleanup (Windows file locking)
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


class TestAliasFunctions:
    """Each alias must not raise AttributeError and must return valid JSON."""

    def _patch_getters(self, monkeypatch, gm, mem_store=None):
        """Patch the getters that the alias functions import at call time.

        The aliases do a local import:
            from .. import _get_graph_mgr
        When runtime/graph.py is loaded standalone as '_runtime_graph', the
        relative import fails and falls back to:
            from mem_reflection_hermes import _get_graph_mgr
        We therefore inject the test doubles into the real package namespace.
        """
        import mem_reflection_hermes as _pkg

        monkeypatch.setattr(_pkg, "_get_graph_mgr", lambda: gm, raising=False)
        if mem_store is not None:
            monkeypatch.setattr(_pkg, "_get_mem_store", lambda: mem_store, raising=False)

        # The aliases also try a relative import first; make that resolve to the
        # same doubles by giving the standalone module a parent attribute.
        monkeypatch.setattr(_runtime_graph, "_get_graph_mgr", lambda: gm, raising=False)
        if mem_store is not None:
            monkeypatch.setattr(_runtime_graph, "_get_mem_store", lambda: mem_store, raising=False)

    def test_srh_graph_retrieve_empty(self, temp_gm, monkeypatch):
        """srh_graph_retrieve on empty graph returns empty results."""
        self._patch_getters(monkeypatch, temp_gm)
        result = _runtime_graph.srh_graph_retrieve(
            {"memory_ids": ["nonexistent"], "max_results": 5, "tier": "list"}
        )
        parsed = json.loads(result)
        assert "results" in parsed
        assert parsed["results"] == []

    def test_srh_graph_retrieve_with_nodes(self, temp_gm, monkeypatch):
        """srh_graph_retrieve returns related nodes after association."""
        gm = temp_gm
        gm.store.ensure_meta("a", zone="general")
        gm.store.ensure_meta("b", zone="general")
        gm.associator.on_co_occurrence(["a", "b"])
        self._patch_getters(monkeypatch, gm)
        result = _runtime_graph.srh_graph_retrieve(
            {"memory_ids": ["a"], "max_results": 5, "tier": "detail"}
        )
        parsed = json.loads(result)
        assert "results" in parsed
        assert len(parsed["results"]) >= 1
        # detail tier should include weight
        assert "weight" in parsed["results"][0]

    def test_srh_graph_retrieve_tier_all_backward_compat(self, temp_gm, monkeypatch):
        """tier='all' is mapped to 'detail' for backward compatibility."""
        gm = temp_gm
        gm.store.ensure_meta("a", zone="general")
        gm.store.ensure_meta("b", zone="general")
        gm.associator.on_co_occurrence(["a", "b"])
        self._patch_getters(monkeypatch, gm)
        result = _runtime_graph.srh_graph_retrieve(
            {"memory_ids": ["a"], "max_results": 5, "tier": "all"}
        )
        parsed = json.loads(result)
        assert "results" in parsed
        # Should not crash; detail tier returns full records
        assert len(parsed["results"]) >= 1

    def test_srh_graph_stats(self, temp_gm, monkeypatch):
        """srh_graph_stats returns JSON with nodes/edges keys."""
        gm = temp_gm
        gm.store.ensure_meta("a", zone="general")
        gm.associator.on_co_occurrence(["a", "a"])  # no-op but safe
        self._patch_getters(monkeypatch, gm)
        result = _runtime_graph.srh_graph_stats({})
        parsed = json.loads(result)
        assert "nodes" in parsed
        assert "edges" in parsed
        assert "avg_weight" in parsed

    def test_srh_graph_viz_empty(self, temp_gm, monkeypatch):
        """srh_graph_viz on empty graph returns empty nodes/edges."""
        self._patch_getters(monkeypatch, temp_gm)
        result = _runtime_graph.srh_graph_viz({})
        parsed = json.loads(result)
        assert "nodes" in parsed
        assert "edges" in parsed
        assert parsed["nodes"] == []
        assert parsed["edges"] == []
        assert "stats" in parsed

    def test_srh_graph_viz_with_data(self, temp_gm, monkeypatch):
        """srh_graph_viz returns nodes and edges when graph has data."""
        gm = temp_gm
        gm.store.ensure_meta("a", zone="general")
        gm.store.ensure_meta("b", zone="general")
        gm.associator.on_co_occurrence(["a", "b"])
        self._patch_getters(monkeypatch, gm)
        result = _runtime_graph.srh_graph_viz({})
        parsed = json.loads(result)
        assert "nodes" in parsed
        assert "edges" in parsed
        assert len(parsed["nodes"]) >= 2
        assert len(parsed["edges"]) >= 1
        assert "stats" in parsed
        assert parsed["stats"]["graph_semantics"] == "associative_coactivation"

    def test_srh_memory_health(self, temp_gm, temp_mem_store, monkeypatch):
        """srh_memory_health returns JSON with graph_edges and memories_indexed."""
        gm = temp_gm
        store = temp_mem_store
        gm.store.ensure_meta("a", zone="general")
        gm.associator.on_co_occurrence(["a", "a"])
        self._patch_getters(monkeypatch, gm, store)
        result = _runtime_graph.srh_memory_health({})
        parsed = json.loads(result)
        assert "graph_edges" in parsed
        assert "memories_indexed" in parsed
        assert "status" in parsed
        assert parsed["status"] in ("healthy", "empty")
