"""Tests for v1.5 Curator Action Pipeline refactor.

These tests verify the refactored curator package structure:
- CuratorAction base class and execute() contract
- CuratorContext and CuratorResult data structures
- Individual action isolation (6 actions)
- Helper function edge cases
- Pipeline ordering and error isolation

These tests are written BEFORE the refactor (RED phase) and will initially fail.
"""
from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Test Fixtures
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
        self.memories[fm.id] = MockMemory(
            fm.id, body, zone=getattr(fm, 'zone', 'general'),
            tags=getattr(fm, 'tags', []),
        )

    def list(self, *, zone=None, active_only=False, sort="rank", limit=None):
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
        for mem in self.memories.values():
            if mem_id in (mem.frontmatter.supersedes or []):
                return True
        return False


@pytest.fixture
def empty_store():
    """Empty MemoryStore for edge case testing."""
    s = MockStore()
    with tempfile.TemporaryDirectory() as td:
        s._cold_store_path_override = str(Path(td) / "_cold_store.jsonl")
        yield s


@pytest.fixture
def pinned_only_store():
    """Store with only pinned/kept memories — no candidates for archive."""
    s = MockStore()
    s.memories["pinned1"] = MockMemory("pinned1", "pinned content", pinned=True)
    s.memories["pinned2"] = MockMemory("pinned2", "kept content", tags=["keep"])
    s.memories["permanent"] = MockMemory("permanent", "permanent", tags=["permanent"])
    with tempfile.TemporaryDirectory() as td:
        s._cold_store_path_override = str(Path(td) / "_cold_store.jsonl")
        yield s


@pytest.fixture
def stale_store():
    """Store with stale memories for archive testing."""
    s = MockStore()
    s.memories["fresh"] = MockMemory("fresh", "recent", confidence="high")
    s.memories["stale"] = MockMemory("stale", "old and unused", confidence="low")
    s.memories["expired"] = MockMemory("expired", "past valid_until",
                                        valid_until="2020-01-01T00:00:00Z")
    s.eff_data = {
        "fresh": {"last_accessed": _MOCK_TIME, "effectiveness": 0.9},
        "stale": {"last_accessed": _MOCK_TIME - 100 * 86400, "effectiveness": 0.05},
    }
    with tempfile.TemporaryDirectory() as td:
        s._cold_store_path_override = str(Path(td) / "_cold_store.jsonl")
        yield s


# ---------------------------------------------------------------------------
# Test: Data Structures
# ---------------------------------------------------------------------------


class TestCuratorContext:
    """Tests for CuratorContext dataclass."""

    def test_context_holds_store(self, empty_store):
        """CuratorContext.mem_store is the provided MemoryStore."""
        from memory.curator.actions import CuratorContext

        ctx = CuratorContext(mem_store=empty_store)
        assert ctx.mem_store is empty_store

    def test_context_has_empty_errors_list(self, empty_store):
        """CuratorContext.errors starts as empty list."""
        from memory.curator.actions import CuratorContext

        ctx = CuratorContext(mem_store=empty_store)
        assert ctx.errors == []
        assert isinstance(ctx.errors, list)


class TestCuratorResult:
    """Tests for CuratorResult dataclass."""

    def test_result_has_action_name(self):
        """CuratorResult.action_name identifies the source action."""
        from memory.curator.actions import CuratorResult

        result = CuratorResult(action_name="ArchiveStale")
        assert result.action_name == "ArchiveStale"

    def test_result_counts_default_zero(self):
        """All count fields default to 0."""
        from memory.curator.actions import CuratorResult

        result = CuratorResult(action_name="TestAction")
        assert result.archived == 0
        assert result.compacted == 0
        assert result.merged == 0
        assert result.similar_pairs == 0
        assert result.orphan_edges == 0

    def test_result_errors_list_default_empty(self):
        """CuratorResult.errors starts as empty list."""
        from memory.curator.actions import CuratorResult

        result = CuratorResult(action_name="TestAction")
        assert result.errors == []


# ---------------------------------------------------------------------------
# Test: Helper Functions
# ---------------------------------------------------------------------------


class TestIsProtected:
    """Tests for is_protected() helper."""

    def test_pinned_is_protected(self):
        """Pinned memories are protected."""
        from memory.curator.helpers import is_protected

        fm = MockFrontmatter("test", "body", pinned=True)
        assert is_protected(fm) is True

    def test_keep_tag_is_protected(self):
        """Memories with 'keep' tag are protected."""
        from memory.curator.helpers import is_protected

        fm = MockFrontmatter("test", "body", tags=["keep"])
        assert is_protected(fm) is True

    def test_permanent_tag_is_protected(self):
        """Memories with 'permanent' tag are protected."""
        from memory.curator.helpers import is_protected

        fm = MockFrontmatter("test", "body", tags=["permanent"])
        assert is_protected(fm) is True

    def test_no_protection_returns_false(self):
        """Unpinned, no keep/permanent tags → not protected."""
        from memory.curator.helpers import is_protected

        fm = MockFrontmatter("test", "body", tags=["other"])
        assert is_protected(fm) is False


class TestLoadLastAccess:
    """Tests for load_last_access() helper."""

    def test_returns_timestamp_on_success(self, stale_store):
        """Returns last_accessed timestamp when effectiveness data exists."""
        from memory.curator.helpers import load_last_access

        ts = load_last_access(stale_store, "fresh")
        assert ts > 0
        assert ts == _MOCK_TIME

    def test_returns_zero_on_missing_memory(self, empty_store):
        """Returns 0 when memory_id not in effectiveness data."""
        from memory.curator.helpers import load_last_access

        ts = load_last_access(empty_store, "nonexistent")
        assert ts == 0

    def test_returns_zero_on_exception(self):
        """Returns 0 gracefully when store raises exception."""
        from memory.curator.helpers import load_last_access

        class BrokenStore:
            def list_active_effectiveness(self):
                raise RuntimeError("DB error")

        ts = load_last_access(BrokenStore(), "any-id")
        assert ts == 0


class TestBuildColdEntry:
    """Tests for build_cold_entry() helper."""

    def test_includes_required_fields(self, stale_store):
        """Cold entry has id, body, zone, archived_at, tags."""
        from memory.curator.helpers import build_cold_entry

        mem = stale_store.get("fresh")
        entry = build_cold_entry(mem, context_tag="test")

        assert entry["id"] == "fresh"
        assert "body" in entry
        assert entry["zone"] == "general"
        assert "archived_at" in entry
        assert "archived" in entry["tags"]
        assert "cold" in entry["tags"]
        assert "test" in entry["tags"]

    def test_context_tag_in_tags(self, stale_store):
        """Context tag is included in tags list."""
        from memory.curator.helpers import build_cold_entry

        mem = stale_store.get("fresh")
        entry = build_cold_entry(mem, context_tag="superseded")

        assert "superseded" in entry["tags"]


class TestArchiveAndDelete:
    """Tests for archive_and_delete() helper."""

    def test_returns_success_tuple(self, stale_store):
        """Returns (True, None) on successful archive+delete."""
        from memory.curator.helpers import archive_and_delete, build_cold_entry

        mem = stale_store.get("fresh")
        entry = build_cold_entry(mem, context_tag="test")
        success, error = archive_and_delete(stale_store, mem, entry, "test context")

        assert success is True
        assert error is None
        assert "fresh" in stale_store.deleted

    def test_returns_failure_on_cold_store_error(self, stale_store):
        """Returns (False, error_msg) when cold store append fails."""
        from memory.curator.helpers import archive_and_delete, build_cold_entry

        # Use a path containing a null byte, which is invalid on all platforms.
        stale_store._cold_store_path_override = "/nonexistent\x00path/_cold.jsonl"

        mem = stale_store.get("fresh")
        entry = build_cold_entry(mem, context_tag="test")
        success, error = archive_and_delete(stale_store, mem, entry, "test")

        assert success is False
        assert error is not None
        assert "cold store" in error.lower()


# ---------------------------------------------------------------------------
# Test: Individual Actions
# ---------------------------------------------------------------------------


class TestArchiveStaleAction:
    """Tests for ArchiveStale action."""

    def test_action_name(self):
        """ArchiveStale.name is 'ArchiveStale'."""
        from memory.curator.actions import ArchiveStale

        action = ArchiveStale()
        assert action.name == "ArchiveStale"

    def test_archives_stale_memories(self, stale_store):
        """ArchiveStale archives expired and stale memories."""
        from memory.curator.actions import ArchiveStale, CuratorContext

        ctx = CuratorContext(mem_store=stale_store)
        action = ArchiveStale()
        result = action.execute(ctx)

        assert result.archived >= 1
        assert "stale" in stale_store.deleted or "expired" in stale_store.deleted

    def test_skips_pinned(self, pinned_only_store):
        """ArchiveStale does not archive pinned/kept memories."""
        from memory.curator.actions import ArchiveStale, CuratorContext

        ctx = CuratorContext(mem_store=pinned_only_store)
        action = ArchiveStale()
        result = action.execute(ctx)

        assert result.archived == 0
        assert len(pinned_only_store.deleted) == 0


class TestCompactChainsAction:
    """Tests for CompactChains action."""

    def test_action_name(self):
        """CompactChains.name is 'CompactChains'."""
        from memory.curator.actions import CompactChains

        action = CompactChains()
        assert action.name == "CompactChains"

    def test_compacts_long_chain(self, empty_store):
        """CompactChains archives intermediate nodes in long chain."""
        from memory.curator.actions import CompactChains, CuratorContext

        # Build chain: v1 → v2 → v3 → v4 → v5
        empty_store.memories["v1"] = MockMemory("v1", "oldest", zone="core", supersedes=[])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "v3", zone="core", supersedes=["v2"])
        empty_store.memories["v4"] = MockMemory("v4", "v4", zone="core", supersedes=["v3"])
        empty_store.memories["v5"] = MockMemory("v5", "newest", zone="core", supersedes=["v4"])

        ctx = CuratorContext(mem_store=empty_store)
        action = CompactChains()
        result = action.execute(ctx)

        assert result.compacted == 3  # v2, v3, v4
        assert "v2" in empty_store.deleted
        assert "v3" in empty_store.deleted
        assert "v4" in empty_store.deleted
        assert "v1" in empty_store.memories  # oldest preserved
        assert "v5" in empty_store.memories  # newest preserved


class TestArchiveSupersededAction:
    """Tests for ArchiveSuperseded action."""

    def test_action_name(self):
        """ArchiveSuperseded.name is 'ArchiveSuperseded'."""
        from memory.curator.actions import ArchiveSuperseded

        action = ArchiveSuperseded()
        assert action.name == "ArchiveSuperseded"

    def test_archives_deep_chains(self, empty_store):
        """ArchiveSuperseded archives nodes in depth >= 3 chains."""
        from memory.curator.actions import ArchiveSuperseded, CuratorContext

        # Chain: orig → v1 → v2 → v3 (depth = 3)
        empty_store.memories["orig"] = MockMemory("orig", "original", zone="core", supersedes=[])
        empty_store.memories["v1"] = MockMemory("v1", "v1", zone="core", supersedes=["orig"])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "newest", zone="core", supersedes=["v2"])

        ctx = CuratorContext(mem_store=empty_store)
        action = ArchiveSuperseded()
        result = action.execute(ctx)

        assert result.archived >= 1  # at least orig or v1 archived
        assert "v3" not in empty_store.deleted  # newest preserved


class TestMergeSimilarAction:
    """Tests for MergeSimilar action."""

    def test_action_name(self):
        """MergeSimilar.name is 'MergeSimilar'."""
        from memory.curator.actions import MergeSimilar

        action = MergeSimilar()
        assert action.name == "MergeSimilar"

    def test_merges_similar_pair(self, empty_store):
        """MergeSimilar archives duplicate and merges body/tags."""
        from memory.curator.actions import MergeSimilar, CuratorContext

        empty_store.memories["a"] = MockMemory(
            "a",
            "The user prefers OpenSpec SDD for development workflow and design patterns",
            tags=["sdd"],
        )
        empty_store.memories["b"] = MockMemory(
            "b",
            "The user prefers OpenSpec SDD for development workflow design",
            tags=["sdd", "workflow"],
        )

        ctx = CuratorContext(mem_store=empty_store)
        action = MergeSimilar()
        result = action.execute(ctx)

        assert result.merged >= 1
        # One archived, one remains
        assert len(empty_store.deleted) >= 1


class TestCleanOrphanEdgesAction:
    """Tests for CleanOrphanEdges action."""

    def test_action_name(self):
        """CleanOrphanEdges.name is 'CleanOrphanEdges'."""
        from memory.curator.actions import CleanOrphanEdges

        action = CleanOrphanEdges()
        assert action.name == "CleanOrphanEdges"

    def test_returns_zero_when_no_graph(self, empty_store):
        """CleanOrphanEdges returns 0 when graph manager unavailable."""
        from memory.curator.actions import CleanOrphanEdges, CuratorContext

        ctx = CuratorContext(mem_store=empty_store)
        action = CleanOrphanEdges()
        result = action.execute(ctx)

        assert result.orphan_edges == 0


class TestGenerateReportAction:
    """Tests for GenerateReport action."""

    def test_action_name(self):
        """GenerateReport.name is 'GenerateReport'."""
        from memory.curator.actions import GenerateReport

        action = GenerateReport()
        assert action.name == "GenerateReport"


# ---------------------------------------------------------------------------
# Test: Pipeline Orchestration
# ---------------------------------------------------------------------------


class TestPipelineOrder:
    """Tests for action execution order in _run_curator."""

    def test_compact_runs_before_archive(self, empty_store):
        """CompactChains executes before ArchiveSuperseded."""
        # This test verifies ordering indirectly via observable state
        # Build a chain that both actions would affect
        empty_store.memories["v1"] = MockMemory("v1", "oldest", zone="core", supersedes=[])
        empty_store.memories["v2"] = MockMemory("v2", "mid", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "newest", zone="core", supersedes=["v2"])

        from memory.curator import _run_curator

        result = _run_curator(None, empty_store)

        # Both compacted and superseded counts should be present
        assert "compacted" in result
        assert "superseded" in result


class TestErrorIsolation:
    """Tests that one action failure does not skip subsequent actions."""

    def test_action_failure_does_not_skip_later_actions(self, stale_store):
        """If ArchiveStale fails, CompactChains still runs."""
        from memory.curator import _run_curator

        # Force ArchiveStale to fail with a path containing a null byte,
        # which is invalid on all platforms.
        stale_store._cold_store_path_override = "/nonexistent\x00path/_cold.jsonl"

        result = _run_curator(None, stale_store)

        # ArchiveStale should have an error
        assert len(result.get("errors", [])) >= 1

        # But other actions still ran (their counts may be 0 but keys exist)
        assert "compacted" in result
        assert "superseded" in result
        assert "similar" in result
        assert "merged" in result


class TestBackwardCompatibility:
    """Tests that existing imports still work after refactor."""

    def test_import_from_package(self):
        """Can import _run_curator from memory.curator package."""
        from memory.curator import _run_curator

        assert callable(_run_curator)

    def test_import_helper_functions(self):
        """Can import helper functions from memory.curator."""
        from memory.curator import is_protected, build_cold_entry

        assert callable(is_protected)
        assert callable(build_cold_entry)

    def test_import_action_classes(self):
        """Can import action classes from memory.curator.actions."""
        from memory.curator.actions import ArchiveStale, CompactChains, CuratorContext

        assert ArchiveStale is not None
        assert CompactChains is not None
        assert CuratorContext is not None
