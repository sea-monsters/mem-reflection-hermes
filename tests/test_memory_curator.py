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


# Import curator module under test
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from memory_curator import (  # noqa: E402
    scan_for_stale,
    archive_expired,
    archive_superseded,
    scan_for_similar,
    _cold_store_path,
    _load_cold_store,
    _run_curator,
    generate_report,
)
from memory_curator import _append_to_cold_store, _curator_config  # noqa: E402


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


# ── Phase 4: Cold Storage ──────────────────────────────────────


class TestColdStorage:
    def test_append_and_load(self, store):
        entry = {"id": "test1", "body": "test", "archived_at": "2026-01-01T00:00:00Z"}
        ok = _append_to_cold_store(store, entry)
        assert ok
        entries = _load_cold_store(store)
        ids = [e["id"] for e in entries]
        assert "test1" in ids


# ── Phase 5: Report ────────────────────────────────────────────


class TestReport:
    def test_generates_summary(self):
        report = generate_report(2, 2, 1, 1, [])
        assert "stale: 2" in report
        assert "superseded: 1" in report
        assert "similar: 1" in report

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
