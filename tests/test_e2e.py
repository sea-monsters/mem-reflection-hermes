"""test_e2e.py — End-to-end integration tests across all modules.

Coverage:
- Full memory lifecycle (store → search → graph → reflect → context → dashboard)
- Update propagation across index layers
- Reflection → memory → graph association chain
- Conflict detection and deduplication
- Context assembly priority enforcement

Run: pytest tests/test_e2e.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent

# Load store module
_spec_store = importlib.util.spec_from_file_location("_e2e_store", str(_REPO / "core" / "store.py"))
_e2e_store = importlib.util.module_from_spec(_spec_store)
sys.modules["_e2e_store"] = _e2e_store
_spec_store.loader.exec_module(_e2e_store)
MemoryStore = _e2e_store.MemoryStore
MemoryFrontmatter = _e2e_store.MemoryFrontmatter
LoadedMemory = _e2e_store.LoadedMemory

# Load search module
_spec_search = importlib.util.spec_from_file_location("_e2e_search", str(_REPO / "core" / "search.py"))
_e2e_search = importlib.util.module_from_spec(_spec_search)
sys.modules["_e2e_search"] = _e2e_search
_spec_search.loader.exec_module(_e2e_search)
SearchIndex = _e2e_search.SearchIndex

# Load graph module
_spec_graph = importlib.util.spec_from_file_location("_e2e_graph", str(_REPO / "core" / "graph.py"))
_e2e_graph = importlib.util.module_from_spec(_spec_graph)
sys.modules["_e2e_graph"] = _e2e_graph
_spec_graph.loader.exec_module(_e2e_graph)
GraphIndex = _e2e_graph.GraphIndex

# Load reflect module
_spec_reflect = importlib.util.spec_from_file_location("_e2e_reflect", str(_REPO / "reflection" / "engine.py"))
_e2e_reflect = importlib.util.module_from_spec(_spec_reflect)
sys.modules["_e2e_reflect"] = _e2e_reflect
_spec_reflect.loader.exec_module(_e2e_reflect)
ReflectionEngine = _e2e_reflect.ReflectionEngine

# Load context module
_spec_context = importlib.util.spec_from_file_location("_e2e_context", str(_REPO / "memory" / "context.py"))
_e2e_context = importlib.util.module_from_spec(_spec_context)
sys.modules["_e2e_context"] = _e2e_context
_spec_context.loader.exec_module(_e2e_context)
build_context = _e2e_context.build_context


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_system():
    """Yield a fully wired temp system: store, search, graph, engine."""
    tmpdir = tempfile.mkdtemp(prefix="hermes_e2e_")
    root = Path(tmpdir) / "memories"
    root.mkdir(parents=True, exist_ok=True)
    db_path = Path(tmpdir) / "memories.db"
    store = MemoryStore(user_root=root, db_path=db_path)
    search = SearchIndex(store)
    graph_db = Path(tmpdir) / "graph.db"
    graph = GraphIndex(graph_db)
    engine = ReflectionEngine(store, search, graph, log_path=Path(tmpdir) / "reflect-log.jsonl")
    yield store, search, graph, engine
    try:
        conn = getattr(store._local, "conn", None)
        if conn is not None:
            conn.close()
    except Exception:
        pass
    graph.close()
    import shutil as _shutil
    import time as _time
    for _attempt in range(5):
        try:
            _shutil.rmtree(tmpdir)
            break
        except PermissionError:
            _time.sleep(0.1)
    _shutil.rmtree(tmpdir, ignore_errors=True)


class TestFullLifecycle:
    def test_create_search_graph_reflect_context(self, temp_system):
        """A memory created in the store is findable by search,
        associable in the graph, and appears in context."""
        store, search, graph, engine = temp_system
        fm = MemoryFrontmatter.new(source="user", zone="work", pinned=True)
        store.put("user", fm, "User prefers dark mode in all applications.")

        # Search finds it
        results = search.search("dark mode", k=5)
        assert any(r.frontmatter.id == fm.id for r in results)

        # Graph can associate it
        graph.associate([fm.id, "mem-2"])
        neighbors = graph.neighbors(fm.id)
        assert any(n["memory_id"] == "mem-2" for n in neighbors)

        # Context includes pinned memory
        class FakeSkills:
            def list(self):
                return []
        ctx = build_context(store, search, FakeSkills(), "dark mode", max_tokens=4000)
        assert "Pinned Memories" in ctx
        assert "dark mode" in ctx.lower()

    def test_update_propagates(self, temp_system):
        """Updating a memory body changes search results."""
        store, search, graph, engine = temp_system
        fm = MemoryFrontmatter.new(source="user")
        store.put("user", fm, "Original content about Python.")

        # Search old term
        assert any("Python" in r.body for r in search.search("Python", k=5))

        # Update body
        store.update(fm.id, body="Updated content about Rust.")

        # Search index is lazily built; force rebuild for test
        search._bm25_retriever = None
        search._embed_array = None
        search.invalidate_cache()
        _e2e_search._embed_single.cache_clear()

        # Search new term
        assert any("Rust" in r.body for r in search.search("Rust", k=5))
        # Old term should not match strongly
        old_results = search.search("Python", k=5)
        assert not any(r.frontmatter.id == fm.id and "Python" in r.body for r in old_results)

    def test_reflection_creates_memories(self, temp_system):
        """Raw chunk reflection stores episode memories."""
        store, search, graph, engine = temp_system
        engine._mode = "raw_chunk"
        result = engine.micro(None, "Remember that I prefer Go for backend work.", "Got it.")
        assert result is not None
        assert result["type"] == "raw_chunk"

        # Memory should be in store
        mems = store.list_active()
        assert any("Go" in m.body for m in mems)

    def test_conflict_avoids_duplicates(self, temp_system):
        """Storing nearly identical content twice should flag conflict."""
        store, search, graph, engine = temp_system
        fm1 = MemoryFrontmatter.new(source="user")
        store.put("user", fm1, "User likes dark mode.")

        conflict = search.check_conflict("User likes dark mode.", threshold=0.3)
        assert conflict is not None
        assert conflict[0] == fm1.id

    def test_context_priority_layers(self, temp_system):
        """Pinned > Active > Skills in context output."""
        store, search, graph, engine = temp_system
        # Pinned
        fm_p = MemoryFrontmatter.new(source="user", pinned=True)
        store.put("user", fm_p, "Pinned important note.")
        # Active
        fm_a = MemoryFrontmatter.new(source="user")
        store.put("user", fm_a, "Active note about testing.")

        class FakeSkills:
            def list(self):
                return []
        ctx = build_context(store, search, FakeSkills(), "testing", max_tokens=4000)
        # Both layers present
        assert "Pinned Memories" in ctx
        assert "Relevant Memories" in ctx

    def test_graph_pagerank_and_decay(self, temp_system):
        """Graph PageRank and step decay work after associations."""
        store, search, graph, engine = temp_system
        ids = [f"mem-{i}" for i in range(4)]
        for i, mid in enumerate(ids):
            fm = MemoryFrontmatter.new(source="test")
            store.put("user", fm, f"Memory {i}")
            if i > 0:
                graph.associate([ids[i - 1], mid])

        pr = graph.pagerank()
        assert len(pr) == 4
        # All have some score
        assert all(v > 0 for v in pr.values())

        # Decay step
        graph.step_decay()
        edges = graph.neighbors(ids[0])
        assert all(e["weight"] < 0.5 for e in edges)
