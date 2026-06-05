"""test_graph_operations.py — Graph store, spread activation, PageRank tests.

Extends test_wave3_retrieval.py with additional coverage for:
- Edge CRUD operations
- Spread activation decay sensitivity
- SUPERSEDES edge filtering
- PageRank correctness
- Concurrent read safety

Run: pytest tests/test_graph_operations.py -v
"""
from __future__ import annotations

import threading

import pytest

from graph.compat import GraphStore


class TestEdgeCRUD:
    def test_upsert_edge_creates(self, temp_graph):
        store = temp_graph
        # New edge: weight = 0.5 + weight_delta
        store.upsert_edge("a", "b", weight_delta=0.3)
        neighbors = store.get_neighbors("a", min_weight=0.0)
        assert len(neighbors) == 1
        assert neighbors[0]["memory_id"] == "b"
        assert abs(neighbors[0]["weight"] - 0.8) < 1e-6  # 0.5 + 0.3

    def test_upsert_edge_accumulates(self, temp_graph):
        store = temp_graph
        # First: weight = 0.5 + 0.3 = 0.8
        store.upsert_edge("a", "b", weight_delta=0.3)
        # Second: weight = 0.8 + 0.2 = 1.0 (clamped)
        store.upsert_edge("a", "b", weight_delta=0.2)
        neighbors = store.get_neighbors("a", min_weight=0.0)
        assert len(neighbors) == 1
        assert abs(neighbors[0]["weight"] - 1.0) < 1e-6

    def test_upsert_edge_symmetric(self, temp_graph):
        store = temp_graph
        store.upsert_edge("a", "b", weight_delta=0.5)
        # get_neighbors looks at both source_id and target_id
        neighbors_b = store.get_neighbors("b")
        assert any(n["memory_id"] == "a" for n in neighbors_b)

    def test_get_neighbors_empty(self, temp_graph):
        store = temp_graph
        neighbors = store.get_neighbors("nonexistent")
        assert neighbors == []


class TestSupersedesFiltering:
    def test_excludes_supersedes_by_default(self, temp_graph):
        store = temp_graph
        store.upsert_edge("a", "b", relation="SUPERSEDES", weight_delta=0.5)
        store.upsert_edge("a", "c", relation="co_occurs", weight_delta=0.3)

        neighbors = store.get_neighbors("a")
        ids = [n["memory_id"] for n in neighbors]
        assert "c" in ids
        assert "b" not in ids, "SUPERSEDES should be excluded by default"

    def test_includes_supersedes_when_explicit(self, temp_graph):
        store = temp_graph
        store.upsert_edge("a", "b", relation="SUPERSEDES", weight_delta=0.5)

        neighbors = store.get_neighbors("a", exclude_relations=[])
        ids = [n["memory_id"] for n in neighbors]
        assert "b" in ids, "SUPERSEDES should appear when exclude_relations=[]"


class TestSpreadActivationAdvanced:
    def test_chain_decays_with_distance(self, temp_graph):
        """A->B->C->D: activation decays with distance from seed."""
        store = temp_graph
        store.upsert_edge("a", "b", weight_delta=0.8)
        store.upsert_edge("b", "c", weight_delta=0.8)
        store.upsert_edge("c", "d", weight_delta=0.8)

        result = store.spread_activation(["a"], decay=0.7, max_iter=50)
        assert result["b"] > result["c"] > result["d"], (
            f"Activation should decay: b({result['b']:.4f}) > c({result['c']:.4f}) > d({result['d']:.4f})"
        )

    def test_high_decay_spreads_further(self, temp_graph):
        """Higher decay factor (0.9) spreads activation further than low (0.3)."""
        store = temp_graph
        store.upsert_edge("a", "b", weight_delta=0.5)
        store.upsert_edge("b", "c", weight_delta=0.5)
        store.upsert_edge("c", "d", weight_delta=0.5)

        r_low = store.spread_activation(["a"], decay=0.3, max_iter=50)
        r_high = store.spread_activation(["a"], decay=0.9, max_iter=50)

        # High decay should reach further nodes with more activation
        assert r_high.get("d", 0.0) > r_low.get("d", 0.0), (
            "Higher decay should spread more activation to distant nodes"
        )

    def test_convergence_within_max_iter(self, temp_graph):
        """Algorithm converges before max_iter."""
        store = temp_graph
        for i in range(5):
            store.upsert_edge(f"n{i}", f"n{i+1}", weight_delta=0.5)

        result = store.spread_activation(["n0"], decay=0.7, max_iter=100, threshold=1e-4)
        # All reachable nodes should be present
        assert len(result) == 5

    def test_min_weight_filter(self, temp_graph):
        """Edges below min_weight are excluded from spreading."""
        store = temp_graph
        store.upsert_edge("a", "b", weight_delta=0.9)   # weight = 1.0
        store.upsert_edge("a", "c", weight_delta=0.01)   # weight = 0.51

        # Use min_weight=0.7 to filter out c's edge (weight 0.51 < 0.7)
        result = store.spread_activation(["a"], decay=0.7, min_weight=0.7)
        assert "b" in result
        assert "c" not in result, "Edge below min_weight should not spread"


class TestPageRank:
    def test_star_hub_highest(self, temp_graph):
        """Hub in star topology has highest PageRank."""
        store = temp_graph
        # Register all nodes with ensure_meta (required for get_all_nodes)
        store.ensure_meta("hub")
        for i in range(6):
            store.upsert_edge("hub", f"leaf_{i}", weight_delta=0.5)
            store.ensure_meta(f"leaf_{i}")

        scores = store.pagerank()
        assert "hub" in scores
        assert scores["hub"] == max(scores.values())

    def test_isolated_node_low(self, temp_graph):
        """Isolated node gets lower PageRank than connected nodes."""
        store = temp_graph
        store.ensure_meta("isolated")
        # Add connected nodes with edges
        store.ensure_meta("a")
        store.ensure_meta("b")
        store.ensure_meta("c")
        store.upsert_edge("a", "b", weight_delta=0.5)
        store.upsert_edge("b", "c", weight_delta=0.5)

        scores = store.pagerank()
        # Isolated node should have lower score than connected hub (b)
        assert "isolated" in scores
        assert scores["isolated"] < scores["b"]


class TestGraphRegression:
    def test_ensure_meta_refreshes_zone_for_existing_row(self, temp_graph):
        store = temp_graph
        store.ensure_meta("mem-1", zone="general")

        store.ensure_meta("mem-1", zone="work")

        meta = store.get_meta("mem-1")
        assert meta is not None
        assert meta["zone"] == "work"

    def test_decay_edges_prunes_when_weight_drops_below_threshold(self, temp_graph):
        store = temp_graph
        store.set_edge_weight("a", "b", relation="co_occurs", weight=0.01)

        store.decay_edges(decay_rate=0.01, prune_threshold=0.005)

        assert store.get_edges("a", relation="co_occurs", min_weight=0.0) == []


class TestThreadSafety:
    def test_multithreaded_reads_remain_stable(self, temp_graph):
        """Multiple threads repeatedly reading preserve healthy results."""
        store = temp_graph
        for i in range(10):
            store.upsert_edge(f"n{i}", f"n{(i+1)%10}", weight_delta=0.3)

        errors = []
        unhealthy_stats = []
        empty_neighbors = []

        def reader():
            try:
                for _ in range(50):
                    neighbors = store.get_neighbors("n0")
                    stats = store.stats()
                    if not neighbors:
                        empty_neighbors.append(True)
                    if not stats.get("healthy"):
                        unhealthy_stats.append(stats)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        assert not empty_neighbors, "Concurrent reads should keep returning neighbors for seeded node"
        assert not unhealthy_stats, f"Concurrent reads should keep stats healthy: {unhealthy_stats}"
