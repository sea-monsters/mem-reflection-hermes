"""tests/test_wave3_retrieval.py — Wave 3 retrieval layer evaluation.

Unit tests + minimal integration tests for:
- W3.1 spread_activation iterative fixed-point
- W3.2 read-without-lock (smoke test)
- W3.3 hub detection in fusion_search
- W3.4 list_active time sorting

Run: pytest tests/test_wave3_retrieval.py -v
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import (
    LoadedMemory, MemoryFrontmatter, _tokenise,
    _CJK_STOPWORDS, _STOPWORDS,
)
from graph.ahe_graph import GraphStore
from graph.pagerank import compute_pagerank


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_graph_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_graph.db"
        store = GraphStore(db_path)
        yield store
        store.close()


@pytest.fixture
def sample_memories() -> List[LoadedMemory]:
    """Create a small set of memories with known properties."""
    mems: List[LoadedMemory] = []
    for i in range(5):
        fm = MemoryFrontmatter.new(
            source="test",
            confidence="medium",
            tags=["test", f"tag{i}"],
            zone="general",
        )
        # Override created for deterministic sorting
        fm.created = datetime(2026, 1, 1 + i, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        m = LoadedMemory(
            frontmatter=fm,
            body=f"Memory content number {i} about topic {i}",
            scope="user",
            source_path=Path(f"/tmp/mem_{i}.md"),
        )
        mems.append(m)
    return mems


# ---------------------------------------------------------------------------
# W3.1: Spreading Activation
# ---------------------------------------------------------------------------

class TestSpreadActivation:
    def test_empty_seeds(self, temp_graph_store: GraphStore):
        result = temp_graph_store.spread_activation([])
        assert result == {}

    def test_single_seed_no_edges(self, temp_graph_store: GraphStore):
        result = temp_graph_store.spread_activation(["mem_a"])
        assert result == {}

    def test_two_connected_nodes(self, temp_graph_store: GraphStore):
        store = temp_graph_store
        store.upsert_edge("mem_a", "mem_b", weight_delta=0.3)
        result = store.spread_activation(["mem_a"], decay=0.7, max_iter=20)
        assert "mem_b" in result
        # mem_b should have positive activation
        assert result["mem_b"] > 0.0
        # mem_a (seed) should be excluded
        assert "mem_a" not in result

    def test_convergence(self, temp_graph_store: GraphStore):
        store = temp_graph_store
        store.upsert_edge("a", "b", weight_delta=0.5)
        store.upsert_edge("b", "c", weight_delta=0.5)
        result = store.spread_activation(["a"], decay=0.7, threshold=1e-4, max_iter=100)
        # Both b and c should be activated
        assert "b" in result
        assert "c" in result
        # Direct neighbor should have higher activation than 2-hop
        assert result["b"] > result["c"]

    def test_adjacency_cache(self, temp_graph_store: GraphStore):
        store = temp_graph_store
        store.upsert_edge("x", "y", weight_delta=0.5)
        # First call builds cache
        r1 = store.spread_activation(["x"])
        # Second call uses cache
        r2 = store.spread_activation(["x"])
        assert r1 == r2
        # After edge mutation, cache invalidated
        store.upsert_edge("x", "z", weight_delta=0.5)
        r3 = store.spread_activation(["x"])
        assert "z" in r3


# ---------------------------------------------------------------------------
# W3.2: Threading — read without lock smoke test
# ---------------------------------------------------------------------------

class TestReadWithoutLock:
    def test_get_neighbors_no_lock(self, temp_graph_store: GraphStore):
        store = temp_graph_store
        store.upsert_edge("a", "b", weight_delta=0.5)
        # get_neighbors is a read operation; should not raise
        neighbors = store.get_neighbors("a")
        assert any(n["memory_id"] == "b" for n in neighbors)

    def test_stats_no_lock(self, temp_graph_store: GraphStore):
        store = temp_graph_store
        stats = store.stats()
        assert "node_count" in stats
        assert "edge_count" in stats


# ---------------------------------------------------------------------------
# W3.3: Hub Detection (via PageRank)
# ---------------------------------------------------------------------------

class TestHubDetection:
    def test_pagerank_hub_identification(self, temp_graph_store: GraphStore):
        store = temp_graph_store
        # Star topology: hub connects to 4 leaves
        for i in range(4):
            store.upsert_edge("hub", f"leaf_{i}", weight_delta=0.5)
        scores = compute_pagerank(store)
        # hub should have highest PageRank
        assert scores["hub"] == max(scores.values())
        # hub score should be > 0.15 (hub threshold)
        assert scores["hub"] > 0.15

    def test_pagerank_isolated_node(self, temp_graph_store: GraphStore):
        store = temp_graph_store
        # Only meta, no edges
        store.ensure_meta("isolated")
        scores = compute_pagerank(store)
        # isolated has no edges → zero or minimal score
        assert scores.get("isolated", 0.0) < 0.15


# ---------------------------------------------------------------------------
# W3.4: Time-based sorting
# ---------------------------------------------------------------------------

class TestTimeSorting:
    def test_list_active_sort_by_created_desc(self, sample_memories):
        from __init__ import MemoryStore
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = MemoryStore(user_root=root)
            for m in sample_memories:
                store._cache["active"].append(m)
                store._id_to_mem[m.id()] = m
            store._cache_valid = True

            sorted_desc = store.list_active(sort_by="created", order="desc")
            sorted_asc = store.list_active(sort_by="created", order="asc")

            assert len(sorted_desc) == 5
            # Descending: newest first
            dates_desc = [datetime.fromisoformat(m.frontmatter.created.replace("Z", "+00:00")) for m in sorted_desc]
            assert dates_desc == sorted(dates_desc, reverse=True)
            # Ascending: oldest first
            dates_asc = [datetime.fromisoformat(m.frontmatter.created.replace("Z", "+00:00")) for m in sorted_asc]
            assert dates_asc == sorted(dates_asc)

    def test_list_active_no_sort(self, sample_memories):
        from __init__ import MemoryStore
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = MemoryStore(user_root=root)
            for m in sample_memories:
                store._cache["active"].append(m)
                store._id_to_mem[m.id()] = m
            store._cache_valid = True

            unsorted = store.list_active()
            assert len(unsorted) == 5


# ---------------------------------------------------------------------------
# W2.2: BM25 CJK stopwords
# ---------------------------------------------------------------------------

class TestBM25CJK:
    def test_cjk_stopwords_filtered(self):
        text = "我的记忆是关于用户的偏好和习惯"
        tokens = _tokenise(text)
        # Common CJK stopwords should be excluded
        assert "的" not in tokens
        assert "是" not in tokens
        assert "我" not in tokens
        assert "和" not in tokens
        # Content words should remain
        assert "记忆" in tokens
        assert "用户" in tokens
        assert "偏好" in tokens

    def test_english_stopwords_still_filtered(self):
        text = "the user prefers dark mode"
        tokens = _tokenise(text)
        assert "the" not in tokens
        assert "user" in tokens
        assert "prefers" in tokens


# ---------------------------------------------------------------------------
# Fusion Search Integration (minimal, ≤10 queries)
# ---------------------------------------------------------------------------

class TestFusionSearchMinimal:
    def test_fusion_search_finds_relevant(self):
        """Integration: with mock memories, fusion_search should find relevant results."""
        from __init__ import MemoryStore
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = MemoryStore(user_root=root)
            # Create 3 memories
            for i, body in enumerate([
                "User prefers dark mode in all applications",
                "User likes golang for backend development",
                "Meeting notes from Tuesday standup",
            ]):
                fm = MemoryFrontmatter.new(source="test", tags=["pref", "dev"])
                fm.created = datetime(2026, 1, 1 + i, 12, 0, 0, tzinfo=timezone.utc).isoformat()
                m = LoadedMemory(
                    frontmatter=fm,
                    body=body,
                    scope="user",
                    source_path=root / f"mem_{i}.md",
                )
                store._cache["active"].append(m)
                store._id_to_mem[m.id()] = m
            store._cache_valid = True

            # Query about dark mode
            results = store.fusion_search("dark mode preference", k=2)
            assert len(results) > 0
            # Top result should contain "dark mode"
            assert "dark" in results[0].body.lower()

    def test_fusion_search_zone_filter(self):
        from __init__ import MemoryStore
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = MemoryStore(user_root=root)
            for i, (body, zone) in enumerate([
                ("Work project deadline tomorrow", "work"),
                ("Personal hobby photography", "general"),
            ]):
                fm = MemoryFrontmatter.new(source="test", zone=zone)
                m = LoadedMemory(
                    frontmatter=fm,
                    body=body,
                    scope="user",
                    source_path=root / f"mem_{i}.md",
                )
                store._cache["active"].append(m)
                store._id_to_mem[m.id()] = m
            store._cache_valid = True

            results = store.fusion_search("project", k=5, zone="work")
            assert all(r.frontmatter.zone == "work" for r in results)
