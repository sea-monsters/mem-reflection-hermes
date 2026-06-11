"""Unit tests for memory_curator module."""
from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tests._helpers import MockFrontmatter, MockMemory, MockStore

# ---------------------------------------------------------------------------
# Minimal mocks imported from _helpers.py
# ---------------------------------------------------------------------------

_MOCK_TIME = time.time()


# Import curator module under test
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from memory.curator import (  # noqa: E402
    scan_for_stale,
    archive_expired,
    archive_superseded,
    compact_superseded_chains,
    scan_for_similar,
    merge_similar,
    _cold_store_path,
    _load_cold_store,
)
from memory.curator import _append_to_cold_store, _curator_config  # noqa: E402


@pytest.fixture
def store(temp_dir):
    s = MockStore()
    s.memories["keep"] = MockMemory("keep", "pinned", pinned=True, tags=["keep"])
    s.memories["fresh"] = MockMemory("fresh", "recent", confidence="high")
    s.memories["stale"] = MockMemory("stale", "old and unused", confidence="low")
    s.memories["expired"] = MockMemory("expired", "past valid_until",
                                        valid_until="2020-01-01T00:00:00Z")
    s.eff_data = {
        "fresh": {"last_accessed": _MOCK_TIME, "effectiveness": 0.9},
        "stale": {"last_accessed": _MOCK_TIME - 100 * 86400, "effectiveness": 0.05},
    }
    s._cold_store_path_override = str(temp_dir / "_cold_store.jsonl")
    return s


def _isolated_store(temp_dir: Path) -> MockStore:
    s = MockStore()
    s._cold_store_path_override = str(temp_dir / "_cold_store.jsonl")
    return s


# ── Phase 1: TTL + Staleness ──────────────────────────────────


class TestScanForStale:
    def test_finds_stale(self, store):
        stale = scan_for_stale(store)
        assert "stale" in stale
        assert "expired" in stale
        assert "keep" not in stale
        assert "fresh" not in stale


class TestArchiveExpired:
    def test_archives_and_deletes(self, store):
        archived = archive_expired(store, ["stale", "expired"])
        assert archived == 2
        assert "stale" in store.deleted
        assert "expired" in store.deleted


# ── Phase 2: Supersedes Archiving ──────────────────────────────


class TestArchiveSuperseded:
    def test_archives_deep_chains(self, store):
        store.memories["v1"] = MockMemory("v1", "version 1", zone="core",
                                           supersedes=["orig"])
        store.memories["v2"] = MockMemory("v2", "version 2", zone="core",
                                           supersedes=["v1"])
        store.memories["v3"] = MockMemory("v3", "version 3", zone="core",
                                           supersedes=["v2"])
        archived = archive_superseded(store)
        assert archived >= 1
        assert "v3" not in store.deleted


# ── Phase 2b: Chain Compaction ─────────────────────────────


class TestCompactChains:
    def test_compacts_long_chain(self, store):
        """v1→v2→v3→v4→v5: v2,v3,v4 archived, v5.supersedes→[v1]."""
        store.memories["v1"] = MockMemory("v1", "oldest", zone="core",
                                           supersedes=[])
        store.memories["v2"] = MockMemory("v2", "v2 body", zone="core",
                                           supersedes=["v1"])
        store.memories["v3"] = MockMemory("v3", "v3 body", zone="core",
                                           supersedes=["v2"])
        store.memories["v4"] = MockMemory("v4", "v4 body", zone="core",
                                           supersedes=["v3"])
        store.memories["v5"] = MockMemory("v5", "newest", zone="core",
                                           supersedes=["v4"])
        result = compact_superseded_chains(store)
        assert result == 3  # v2, v3, v4
        assert "v1" in store.memories
        assert "v5" in store.memories
        assert store.memories["v5"].frontmatter.supersedes == ["v1"]


# ── Phase 3: Similarity Detection ──────────────────────────────


class TestScanForSimilar:
    def test_finds_similar_pairs(self, temp_dir):
        store = _isolated_store(temp_dir)
        store.memories["a"] = MockMemory("a",
            "The user prefers OpenSpec SDD for development")
        store.memories["b"] = MockMemory("b",
            "OpenSpec SDD is preferred for development by the user")
        store.memories["c"] = MockMemory("c",
            "The weather is sunny today in the park")
        similar = scan_for_similar(store)
        assert len(similar) >= 1


# ── Phase 3b: Similarity Merge ─────────────────────────────


class TestMergeSimilar:
    def test_merges_similar_pair(self, temp_dir):
        """Two similar memories: one archived, keeper updated with merged body+tags."""
        store = _isolated_store(temp_dir)
        store.memories["keeper"] = MockMemory(
            "keeper", "The user prefers OpenSpec SDD for development workflow and design patterns",
            tags=["sdd"],
        )
        store.memories["dup"] = MockMemory(
            "dup", "The user prefers OpenSpec SDD for development workflow design",
            tags=["sdd", "workflow"],
        )
        result = merge_similar(store)
        assert result == 1
        assert "keeper" in store.memories
        assert "dup" in store.deleted


# ── Phase 4: Cold Storage ──────────────────────────────────────


class TestColdStorage:
    def test_append_and_load(self, store):
        entry = {"id": "test1", "body": "test", "archived_at": "2026-01-01T00:00:00Z"}
        ok = _append_to_cold_store(store, entry)
        assert ok
        entries = _load_cold_store(store)
        ids = [e["id"] for e in entries]
        assert "test1" in ids


# ── P2b: Cold Restore Graph Rebuild ─────────────────────────


class TestRestoreGraphRebuild:
    def test_restore_succeeds_without_graph(self, store):
        """_restore_from_cold succeeds even when graph manager unavailable."""
        from memory.curator import _restore_from_cold, _append_to_cold_store

        entry = {
            "id": "restored-mem",
            "body": "restored content",
            "zone": "general",
            "archived_at": "2026-06-01T00:00:00Z",
            "tags": ["archived", "cold"],
            "original_frontmatter": {
                "created": "2026-01-01T00:00:00Z",
                "confidence": "medium",
                "pinned": False,
                "supersedes": [],
                "supersedes_reason": "",
            },
        }
        _append_to_cold_store(store, entry)

        result = _restore_from_cold(store, "restored-mem")
        assert result is True

        # Memory should be back in active store
        mem = store.get("restored-mem")
        assert mem is not None
        assert mem.body == "restored content"


# ── Phase 5: Orphan Edge Cleanup (P2a) ──────────────────────


class TestCleanOrphanEdges:
    def test_graceful_when_no_graph(self, store):
        """Returns 0 gracefully when graph manager is unavailable."""
        from memory.curator import clean_orphan_edges
        result = clean_orphan_edges(store)
        assert result == 0


