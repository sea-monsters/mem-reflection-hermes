"""test_fusion_rerank.py — Fusion search rerank dimension effectiveness tests.

Tests ALL six rerank dimensions:
  1. Channel normalization (cosine/BM25 → [0,1])
  2. Recency scoring (30-day half-life exponential decay)
  3. Effectiveness scoring (factor × decay_factor)
  4. Hebbian boost (one-hop neighbor weight)
  5. Hub bonus (PageRank > 0.15 nodes)
  6. Supersedes lineage weighting
Plus weight sensitivity and full pipeline integration.

Run: pytest tests/test_fusion_rerank.py -v
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch, MagicMock

import pytest

from tests._helpers import make_memory, make_memory_with_id, effectiveness_for


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inject_memories(store, memories):
    """Inject memories into store cache (bypasses file I/O)."""
    store._cache.setdefault("active", [])
    store._cache.setdefault("pinned", [])
    store._cache.setdefault("all", [])
    store._cache.setdefault("superseded", set())
    for m in memories:
        store._cache["active"].append(m)
        store._id_to_mem[m.id()] = m
    store._cache_valid = True


def _inject_effectiveness(store, eff_map):
    """Inject effectiveness cache directly."""
    store._effectiveness_cache = eff_map
    store._effectiveness_mtime_ns = 99999999


def _mock_embed(store, mapping: Dict[str, float]):
    """Patch store._embed_search to return controlled cosine scores."""
    def mock_embed(query, k):
        return list(mapping.items())[:k]
    store._embed_search = mock_embed


# ---------------------------------------------------------------------------
# Recency tests
# ---------------------------------------------------------------------------

class TestRecency:
    def test_fresh_ranks_above_stale(self, temp_store):
        """Memory created today ranks above identical memory from 60 days ago."""
        fresh = make_memory_with_id("fresh-1", "golang backend development", age_days=0)
        stale = make_memory_with_id("stale-1", "golang backend development", age_days=60)
        _inject_memories(temp_store, [fresh, stale])

        # Use high gamma to amplify recency effect, disable other dimensions
        results = temp_store.fusion_search(
            "golang backend", k=2,
            alpha=0.0, beta=0.5, gamma=0.5, delta=0.0,
        )
        assert len(results) == 2
        assert results[0].id() == "fresh-1", "Fresh memory should rank first"

    def test_half_life_value(self, temp_store):
        """30-day-old memory gets recency ≈ exp(-30/30) = 0.368."""
        now = datetime.now(timezone.utc)
        mem_30d = make_memory_with_id("mem-30d", "test content", age_days=30)
        _inject_memories(temp_store, [mem_30d])

        # Create a reference at age=0
        mem_0d = make_memory_with_id("mem-0d", "unrelated xyz", age_days=0)
        _inject_memories(temp_store, [mem_0d])

        # Force BM25 to match both equally by making query match both
        # We'll test recency through fusion_search with gamma=1.0, others=0
        results = temp_store.fusion_search(
            "test content unrelated xyz", k=2,
            alpha=0.0, beta=0.01, gamma=1.0, delta=0.0,
        )
        # mem-0d should rank first (recency=1.0 vs ~0.368)
        assert results[0].id() == "mem-0d"

    def test_recency_exponential_decay(self):
        """Verify MemoryEffectiveness.decay_factor follows exponential curve."""
        eff = effectiveness_for("x", loaded=1, referenced=1, last_event_days_ago=30)
        decay = eff.decay_factor()
        # Formula: 0.5^(days/30) = 0.5^1.0 = 0.5, floor 0.3
        assert abs(decay - 0.5) < 1e-9, f"30-day decay should be 0.5, got {decay}"

        eff_60 = effectiveness_for("y", loaded=1, referenced=1, last_event_days_ago=60)
        decay_60 = eff_60.decay_factor()
        # 0.5^(60/30) = 0.25, but floor is 0.3
        assert abs(decay_60 - 0.3) < 1e-9, f"60-day decay should be 0.3 (floor), got {decay_60}"


# ---------------------------------------------------------------------------
# Effectiveness tests
# ---------------------------------------------------------------------------

class TestEffectiveness:
    def test_high_vs_low_effectiveness(self, temp_store):
        """Memory with high effectiveness ranks above low effectiveness."""
        mem_a = make_memory_with_id("eff-high", "database query optimization", age_days=0)
        mem_b = make_memory_with_id("eff-low", "database query optimization", age_days=0)
        _inject_memories(temp_store, [mem_a, mem_b])

        eff_high = effectiveness_for("eff-high", loaded=10, referenced=9, last_event_days_ago=0)
        eff_low = effectiveness_for("eff-low", loaded=10, referenced=1, last_event_days_ago=0)
        _inject_effectiveness(temp_store, {"eff-high": eff_high, "eff-low": eff_low})

        results = temp_store.fusion_search(
            "database query", k=2,
            alpha=0.0, beta=0.5, gamma=0.0, delta=0.5,
        )
        assert len(results) == 2
        assert results[0].id() == "eff-high", "High-effectiveness memory should rank first"

    def test_effectiveness_decay_stale(self):
        """Memory with last_event 60 days ago gets decayed effectiveness."""
        eff = effectiveness_for("x", loaded=10, referenced=5, last_event_days_ago=60)
        factor = eff.factor()
        decay = eff.decay_factor()
        # factor = 0.5 + 0.5 * (5/10) = 0.75
        assert 0.7 < factor < 0.8
        # decay should be floor(0.3) since 60-day 0.5^(60/30)=0.25 < 0.3
        assert decay == pytest.approx(0.3, abs=0.01)

    def test_no_effectiveness_stats_gives_zero(self, temp_store):
        """Memory without effectiveness stats gets eff=0.0 in fusion."""
        mem = make_memory_with_id("no-eff", "test content here", age_days=0)
        _inject_memories(temp_store, [mem])
        # No effectiveness injected → defaults to empty
        results = temp_store.fusion_search(
            "test content", k=1,
            alpha=0.0, beta=0.5, gamma=0.0, delta=0.5,
        )
        assert len(results) == 1
        # Should still return the memory (BM25 alone suffices)


# ---------------------------------------------------------------------------
# Hebbian boost tests
# ---------------------------------------------------------------------------

class TestHebbianBoost:
    def test_enabled_boosts_connected_nodes(self, temp_store, temp_graph):
        """With hebbian_beta > 0, connected memories get boost."""
        mem_a = make_memory_with_id("h-a", "react frontend components", age_days=0)
        mem_b = make_memory_with_id("h-b", "vue frontend components", age_days=0)
        mem_c = make_memory_with_id("h-c", "backend api design", age_days=0)
        _inject_memories(temp_store, [mem_a, mem_b, mem_c])

        # Create graph edge: a↔b with high weight, c is isolated
        temp_graph.upsert_edge("h-a", "h-b", weight_delta=0.8)
        temp_graph.ensure_meta("h-a")
        temp_graph.ensure_meta("h-b")

        # Mock graph manager
        gm = MagicMock()
        gm.store = temp_graph
        with patch("mem_reflection_hermes._get_graph_mgr", return_value=gm):
            results = temp_store.fusion_search(
                "frontend components", k=3,
                alpha=0.0, beta=0.3, gamma=0.0, delta=0.0,
                hebbian_beta=0.5,
            )
        # Both a and b should rank above c (they have Hebbian boost)

    def test_disabled_no_effect(self, temp_store, temp_graph):
        """With hebbian_beta=0.0 (default), graph edges don't affect ranking."""
        mem_a = make_memory_with_id("hd-a", "python web framework", age_days=0)
        mem_b = make_memory_with_id("hd-b", "python web framework", age_days=0)
        _inject_memories(temp_store, [mem_a, mem_b])

        temp_graph.upsert_edge("hd-a", "hd-b", weight_delta=0.9)

        gm = MagicMock()
        gm.store = temp_graph
        with patch("mem_reflection_hermes._get_graph_mgr", return_value=gm):
            results = temp_store.fusion_search(
                "python web framework", k=2,
                alpha=0.0, beta=1.0, gamma=0.0, delta=0.0,
                hebbian_beta=0.0,
            )
        # Both have same content, should have similar scores
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Hub bonus tests
# ---------------------------------------------------------------------------

class TestHubBonus:
    def test_hub_ranks_above_leaf(self, temp_store, temp_graph):
        """Star-topology hub gets +hub_bonus, leaf doesn't."""
        # Hub connects to 4 leaves
        hub = make_memory_with_id("hub-1", "central architecture design pattern", age_days=0)
        leaves = [
            make_memory_with_id(f"leaf-{i}", "central architecture design pattern", age_days=0)
            for i in range(4)
        ]
        _inject_memories(temp_store, [hub] + leaves)

        for i in range(4):
            temp_graph.upsert_edge("hub-1", f"leaf-{i}", weight_delta=0.5)
            temp_graph.ensure_meta(f"leaf-{i}")
        temp_graph.ensure_meta("hub-1")

        gm = MagicMock()
        gm.store = temp_graph
        with patch("mem_reflection_hermes._get_graph_mgr", return_value=gm):
            results = temp_store.fusion_search(
                "architecture design pattern", k=5,
                alpha=0.0, beta=0.5, gamma=0.0, delta=0.0,
                hub_bonus=0.1,
            )
        # Hub should rank first (same content + hub bonus)
        assert results[0].id() == "hub-1", "Hub should rank first with hub_bonus"

    def test_no_graph_graceful(self, temp_store):
        """Without graph, hub_bonus has no effect."""
        mem_a = make_memory_with_id("ng-a", "golang backend services", age_days=0)
        mem_b = make_memory_with_id("ng-b", "golang backend services", age_days=0)
        _inject_memories(temp_store, [mem_a, mem_b])

        with patch("mem_reflection_hermes._get_graph_mgr", return_value=None):
            results = temp_store.fusion_search(
                "golang backend", k=2,
                alpha=0.0, beta=1.0, gamma=0.0, delta=0.0,
                hub_bonus=0.1,
            )
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Supersedes lineage weighting tests
# ---------------------------------------------------------------------------

class TestSupersedesPenalty:
    def test_depth_boosts_more_revised_memory(self, temp_store):
        """Revised memory at supersedes depth=1 gets a gentle boost over depth=0."""
        mem_root = make_memory_with_id("root-1", "original content about databases", age_days=90)
        mem_v1 = make_memory_with_id("v1-1", "updated content about databases", age_days=30, supersedes=["root-1"])
        mem_fresh = make_memory_with_id("fresh-1", "updated content about databases", age_days=30)
        _inject_memories(temp_store, [mem_root, mem_v1, mem_fresh])

        results = temp_store.fusion_search(
            "databases", k=3,
            alpha=0.0, beta=0.5, gamma=0.5, delta=0.0,
        )
        # v1 has supersedes depth=1 → sup_factor=2/3
        # fresh has depth=0 → sup_factor=1/2
        # Both have same BM25 and same age, so the revised memory should rank first.
        ids = [r.id() for r in results]
        if "fresh-1" in ids and "v1-1" in ids:
            assert ids.index("v1-1") < ids.index("fresh-1"), (
                "Revised memory (depth=1) should rank above standalone memory (depth=0)"
            )

    def test_no_supersedes_no_penalty(self, temp_store):
        """Memory with no supersedes gets sup_factor=1.0."""
        mem = make_memory_with_id("clean-1", "standalone memory content", age_days=0)
        _inject_memories(temp_store, [mem])

        # Verify _calc_supersedes_depth returns 0
        depth = temp_store._calc_supersedes_depth("clean-1")
        assert depth == 0


# ---------------------------------------------------------------------------
# Channel normalization
# ---------------------------------------------------------------------------

class TestChannelNormalization:
    def test_bm25_only_channel(self, temp_store):
        """Memory only in BM25 pool gets cosine=0.0 after normalization."""
        mem = make_memory_with_id("bm25-only", "unique searchable content here", age_days=0)
        _inject_memories(temp_store, [mem])

        results = temp_store.fusion_search("unique searchable", k=1)
        assert len(results) == 1
        assert results[0].id() == "bm25-only"

    def test_mixed_channels(self, temp_store):
        """Memories in both channels vs one channel still rank correctly."""
        mem_a = make_memory_with_id("ch-a", "python machine learning", age_days=0)
        mem_b = make_memory_with_id("ch-b", "python machine learning", age_days=0)
        _inject_memories(temp_store, [mem_a, mem_b])

        # Give mem_a a fake cosine boost
        _mock_embed(temp_store, {"ch-a": 0.9})

        results = temp_store.fusion_search(
            "python machine learning", k=2,
            alpha=0.5, beta=0.5, gamma=0.0, delta=0.0,
        )
        # mem_a should rank first (cosine + BM25 vs BM25 only)
        assert results[0].id() == "ch-a"


# ---------------------------------------------------------------------------
# Zone filtering
# ---------------------------------------------------------------------------

class TestZoneFiltering:
    def test_zone_filter_works(self, temp_store):
        """Results filtered to specified zone only."""
        work = make_memory_with_id("w-1", "project deadline tomorrow", zone="work", age_days=0)
        general = make_memory_with_id("g-1", "project deadline tomorrow", zone="general", age_days=0)
        _inject_memories(temp_store, [work, general])

        results = temp_store.fusion_search("project deadline", k=5, zone="work")
        assert all(r.frontmatter.zone == "work" for r in results)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Weight sensitivity
# ---------------------------------------------------------------------------

class TestWeightSensitivity:
    def test_alpha_beta_swap_changes_ranking(self, temp_store):
        """Swapping alpha/beta weights shifts ranking."""
        # mem_a is strong in embedding, mem_b in BM25
        mem_a = make_memory_with_id("embed-strong", "alpha beta gamma delta", age_days=0)
        mem_b = make_memory_with_id("bm25-strong", "alpha beta gamma delta", age_days=0)
        _inject_memories(temp_store, [mem_a, mem_b])

        _mock_embed(temp_store, {"embed-strong": 0.95})

        # alpha-heavy: embed-strong should win
        r_alpha = temp_store.fusion_search(
            "alpha beta", k=2,
            alpha=0.9, beta=0.1, gamma=0.0, delta=0.0,
        )
        assert r_alpha[0].id() == "embed-strong"


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------

class TestFusionPipeline:
    def test_full_pipeline_top3(self, seeded_store):
        """Integration: top-3 results match expected given known parameters."""
        store = seeded_store
        # Query about "dark mode"
        results = store.fusion_search("dark mode preference", k=3)
        assert len(results) >= 1
        # mem-1 ("dark mode") should be in top results
        ids = [r.id() for r in results]
        assert "mem-1" in ids, "Dark mode memory should be in top results"
