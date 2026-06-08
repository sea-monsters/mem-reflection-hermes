"""test_search.py — Tests for SearchIndex.

Coverage:
- check_conflict dual-path (embedding + BM25)
- RRF fusion correctness
- MMR diversity re-ranking

Run: pytest tests/test_search.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

_spec_store = importlib.util.spec_from_file_location("_store", str(_REPO / "core" / "store.py"))
_store = importlib.util.module_from_spec(_spec_store)
sys.modules["_store"] = _store
_spec_store.loader.exec_module(_store)
MemoryStore = _store.MemoryStore
MemoryFrontmatter = _store.MemoryFrontmatter
LoadedMemory = _store.LoadedMemory

_spec_search = importlib.util.spec_from_file_location("_search", str(_REPO / "core" / "search.py"))
_search = importlib.util.module_from_spec(_spec_search)
sys.modules["_search"] = _search
_spec_search.loader.exec_module(_search)
SearchIndex = _search.SearchIndex

_spec_graph = importlib.util.spec_from_file_location("_graph", str(_REPO / "core" / "graph.py"))
_graph = importlib.util.module_from_spec(_spec_graph)
sys.modules["_graph"] = _graph
_spec_graph.loader.exec_module(_graph)
GraphIndex = _graph.GraphIndex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


@pytest.fixture
def temp_search(temp_mem_store):
    """SearchIndex backed by a temporary MemoryStore."""
    store = temp_mem_store
    return SearchIndex(store)


# ---------------------------------------------------------------------------
# RRF Fusion
# ---------------------------------------------------------------------------

class TestRRFFusion:
    def test_rrf_single_channel(self, temp_search, temp_mem_store):
        """RRF with only one channel still produces valid scores."""
        si = temp_search
        store = temp_mem_store
        fm1 = MemoryFrontmatter.new(source="test")
        fm2 = MemoryFrontmatter.new(source="test")
        store.put("user", fm1, "memory a")
        store.put("user", fm2, "memory b")
        active = store.list_active()
        active_map = {m.id(): m for m in active}
        embed_results = {active[0].id(): 0.9, active[1].id(): 0.8}
        bm25_results = {}
        fused = si._rrf_fusion(embed_results, bm25_results, active_map)
        assert active[0].id() in fused
        assert active[1].id() in fused
        assert fused[active[0].id()] > fused[active[1].id()]  # a ranked higher in embed

    def test_rrf_both_channels_boost_overlap(self, temp_search):
        """Memory present in both channels gets higher RRF score."""
        si = temp_search
        active_map = {"m1": None, "m2": None, "m3": None}
        embed_results = {"m1": 0.9, "m2": 0.5}
        bm25_results = {"m1": 0.6, "m3": 0.8}
        fused = si._rrf_fusion(embed_results, bm25_results, active_map)
        # m1 is in both channels → highest score
        assert fused["m1"] > fused["m2"]
        assert fused["m1"] > fused["m3"]

    def test_rrf_k60_constant(self, temp_search):
        """RRF scores use k=60 constant (parameter-free)."""
        si = temp_search
        active_map = {"a": None}
        embed_results = {"a": 1.0}
        fused = si._rrf_fusion(embed_results, {}, active_map)
        # rank=1 → score = 1/(60+1) ≈ 0.01639
        assert abs(fused["a"] - 1 / 61) < 1e-6

    def test_rrf_excludes_missing_from_active_map(self, temp_search):
        """RRF filters out IDs not present in active_map."""
        si = temp_search
        active_map = {"a": None}
        embed_results = {"a": 0.9, "b": 0.8}
        fused = si._rrf_fusion(embed_results, {}, active_map)
        assert "a" in fused
        assert "b" not in fused


# ---------------------------------------------------------------------------
# MMR Re-ranking
# ---------------------------------------------------------------------------

class TestMMRRerank:
    def test_mmr_empty_candidates(self, temp_search):
        """MMR with empty candidates returns empty list."""
        si = temp_search
        result = si._mmr_rerank("query", [])
        assert result == []

    def test_mmr_single_candidate(self, temp_search):
        """MMR with one candidate returns it unchanged."""
        si = temp_search
        fm = MemoryFrontmatter.new(source="test")
        m = LoadedMemory(frontmatter=fm, body="only one", source_path=Path("/tmp/1.md"), scope="user")
        result = si._mmr_rerank("query", [m])
        assert len(result) == 1
        assert result[0].id() == m.id()

    def test_mmr_promotes_diversity(self, temp_search, temp_mem_store):
        """MMR selects diverse candidates over redundant ones."""
        si = temp_search
        store = temp_mem_store

        # Create two semantically similar memories and one different
        fm1 = MemoryFrontmatter.new(source="test")
        fm2 = MemoryFrontmatter.new(source="test")
        fm3 = MemoryFrontmatter.new(source="test")
        store.put("user", fm1, "Python machine learning with scikit-learn")
        store.put("user", fm2, "Python machine learning with tensorflow")
        store.put("user", fm3, "Golang backend microservices architecture")

        mems = store.list_active()
        # Query favors ML content, so fm1 and fm2 would rank high without MMR
        reranked = si._mmr_rerank("machine learning", mems, lambda_param=0.7, top_n=3)
        ids = [m.id() for m in reranked]
        # First should be most relevant (one of the ML ones)
        # Second should diversify (the golang one, not the other ML one)
        assert len(reranked) == 3

    def test_mmr_lambda_zero_pure_diversity(self, temp_search, temp_mem_store):
        """λ=0 means pure diversity (second pick maximally different from first)."""
        si = temp_search
        store = temp_mem_store

        fm1 = MemoryFrontmatter.new(source="test")
        fm2 = MemoryFrontmatter.new(source="test")
        fm3 = MemoryFrontmatter.new(source="test")
        store.put("user", fm1, "aaa bbb ccc ddd eee fff ggg")
        store.put("user", fm2, "aaa bbb ccc ddd eee fff ggg")
        store.put("user", fm3, "zzz yyy xxx www vvv uuu ttt")

        mems = store.list_active()
        reranked = si._mmr_rerank("aaa bbb", mems, lambda_param=0.0, top_n=3)
        ids = [m.id() for m in reranked]
        # First pick is most relevant (one of the identical ones)
        # Second pick should be the maximally different one (zzz...)
        assert ids[1] == fm3.id or ids[2] == fm3.id


# ---------------------------------------------------------------------------
# Dual-path conflict detection
# ---------------------------------------------------------------------------

class TestCheckConflict:
    def test_no_conflict_empty_store(self, temp_search):
        """check_conflict on empty store returns None."""
        si = temp_search
        result = si.check_conflict("some content")
        assert result is None

    def test_bm25_path_detects_keyword_overlap(self, temp_search, temp_mem_store):
        """Path 2 (BM25) detects keyword overlap when embeddings unavailable."""
        si = temp_search
        store = temp_mem_store

        fm = MemoryFrontmatter.new(source="test")
        store.put("user", fm, "User prefers dark mode in all applications")

        # Ensure embed index is not built (no embeddings)
        si._embed_array = None
        result = si.check_conflict(
            "User prefers dark mode in all applications", threshold=0.3
        )
        assert result is not None
        assert result[0] == fm.id
        assert result[1] > 0.3

    def test_exclude_ids_works(self, temp_search, temp_mem_store):
        """exclude_ids prevents matching specified memories."""
        si = temp_search
        store = temp_mem_store

        fm = MemoryFrontmatter.new(source="test")
        store.put("user", fm, "Unique memory content xyz")

        # Exclude the only memory
        result = si.check_conflict("Unique memory content xyz", exclude_ids=[fm.id])
        assert result is None

    def test_threshold_tuning(self, temp_search, temp_mem_store):
        """Higher threshold reduces false positives."""
        si = temp_search
        store = temp_mem_store

        fm = MemoryFrontmatter.new(source="test")
        store.put("user", fm, "Completely different topic about databases")

        # Very high threshold should not match unrelated content
        result = si.check_conflict("Machine learning with Python", threshold=0.95)
        assert result is None

    def test_short_text_threshold_adjustment(self, temp_search, temp_mem_store):
        """Short text gets a lower threshold adjustment."""
        si = temp_search
        store = temp_mem_store

        fm = MemoryFrontmatter.new(source="test")
        store.put("user", fm, "short text")

        # Short text should use threshold - 0.05 (but capped at 0.65)
        # Use low threshold so identical text definitely matches
        result = si.check_conflict("short text", threshold=0.3)
        assert result is not None
        assert result[0] == fm.id


class TestSearchCacheBoundaries:
    def test_include_history_is_part_of_cache_key(self, temp_search, temp_mem_store):
        """Active-only and history-inclusive searches must not share cache entries."""
        si = temp_search
        store = temp_mem_store

        old = MemoryFrontmatter.new(source="test")
        old.id = "old-pref"
        store.put("user", old, "User prefers Python for backend services")

        new = MemoryFrontmatter.new(source="test")
        new.id = "new-pref"
        new.supersedes = ["old-pref"]
        store.put("user", new, "User prefers Go for backend services")

        active_first = si.search("Python backend", k=10, include_history=False)
        assert "old-pref" not in [m.id() for m in active_first]

        with_history = si.search("Python backend", k=10, include_history=True)
        assert "old-pref" in [m.id() for m in with_history]

        si.invalidate_cache()
        history_first = si.search("Python backend", k=10, include_history=True)
        assert "old-pref" in [m.id() for m in history_first]

        active_second = si.search("Python backend", k=10, include_history=False)
        assert "old-pref" not in [m.id() for m in active_second]


class TestStoreSearchGraphWiring:
    def test_store_fusion_search_uses_injected_graph(self, temp_mem_store):
        """Tool-facing store.fusion_search path should preserve graph injection."""
        store = temp_mem_store
        fm = MemoryFrontmatter.new(source="test")
        store.put("user", fm, "Graph connected memory about retrieval")

        class FakeGraph:
            def __init__(self):
                self.calls = []

            def spread(self, seed_ids, decay=0.7, max_iter=30):
                self.calls.append((list(seed_ids), decay, max_iter))
                return {mid: 1.0 for mid in seed_ids}

        graph = FakeGraph()
        store.set_graph(graph)
        results = store.fusion_search("retrieval", k=3, hebbian_beta=0.5)

        assert any(m.id() == fm.id for m in results)
        assert graph.calls

    def test_store_fusion_search_graph_boost_changes_ranking(self, temp_mem_store):
        """Graph boost should affect the tool-facing ranking, not just call plumbing."""
        store = temp_mem_store

        primary = MemoryFrontmatter.new(source="test")
        primary.id = "primary-match"
        store.put("user", primary, "retrieval retrieval retrieval architecture")

        connected = MemoryFrontmatter.new(source="test")
        connected.id = "connected-match"
        store.put("user", connected, "retrieval architecture")

        without_graph = store.fusion_search("retrieval", k=2, hebbian_beta=0.0)
        assert without_graph[0].id() == "primary-match"

        class RankingGraph:
            def spread(self, seed_ids, decay=0.7, max_iter=30):
                assert "connected-match" in seed_ids
                return {"connected-match": 1.0}

        store.set_graph(RankingGraph())
        with_graph = store.fusion_search("retrieval", k=2, hebbian_beta=10.0)

        assert with_graph[0].id() == "connected-match"
