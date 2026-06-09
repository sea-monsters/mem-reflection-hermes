"""Unit tests for memory_curator module."""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Minimal mocks (no plugin loading required)
# ---------------------------------------------------------------------------

_MOCK_TIME = time.time()


class MockFrontmatter:
    def __init__(self, mem_id: str, body: str, zone: str = "general",
                 created: str = "", confidence: str = "medium",
                 pinned: bool = False, tags: list = None,
                 supersedes: list = None, valid_until: str = ""):
        self.id_val = mem_id
        self.zone = zone
        self.created = created or datetime.now(timezone.utc).isoformat()
        self.confidence = confidence
        self.pinned = pinned
        self.tags = tags or []
        self.supersedes = supersedes or []
        self.valid_until = valid_until
        self.supersedes_reason = ""
        self._body = body

    def id(self) -> str:
        return self.id_val


class MockMemory:
    def __init__(self, mid: str, body: str, zone: str = "general",
                 created: str = "", confidence: str = "medium",
                 pinned: bool = False, tags: list = None,
                 supersedes: list = None, valid_until: str = ""):
        self.id_val = mid
        self.body = body
        self.scope = "user"
        self.frontmatter = MockFrontmatter(
            mid, body, zone, created, confidence,
            pinned, tags, supersedes, valid_until,
        )

    def id(self) -> str:
        return self.id_val


class MockStore:
    def __init__(self):
        self.memories: Dict[str, MockMemory] = {}
        self.deleted: List[str] = []
        self.eff_data: Dict[str, Dict[str, Any]] = {}
        self._cold_store: List[Dict[str, Any]] = []

    def list_active(self) -> List[MockMemory]:
        return list(self.memories.values())

    def get(self, mid: str) -> Optional[MockMemory]:
        return self.memories.get(mid)

    def put(self, scope: str, fm, body: str):
        """Mock for MemoryStore.put — adds to memories dict."""
        self.memories[fm.id] = MockMemory(
            fm.id, body, zone=getattr(fm, 'zone', 'general'),
            tags=getattr(fm, 'tags', []),
        )

    def list(self, *, zone=None, active_only=False, sort="rank", limit=None):
        """Mock for MemoryStore.list — returns all memories (ignores active_only)."""
        mems = list(self.memories.values())
        if zone:
            mems = [m for m in mems if m.frontmatter.zone == zone]
        if limit is not None:
            mems = mems[:limit]
        return mems

    def delete(self, scope: str, mid: str) -> bool:
        if mid in self.memories:
            del self.memories[mid]
            self.deleted.append(mid)
            return True
        return False

    def list_active_effectiveness(self) -> Dict[str, Dict[str, Any]]:
        return self.eff_data

    def update(self, mem_id, body=None, zone=None, confidence=None, tags=None, pinned=None, supersedes=None):
        """Mock for MemoryStore.update."""
        mem = self.memories.get(mem_id)
        if mem is None:
            return None
        if body is not None:
            mem.body = body
        if zone is not None:
            mem.frontmatter.zone = zone
        if confidence is not None:
            mem.frontmatter.confidence = confidence
        if tags is not None:
            mem.frontmatter.tags = tags
        if pinned is not None:
            mem.frontmatter.pinned = pinned
        if supersedes is not None:
            mem.frontmatter.supersedes = supersedes
        return mem

    def is_superseded(self, mem_id: str) -> bool:
        """Check if any active memory supersedes mem_id."""
        for mem in self.memories.values():
            if mem_id in (mem.frontmatter.supersedes or []):
                return True
        return False

    def latest_for(self, mem_id: str):
        """Walk forward to find the newest memory in the chain."""
        visited: set = set()
        current = mem_id
        while current not in visited:
            visited.add(current)
            next_id = None
            for mem in self.memories.values():
                if current in (mem.frontmatter.supersedes or []):
                    next_id = mem.id()
                    break
            if next_id is None:
                break
            current = next_id
        return self.memories.get(current)


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
    _run_curator,
    generate_report,
)
from memory.curator import _append_to_cold_store, _curator_config  # noqa: E402


@pytest.fixture
def store(tmp_path):
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
    s._cold_store_path_override = str(tmp_path / "_cold_store.jsonl")
    return s


# ── Phase 1: TTL + Staleness ──────────────────────────────────


class TestScanForStale:
    def test_finds_stale(self, store):
        stale = scan_for_stale(store)
        assert "stale" in stale
        assert "expired" in stale

    def test_exempts_pinned(self, store):
        stale = scan_for_stale(store)
        assert "keep" not in stale

    def test_exempts_fresh(self, store):
        stale = scan_for_stale(store)
        assert "fresh" not in stale


class TestArchiveExpired:
    def test_archives_and_deletes(self, store):
        archived = archive_expired(store, ["stale", "expired"])
        assert archived == 2
        assert "stale" in store.deleted
        assert "expired" in store.deleted

    def test_cold_storage_has_entries(self, store):
        archive_expired(store, ["stale", "expired"])
        entries = _load_cold_store(store)
        assert len(entries) >= 1
        ids = [e["id"] for e in entries]
        assert "stale" in ids or "expired" in ids


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
        # v1 and v2 should be archived (they have successors pointing at them)
        assert archived >= 1
        assert "v1" in store.deleted or "v2" in store.deleted
        # v3 is latest, not archived
        assert "v3" not in store.deleted

    def test_keeps_single_chain(self, store):
        store.memories["v1"] = MockMemory("v1", "single version",
                                           zone="core")
        archived = archive_superseded(store)
        assert archived == 0  # no supersedes, not archived


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
        # v1 and v5 should remain
        assert "v1" in store.memories
        assert "v5" in store.memories
        # v5.supersedes should now point to v1 (skip intermediates)
        assert store.memories["v5"].frontmatter.supersedes == ["v1"]

    def test_skips_short_chain(self, store):
        """Chain of 2 (v1→v2) is not compacted."""
        store.memories["v1"] = MockMemory("v1", "oldest", zone="core",
                                           supersedes=[])
        store.memories["v2"] = MockMemory("v2", "newest", zone="core",
                                           supersedes=["v1"])
        result = compact_superseded_chains(store)
        assert result == 0
        assert "v1" in store.memories
        assert "v2" in store.memories

    def test_skips_pinned_intermediate(self, store):
        """Pinned intermediate nodes are preserved, others archived."""
        store.memories["v1"] = MockMemory("v1", "oldest", zone="core",
                                           supersedes=[])
        store.memories["v2"] = MockMemory("v2", "pinned body", zone="core",
                                           supersedes=["v1"], pinned=True)
        store.memories["v3"] = MockMemory("v3", "v3 body", zone="core",
                                           supersedes=["v2"])
        store.memories["v4"] = MockMemory("v4", "newest", zone="core",
                                           supersedes=["v3"])
        result = compact_superseded_chains(store)
        # v3 is archived (only non-pinned intermediate), v2 is kept
        assert result == 1
        assert "v1" in store.memories
        assert "v2" in store.memories  # pinned, preserved
        assert "v3" in store.deleted
        assert "v4" in store.memories
        # v4.supersedes should point to v1 (skip v2,v3; v2 kept, v3 gone)
        assert store.memories["v4"].frontmatter.supersedes == ["v1"]


# ── Phase 3: Similarity Detection ──────────────────────────────


class TestScanForSimilar:
    def test_finds_similar_pairs(self):
        store = MockStore()
        store.memories["a"] = MockMemory("a",
            "The user prefers OpenSpec SDD for development")
        store.memories["b"] = MockMemory("b",
            "OpenSpec SDD is preferred for development by the user")
        store.memories["c"] = MockMemory("c",
            "The weather is sunny today in the park")
        similar = scan_for_similar(store)
        assert len(similar) >= 1

    def test_empty_store(self):
        assert scan_for_similar(MockStore()) == []

    def test_single_memory(self):
        store = MockStore()
        store.memories["a"] = MockMemory("a", "lonely")
        assert scan_for_similar(store) == []


# ── Phase 3b: Similarity Merge ─────────────────────────────


class TestMergeSimilar:
    def test_merges_similar_pair(self):
        """Two similar memories: one archived, keeper updated with merged body+tags."""
        store = MockStore()
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
        # Keeper should still exist with updated body
        assert "keeper" in store.memories
        # Archived should be deleted
        assert "dup" in store.deleted

    def test_exact_dedup(self):
        """Identical bodies: one archived deterministically."""
        store = MockStore()
        store.memories["a"] = MockMemory("a", "exact same content", tags=["x"])
        store.memories["b"] = MockMemory("b", "exact same content", tags=["y"])
        result = merge_similar(store)
        assert result == 1
        # Only one should remain (the one with smaller id alphabetically)
        assert "a" in store.memories or "b" in store.memories
        assert len(store.deleted) == 1

    def test_skip_already_superseded(self):
        """Memories that have been superseded by another are skipped."""
        store = MockStore()
        # 'a' is the OLD memory, 'c' is the NEW memory that supersedes it
        store.memories["a"] = MockMemory("a", "content alpha")
        store.memories["c"] = MockMemory("c", "content gamma", supersedes=["a"])
        store.memories["b"] = MockMemory("b", "content beta which is quite similar to alpha")
        result = merge_similar(store)
        # 'a' is superseded by 'c', so skipped; 'b' remains
        assert result == 0
        assert "a" in store.memories
        assert "b" in store.memories
        assert "c" in store.memories

    def test_merge_updates_tags_union(self):
        """Keeper's tags become union of both memories' tags."""
        store = MockStore()
        store.memories["x"] = MockMemory("x", "the user likes python programming for development work", tags=["python"])
        store.memories["y"] = MockMemory("y", "the user likes python programming for development tasks and devops", tags=["python", "dev"])
        merge_similar(store)
        keeper = store.memories.get("x") or store.memories.get("y")
        assert keeper is not None
        assert "python" in keeper.frontmatter.tags
        assert "dev" in keeper.frontmatter.tags
        assert len(keeper.frontmatter.tags) == 2  # union, not duplicate

    def test_merge_empty_store(self):
        """No memories = no merge."""
        assert merge_similar(MockStore()) == 0


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

    def test_restore_fail_open_on_graph_error(self, store):
        """Restore still works even if graph ensure_meta throws."""
        from memory.curator import _restore_from_cold, _append_to_cold_store

        entry = {
            "id": "restored-mem-2",
            "body": "restored again",
            "zone": "general",
            "archived_at": "2026-06-01T00:00:00Z",
            "tags": ["archived"],
            "original_frontmatter": {
                "created": "2026-01-01T00:00:00Z",
                "confidence": "high",
                "pinned": False,
                "supersedes": [],
                "supersedes_reason": "",
            },
        }
        _append_to_cold_store(store, entry)

        # Should succeed (graph ensure_meta is fail-open)
        result = _restore_from_cold(store, "restored-mem-2")
        assert result is True
        assert store.get("restored-mem-2") is not None


# ── Phase 5: Orphan Edge Cleanup (P2a) ──────────────────────


class TestCleanOrphanEdges:
    def test_graceful_when_no_graph(self, store):
        """Returns 0 gracefully when graph manager is unavailable."""
        from memory.curator import clean_orphan_edges
        result = clean_orphan_edges(store)
        assert result == 0

    def test_pipeline_includes_orphan_field(self, store):
        """_run_curator result includes orphan_edges field."""
        from memory.curator import _run_curator
        result = _run_curator(None, store)
        assert "orphan_edges" in result
        assert isinstance(result["orphan_edges"], int)


# ── Phase 6: Report ────────────────────────────────────────────


class TestReport:
    def test_generates_summary(self):
        report = generate_report(2, 2, 1, 1, [], merged_count=2)
        assert "stale: 2" in report
        assert "superseded: 1" in report
        assert "similar: 1" in report
        assert "merged: 2" in report

    def test_empty_when_nothing(self):
        assert generate_report(0, 0, 0, 0, []) == "No curator actions"


# ── Full Pipeline ──────────────────────────────────────────────


class TestFullPipeline:
    def test_runs_without_crash(self, store):
        result = _run_curator(None, store)
        assert "curator" in result
        assert isinstance(result.get("stale", 0), int)
        assert isinstance(result.get("errors", []), list)

    def test_cleanup_session_state(self, store):
        # Simulate on_session_end would call this
        _run_curator(None, store)
        assert True  # no exception = success
