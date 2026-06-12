"""Tests for v1.5 Curator Action Pipeline refactor.

These tests verify the refactored curator package against the design intent
in docs/design/1.5/sprint1-curator-pipeline-sdd.md:
- CuratorAction base class and execute() contract
- CuratorContext and CuratorResult data structures
- Individual action isolation (6 actions) and boundary conditions
- Shared helper edge cases
- Pipeline ordering and error isolation via observable state
- Backward-compatible public API behaviour
- Cold store I/O directly

These tests are intent-facing: they exercise public/internal contracts from
the SDD, not implementation details of the legacy curator.py.
"""
from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tests._helpers import MockFrontmatter, MockMemory, MockStore

# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

_MOCK_TIME = time.time()


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
    s.memories["expired"] = MockMemory(
        "expired", "past valid_until", valid_until="2020-01-01T00:00:00Z"
    )
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
        """Unpinned, no keep/permanent tags -> not protected."""
        from memory.curator.helpers import is_protected

        fm = MockFrontmatter("test", "body", tags=["other"])
        assert is_protected(fm) is False

    def test_none_tags_is_not_protected(self):
        """None tags does not raise and returns False."""
        from memory.curator.helpers import is_protected

        fm = MockFrontmatter("test", "body")
        fm.tags = None
        assert is_protected(fm) is False

    def test_empty_tags_is_not_protected(self):
        """Empty tags list returns False."""
        from memory.curator.helpers import is_protected

        fm = MockFrontmatter("test", "body", tags=[])
        assert is_protected(fm) is False


class TestLoadLastAccess:
    """Tests for load_last_access() helper."""

    def test_returns_timestamp_on_success(self, stale_store):
        """Returns last_accessed timestamp when effectiveness data exists."""
        from memory.curator.helpers import load_last_access

        ts = load_last_access(stale_store, "fresh")
        assert ts > 0
        assert ts == _MOCK_TIME
        assert isinstance(ts, float)

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

    def test_extra_fields_passthrough(self, stale_store):
        """Arbitrary keyword arguments are merged into the entry."""
        from memory.curator.helpers import build_cold_entry

        mem = stale_store.get("fresh")
        entry = build_cold_entry(
            mem, context_tag="test", chain_depth=3, custom_key="custom_value"
        )

        assert entry["chain_depth"] == 3
        assert entry["custom_key"] == "custom_value"

    def test_original_frontmatter_includes_key_fields(self, stale_store):
        """original_frontmatter preserves created, confidence, pinned, supersedes."""
        from memory.curator.helpers import build_cold_entry

        mem = stale_store.get("fresh")
        entry = build_cold_entry(mem, context_tag="test")
        orig = entry["original_frontmatter"]

        assert "created" in orig
        assert "confidence" in orig
        assert "pinned" in orig
        assert "supersedes" in orig
        assert "supersedes_reason" in orig

    def test_refines_body_strips_tool_noise_and_truncates(self, empty_store):
        """build_cold_entry strips code blocks, tool markers, and truncates before persisting."""
        from memory.curator.helpers import build_cold_entry

        empty_store.memories["noisy"] = MockMemory(
            "noisy",
            'User preference\n```json\n{"key": "value"}\n```\n[Tool: search] result here',
        )
        mem = empty_store.get("noisy")
        entry = build_cold_entry(mem, context_tag="test")

        body = entry["body"]
        assert "```" not in body
        assert "[Tool:" not in body
        assert "User preference" in body
        assert len(body) <= 560  # max_chars=500 + zone/tags overhead from truncation


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

        stale_store._cold_store_path_override = "/nonexistent\x00path/_cold.jsonl"

        mem = stale_store.get("fresh")
        entry = build_cold_entry(mem, context_tag="test")
        success, error = archive_and_delete(stale_store, mem, entry, "test")

        assert success is False
        assert error is not None
        assert "cold store" in error.lower()

    def test_preserves_active_memory_when_delete_fails(self, stale_store, caplog):
        """If cold store succeeds but active delete fails, return False, keep memory, log warning."""
        from memory.curator.helpers import archive_and_delete, build_cold_entry

        class StoreWithFailingDelete(MockStore):
            def delete(self, scope, mid):
                raise RuntimeError("delete denied")

        s = StoreWithFailingDelete()
        s.memories["fresh"] = MockMemory("fresh", "recent")
        with tempfile.TemporaryDirectory() as td:
            s._cold_store_path_override = str(Path(td) / "_cold_store.jsonl")
            mem = s.get("fresh")
            entry = build_cold_entry(mem, context_tag="test")
            with caplog.at_level("WARNING"):
                success, error = archive_and_delete(s, mem, entry, "test")

            assert success is False
            assert "delete denied" in error
            assert "fresh" in s.memories  # active memory preserved for safety
            assert "Failed to delete" in caplog.text
            assert "cold entry preserved" in caplog.text.lower()


# ---------------------------------------------------------------------------
# Test: Config helpers
# ---------------------------------------------------------------------------


class TestCuratorConfig:
    """Tests for _curator_config and _curator_enabled helpers."""

    def test_config_merges_defaults(self, empty_store):
        """Missing keys fall back to hard-coded defaults."""
        from memory.curator.helpers import _curator_config

        cfg = _curator_config(empty_store)
        assert cfg["enabled"] is True
        assert cfg["trigger"] == "session_end"
        assert cfg["stale"]["days"] == 90
        assert cfg["similarity"]["bm25_threshold"] == 0.6

    def test_config_layered_merge(self, empty_store):
        """Top-level overrides and nested overrides both work."""
        from memory.curator.helpers import _curator_config

        empty_store._plugin_config_override = {
            "curator": {
                "enabled": False,
                "stale": {"days": 30},
                "similarity": {"enabled": False},
            }
        }
        cfg = _curator_config(empty_store)
        assert cfg["enabled"] is False
        assert cfg["stale"]["days"] == 30
        assert cfg["stale"]["effectiveness_threshold"] == 0.1  # default preserved
        assert cfg["similarity"]["enabled"] is False
        assert cfg["similarity"]["bm25_threshold"] == 0.6  # default preserved

    def test_curator_enabled_false_disables_pipeline(self, empty_store):
        """enabled=False is the hard off switch for the curator."""
        from memory.curator.helpers import _curator_enabled

        empty_store._plugin_config_override = {"curator": {"enabled": False}}

        assert _curator_enabled(empty_store) is False

    def test_unsupported_trigger_warns_but_remains_enabled(self, empty_store, caplog):
        """Unsupported trigger values warn but stay fail-open for session_end callers."""
        from memory.curator.helpers import _curator_enabled

        empty_store._plugin_config_override = {"curator": {"trigger": "manual"}}

        with caplog.at_level("WARNING"):
            enabled = _curator_enabled(empty_store)

        assert enabled is True
        assert "not supported" in caplog.text


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

    def test_empty_store_archives_nothing(self, empty_store):
        """ArchiveStale on empty store returns zero archives."""
        from memory.curator.actions import ArchiveStale, CuratorContext

        ctx = CuratorContext(mem_store=empty_store)
        result = ArchiveStale().execute(ctx)

        assert result.archived == 0
        assert result.errors == []

    def test_invalid_valid_until_is_treated_as_not_expired(self, empty_store):
        """Malformed valid_until does not trigger archive."""
        from memory.curator.actions import ArchiveStale, CuratorContext

        empty_store.memories["bad_date"] = MockMemory(
            "bad_date", "body", valid_until="not-a-date"
        )
        ctx = CuratorContext(mem_store=empty_store)
        result = ArchiveStale().execute(ctx)

        assert result.archived == 0
        assert "bad_date" in empty_store.memories

    def test_effectiveness_below_threshold_triggers_archive(self, empty_store):
        """Low effectiveness (even with recent access) triggers stale archive."""
        from memory.curator.actions import ArchiveStale, CuratorContext

        empty_store.memories["low_eff"] = MockMemory(
            "low_eff", "low effectiveness memory", confidence="high"
        )
        empty_store.eff_data["low_eff"] = {
            "last_accessed": _MOCK_TIME,
            "effectiveness": 0.05,  # below default threshold 0.1
        }
        ctx = CuratorContext(mem_store=empty_store)
        result = ArchiveStale().execute(ctx)

        assert result.archived == 1
        assert "low_eff" in empty_store.deleted

    def test_recent_high_effectiveness_is_not_archived(self, empty_store):
        """Recent access and high effectiveness keep memory active."""
        from memory.curator.actions import ArchiveStale, CuratorContext

        empty_store.memories["active"] = MockMemory("active", "active memory")
        empty_store.eff_data["active"] = {
            "last_accessed": _MOCK_TIME,
            "effectiveness": 0.9,
        }
        ctx = CuratorContext(mem_store=empty_store)
        result = ArchiveStale().execute(ctx)

        assert result.archived == 0
        assert "active" in empty_store.memories


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

        # Build chain: v1 -> v2 -> v3 -> v4 -> v5
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

    def test_short_chain_is_skipped(self, empty_store):
        """Chains shorter than compact_min_chain are left untouched."""
        from memory.curator.actions import CompactChains, CuratorContext

        empty_store.memories["v1"] = MockMemory("v1", "oldest", zone="core", supersedes=[])
        empty_store.memories["v2"] = MockMemory("v2", "newest", zone="core", supersedes=["v1"])

        ctx = CuratorContext(mem_store=empty_store)
        result = CompactChains().execute(ctx)

        assert result.compacted == 0
        assert "v1" in empty_store.memories
        assert "v2" in empty_store.memories

    def test_recent_access_protects_chain(self, empty_store):
        """Chains with a recently accessed node are not compacted."""
        from memory.curator.actions import CompactChains, CuratorContext

        empty_store.memories["v1"] = MockMemory("v1", "oldest", zone="core", supersedes=[])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "v3", zone="core", supersedes=["v2"])
        empty_store.memories["v4"] = MockMemory("v4", "newest", zone="core", supersedes=["v3"])
        empty_store.eff_data["v2"] = {
            "last_accessed": time.time(),  # recent
            "effectiveness": 0.5,
        }

        ctx = CuratorContext(mem_store=empty_store)
        result = CompactChains().execute(ctx)

        assert result.compacted == 0
        assert "v1" in empty_store.memories
        assert "v4" in empty_store.memories

    def test_protected_intermediate_is_skipped(self, empty_store):
        """Pinned intermediate nodes are not compacted."""
        from memory.curator.actions import CompactChains, CuratorContext

        empty_store.memories["v1"] = MockMemory("v1", "oldest", zone="core", supersedes=[])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"], pinned=True)
        empty_store.memories["v3"] = MockMemory("v3", "v3", zone="core", supersedes=["v2"])
        empty_store.memories["v4"] = MockMemory("v4", "newest", zone="core", supersedes=["v3"])

        ctx = CuratorContext(mem_store=empty_store)
        result = CompactChains().execute(ctx)

        assert "v2" in empty_store.memories  # protected intermediate kept
        assert "v1" in empty_store.memories
        assert "v4" in empty_store.memories

    def test_update_head_failure_is_reported_after_archiving(self, empty_store):
        """Compaction still archives intermediates even if head supersedes update fails."""
        from memory.curator.actions import CompactChains, CuratorContext

        class StoreWithFailingHeadUpdate(MockStore):
            def update(self, mem_id, **kwargs):
                if mem_id == "v4":
                    raise RuntimeError("update blocked")
                return super().update(mem_id, **kwargs)

        store = StoreWithFailingHeadUpdate()
        with tempfile.TemporaryDirectory() as td:
            store._cold_store_path_override = str(Path(td) / "_cold_store.jsonl")
            store.memories["v1"] = MockMemory("v1", "oldest", zone="core", supersedes=[])
            store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
            store.memories["v3"] = MockMemory("v3", "v3", zone="core", supersedes=["v2"])
            store.memories["v4"] = MockMemory("v4", "newest", zone="core", supersedes=["v3"])

            ctx = CuratorContext(mem_store=store)
            result = CompactChains().execute(ctx)

        assert result.compacted == 2
        assert "v2" in store.deleted
        assert "v3" in store.deleted
        assert any("update head v4" in err for err in result.errors)


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

        # Chain: orig -> v1 -> v2 -> v3 (depth = 3 from head v3)
        empty_store.memories["orig"] = MockMemory("orig", "original", zone="core", supersedes=[])
        empty_store.memories["v1"] = MockMemory("v1", "v1", zone="core", supersedes=["orig"])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "newest", zone="core", supersedes=["v2"])

        ctx = CuratorContext(mem_store=empty_store)
        action = ArchiveSuperseded()
        result = action.execute(ctx)

        assert result.archived >= 1
        assert "v3" not in empty_store.deleted  # newest preserved

    def test_shallow_chain_is_skipped(self, empty_store):
        """Chains with depth < 3 are not archived."""
        from memory.curator.actions import ArchiveSuperseded, CuratorContext

        empty_store.memories["orig"] = MockMemory("orig", "original", zone="core", supersedes=[])
        empty_store.memories["v1"] = MockMemory("v1", "newest", zone="core", supersedes=["orig"])

        ctx = CuratorContext(mem_store=empty_store)
        result = ArchiveSuperseded().execute(ctx)

        assert result.archived == 0
        assert "orig" in empty_store.memories
        assert "v1" in empty_store.memories

    def test_protected_node_is_skipped(self, empty_store):
        """Protected nodes in deep chains are not archived."""
        from memory.curator.actions import ArchiveSuperseded, CuratorContext

        empty_store.memories["orig"] = MockMemory("orig", "original", zone="core", supersedes=[])
        empty_store.memories["v1"] = MockMemory("v1", "v1", zone="core", supersedes=["orig"], pinned=True)
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "newest", zone="core", supersedes=["v2"])

        ctx = CuratorContext(mem_store=empty_store)
        result = ArchiveSuperseded().execute(ctx)

        assert "v1" in empty_store.memories  # protected node kept

    def test_recently_accessed_node_is_skipped(self, empty_store):
        """Recently accessed superseded nodes are preserved even in deep chains."""
        from memory.curator.actions import ArchiveSuperseded, CuratorContext

        empty_store.memories["orig"] = MockMemory("orig", "original", zone="core", supersedes=[])
        empty_store.memories["v1"] = MockMemory("v1", "v1", zone="core", supersedes=["orig"])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "newest", zone="core", supersedes=["v2"])
        empty_store.eff_data["v1"] = {
            "last_accessed": time.time(),
            "effectiveness": 0.8,
        }

        ctx = CuratorContext(mem_store=empty_store)
        result = ArchiveSuperseded().execute(ctx)

        assert "v1" in empty_store.memories
        assert result.archived >= 1


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
        assert len(empty_store.deleted) >= 1

    def test_below_merge_threshold_does_not_merge(self, empty_store):
        """Pairs with similarity below merge_threshold are only counted, not merged."""
        from memory.curator.actions import MergeSimilar, CuratorContext

        # Slightly related but unlikely to exceed default merge_threshold=0.7
        empty_store.memories["a"] = MockMemory("a", "apple banana cherry")
        empty_store.memories["b"] = MockMemory("b", "date elderberry fig")

        ctx = CuratorContext(mem_store=empty_store)
        result = MergeSimilar().execute(ctx)

        assert result.merged == 0
        assert len(empty_store.deleted) == 0
        assert "a" in empty_store.memories
        assert "b" in empty_store.memories

    def test_similarity_disabled_returns_zero(self, empty_store):
        """When similarity.enabled is False, action returns zero candidates."""
        from memory.curator.actions import MergeSimilar, CuratorContext

        empty_store._plugin_config_override = {
            "curator": {"similarity": {"enabled": False}}
        }

        empty_store.memories["a"] = MockMemory("a", "duplicate text one")
        empty_store.memories["b"] = MockMemory("b", "duplicate text two")

        ctx = CuratorContext(mem_store=empty_store)
        result = MergeSimilar().execute(ctx)

        assert result.similar_pairs == 0
        assert result.merged == 0

    def test_identical_bodies_archive_one(self, empty_store):
        """Identical bodies result in archiving one memory and keeping the other."""
        from memory.curator.actions import MergeSimilar, CuratorContext

        empty_store.memories["a"] = MockMemory("a", "exactly the same body", tags=["x"])
        empty_store.memories["b"] = MockMemory("b", "exactly the same body", tags=["y"])

        ctx = CuratorContext(mem_store=empty_store)
        result = MergeSimilar().execute(ctx)

        assert result.merged == 1
        assert len(empty_store.deleted) == 1
        assert ("a" in empty_store.deleted) != ("b" in empty_store.deleted)

    def test_superseded_memories_are_skipped(self, empty_store):
        """Memories already superseded by another are not merged."""
        from memory.curator.actions import MergeSimilar, CuratorContext

        empty_store.memories["a"] = MockMemory("a", "exactly the same body")
        empty_store.memories["b"] = MockMemory("b", "exactly the same body")
        empty_store.memories["c"] = MockMemory("c", "supersedes b", supersedes=["b"])

        ctx = CuratorContext(mem_store=empty_store)
        result = MergeSimilar().execute(ctx)

        # a and c might be considered, but b is superseded and skipped
        assert "b" not in empty_store.deleted


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

    def test_counts_cleaned_edges_when_graph_available(self, empty_store, monkeypatch):
        """Action forwards active IDs to graph manager and returns cleaned count."""
        from memory.curator.actions import CleanOrphanEdges, CuratorContext

        cleaned = {"count": 0}

        class FakeGraphStore:
            def clean_orphan_edges(self, active_ids):
                cleaned["count"] = len(active_ids)
                return 7

        class FakeManager:
            store = FakeGraphStore()

        def fake_get_graph_manager_compat():
            return FakeManager()

        import memory.curator.actions as _actions_mod

        monkeypatch.setattr(
            _actions_mod,
            "get_graph_manager_compat",
            fake_get_graph_manager_compat,
        )

        empty_store.memories["m1"] = MockMemory("m1", "body1")
        empty_store.memories["m2"] = MockMemory("m2", "body2")

        ctx = CuratorContext(mem_store=empty_store)
        result = CleanOrphanEdges().execute(ctx)

        assert result.orphan_edges == 7
        assert cleaned["count"] == 2


class TestGenerateReportAction:
    """Tests for GenerateReport action."""

    def test_action_name(self):
        """GenerateReport.name is 'GenerateReport'."""
        from memory.curator.actions import GenerateReport

        action = GenerateReport()
        assert action.name == "GenerateReport"

    def test_empty_results_yield_no_actions_message(self):
        """With no prior results, report text says 'No curator actions'."""
        from memory.curator.actions import GenerateReport, CuratorContext

        ctx = CuratorContext(mem_store=MockStore())
        action = GenerateReport()
        result = action.execute(ctx, [])

        assert getattr(result, "report_text", None) == "No curator actions"

    def test_report_includes_stale_archives(self):
        """Report text mentions stale detection and archive counts."""
        from memory.curator.actions import (
            GenerateReport,
            CuratorContext,
            CuratorResult,
        )

        ctx = CuratorContext(mem_store=MockStore())
        prior = [CuratorResult(action_name="ArchiveStale", archived=5)]
        result = GenerateReport().execute(ctx, prior)
        text = getattr(result, "report_text", "")

        assert "stale" in text
        assert "5" in text

    def test_report_includes_all_action_types(self):
        """Report aggregates counts from all action types."""
        from memory.curator.actions import (
            GenerateReport,
            CuratorContext,
            CuratorResult,
        )

        ctx = CuratorContext(mem_store=MockStore())
        prior = [
            CuratorResult(action_name="ArchiveStale", archived=2),
            CuratorResult(action_name="ArchiveSuperseded", archived=3),
            CuratorResult(action_name="CompactChains", compacted=4),
            CuratorResult(action_name="MergeSimilar", similar_pairs=1, merged=1),
            CuratorResult(action_name="CleanOrphanEdges", orphan_edges=6),
        ]
        result = GenerateReport().execute(ctx, prior)
        text = getattr(result, "report_text", "")

        assert "superseded: 3 archived" in text
        assert "compacted: 4 archived" in text
        assert "similar: 1 candidate pair(s) found" in text
        assert "merged: 1 archived" in text
        assert "orphan edges: 6 cleaned" in text

    def test_report_includes_error_count(self):
        """Report text includes the number of accumulated errors."""
        from memory.curator.actions import (
            GenerateReport,
            CuratorContext,
            CuratorResult,
        )

        ctx = CuratorContext(mem_store=MockStore())
        ctx.errors.append("failure one")
        ctx.errors.append("failure two")
        prior = [CuratorResult(action_name="ArchiveStale", archived=1)]
        result = GenerateReport().execute(ctx, prior)
        text = getattr(result, "report_text", "")

        assert "errors: 2" in text


class TestReportPersistence:
    """Tests for curator report persistence side effects."""

    def test_run_curator_persists_report_and_appends_reflection_log(self, empty_store, monkeypatch):
        """Full curator run persists a report file and mirrors its summary into reflection logs."""
        from memory.curator import _run_curator
        import memory.curator.report as _report_mod

        reflected = []

        class FakeReflectionRuntime:
            @staticmethod
            def _append_reflect_log(entry):
                reflected.append(entry)

        monkeypatch.setattr(
            _report_mod,
            "_lb_fn",
            lambda name: FakeReflectionRuntime if name.endswith("reflection.runtime") else None,
        )

        empty_store.memories["old"] = MockMemory(
            "old", "old", valid_until="2020-01-01T00:00:00Z"
        )
        result = _run_curator(None, empty_store)

        report_path = Path(empty_store._cold_store_path_override).parent / "curator-report.json"
        payload = json.loads(report_path.read_text(encoding="utf-8"))

        assert payload["report"] == result["report"]
        assert payload["total_archived"] == result["total_archived"]
        assert reflected and reflected[0]["summary"] == result["report"]


# ---------------------------------------------------------------------------
# Test: Cold Store I/O
# ---------------------------------------------------------------------------


class TestColdStore:
    """Direct tests for memory.curator.cold_store helpers."""

    def test_load_returns_empty_when_missing(self, empty_store):
        """Loading cold store that does not exist returns []."""
        from memory.curator.cold_store import _load_cold_store

        assert _load_cold_store(empty_store) == []

    def test_append_and_load_round_trip(self, empty_store):
        """Appended entries can be loaded back."""
        from memory.curator.cold_store import _append_to_cold_store, _load_cold_store

        entry = {"id": "m1", "body": "hello", "archived_at": "2026-01-01T00:00:00Z"}
        assert _append_to_cold_store(empty_store, entry) is True
        loaded = _load_cold_store(empty_store)
        assert len(loaded) == 1
        assert loaded[0]["id"] == "m1"

    def test_load_skips_corrupt_jsonl_lines(self, empty_store):
        """Corrupt JSONL lines are skipped with a warning, valid lines preserved."""
        from memory.curator.cold_store import _load_cold_store, _cold_store_path

        path = _cold_store_path(empty_store)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"id":"good"}\nnot-json\n{"id":"good2"}\n', encoding="utf-8"
        )
        loaded = _load_cold_store(empty_store)
        assert len(loaded) == 2
        assert loaded[0]["id"] == "good"
        assert loaded[1]["id"] == "good2"

    def test_prune_removes_oldest_to_fit_cap(self, empty_store):
        """Prune removes oldest entries until total size is under cap."""
        from memory.curator.cold_store import (
            _append_to_cold_store,
            _load_cold_store,
            _prune_cold_store,
        )

        for i in range(5):
            entry = {
                "id": f"m{i}",
                "body": "x" * 2000,
                "archived_at": f"2026-01-{i + 1:02d}T00:00:00Z",
            }
            _append_to_cold_store(empty_store, entry)

        removed = _prune_cold_store(empty_store, cap_mb=0)
        assert removed >= 1
        loaded = _load_cold_store(empty_store)
        assert len(loaded) < 5

    def test_restore_moves_entry_back_to_active(self, empty_store):
        """Restoring a cold entry writes it to active store and removes from cold."""
        from memory.curator.cold_store import (
            _append_to_cold_store,
            _load_cold_store,
            _restore_from_cold,
        )

        entry = {
            "id": "m1",
            "body": "restored body",
            "zone": "work",
            "archived_at": "2026-01-01T00:00:00Z",
            "tags": ["old"],
            "original_frontmatter": {
                "created": "2025-01-01T00:00:00Z",
                "confidence": "high",
                "pinned": False,
                "supersedes": [],
            },
        }
        _append_to_cold_store(empty_store, entry)
        success = _restore_from_cold(empty_store, "m1")

        assert success is True
        assert "m1" in empty_store.memories
        assert empty_store.memories["m1"].frontmatter.zone == "work"
        loaded = _load_cold_store(empty_store)
        assert not any(e.get("id") == "m1" for e in loaded)

    def test_restore_missing_returns_false(self, empty_store):
        """Restoring a non-existent ID returns False."""
        from memory.curator.cold_store import _restore_from_cold

        assert _restore_from_cold(empty_store, "missing") is False

    def test_append_fails_open_on_invalid_path(self, empty_store):
        """Cold store append returns False instead of raising on invalid path."""
        from memory.curator.cold_store import _append_to_cold_store

        empty_store._cold_store_path_override = "/nonexistent\x00path/_cold.jsonl"
        result = _append_to_cold_store(empty_store, {"id": "x"})
        assert result is False


# ---------------------------------------------------------------------------
# Test: Pipeline Orchestration
# ---------------------------------------------------------------------------


class TestPipelineOrder:
    """Tests for action execution order in _run_curator."""

    def test_compact_runs_before_archive(self, empty_store):
        """CompactChains executes before ArchiveSuperseded, preserving chain head ancestors.

        With v1 -> v2 -> v3 -> v4 -> v5:
        - If Compact runs first, intermediates v2,v3,v4 are archived and v5.supersedes
          is updated to [v1]. ArchiveSuperseded then sees depth 2 and leaves v1 alone.
        - If Archive ran first, depth 5 >= 3 would archive v4,v3,v2,v1.
        Observable invariant: v1 remains active only when Compact runs before Archive.
        """
        from memory.curator import _run_curator

        empty_store.memories["v1"] = MockMemory("v1", "oldest", zone="core", supersedes=[])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "v3", zone="core", supersedes=["v2"])
        empty_store.memories["v4"] = MockMemory("v4", "v4", zone="core", supersedes=["v3"])
        empty_store.memories["v5"] = MockMemory("v5", "newest", zone="core", supersedes=["v4"])

        _run_curator(None, empty_store)

        assert "v5" in empty_store.memories
        assert "v1" in empty_store.memories  # preserved only if compact ran first
        assert "v2" in empty_store.deleted
        assert "v3" in empty_store.deleted
        assert "v4" in empty_store.deleted


class TestErrorIsolation:
    """Tests that one action failure does not skip subsequent actions."""

    def test_action_failure_does_not_skip_later_actions(self, empty_store, monkeypatch):
        """If ArchiveStale cannot write cold store, CompactChains still compacts chains."""
        from memory.curator import _run_curator
        import memory.curator.actions as _actions_mod
        import memory.curator.helpers as _helpers_mod

        # Memory that ArchiveStale will try to archive
        empty_store.memories["expired"] = MockMemory(
            "expired", "past valid_until", valid_until="2020-01-01T00:00:00Z"
        )
        # Independent chain that CompactChains can process
        empty_store.memories["v1"] = MockMemory("v1", "oldest", zone="core", supersedes=[])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "v3", zone="core", supersedes=["v2"])
        empty_store.memories["v4"] = MockMemory("v4", "newest", zone="core", supersedes=["v3"])

        _original = _helpers_mod.archive_and_delete

        def _failing_for_stale(mem_store, mem, entry, context):
            if context == "stale":
                return False, "stale cold store forced failure"
            return _original(mem_store, mem, entry, context)

        # actions.py imports archive_and_delete into its module namespace,
        # so patch that binding rather than the helpers module source.
        monkeypatch.setattr(_actions_mod, "archive_and_delete", _failing_for_stale)

        result = _run_curator(None, empty_store)

        assert len(result.get("errors", [])) >= 1
        assert result["compacted"] == 2  # v2, v3 archived
        assert "v2" in empty_store.deleted
        assert "v3" in empty_store.deleted


class TestPipelineAggregation:
    """Tests for result aggregation in _run_curator."""

    def test_total_archived_sums_archived_sources_only(self, empty_store):
        """total_archived includes stale, superseded, and merged but not compacted."""
        from memory.curator import _run_curator

        # Stale memory
        empty_store.memories["old"] = MockMemory(
            "old", "old", valid_until="2020-01-01T00:00:00Z"
        )
        # Superseded chain depth 3
        empty_store.memories["orig"] = MockMemory("orig", "orig", zone="core", supersedes=[])
        empty_store.memories["v1"] = MockMemory("v1", "v1", zone="core", supersedes=["orig"])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])

        result = _run_curator(None, empty_store)

        expected = result["archived"] + result["superseded"] + result["merged"]
        assert result["total_archived"] == expected

    def test_all_expected_keys_present(self, empty_store):
        """_run_curator returns the same top-level keys as the v1.4 contract."""
        from memory.curator import _run_curator

        result = _run_curator(None, empty_store)

        for key in (
            "curator",
            "stale",
            "archived",
            "superseded",
            "compacted",
            "similar",
            "merged",
            "orphan_edges",
            "total_archived",
            "errors",
            "report",
        ):
            assert key in result

    def test_report_failure_falls_back_to_text_summary(self, empty_store, monkeypatch):
        """If GenerateReport action crashes, orchestrator still returns a fallback report."""
        import memory.curator as _curator_mod

        class BrokenGenerateReport:
            def execute(self, ctx, prior_results=None):
                raise RuntimeError("report explosion")

        monkeypatch.setattr(_curator_mod, "GenerateReport", BrokenGenerateReport)
        empty_store.memories["old"] = MockMemory(
            "old", "old", valid_until="2020-01-01T00:00:00Z"
        )

        result = _curator_mod._run_curator(None, empty_store)

        assert result["report"].startswith("curator:")
        assert any(err.startswith("report: ") for err in result["errors"])


# ---------------------------------------------------------------------------
# Test: Backward Compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Tests that existing imports and wrapper functions still work."""

    def test_import_from_package(self):
        """Can import _run_curator from memory.curator package."""
        from memory.curator import _run_curator

        assert callable(_run_curator)

    def test_import_helper_functions(self):
        """Can import helper functions from memory.curator."""
        from memory.curator import build_cold_entry, is_protected

        assert callable(is_protected)
        assert callable(build_cold_entry)

    def test_import_action_classes(self):
        """Can import action classes from memory.curator.actions."""
        from memory.curator.actions import ArchiveStale, CompactChains, CuratorContext

        assert ArchiveStale is not None
        assert CompactChains is not None
        assert CuratorContext is not None

    def test_scan_for_stale_returns_ids_without_mutating(self, stale_store):
        """Legacy scan_for_stale detects stale IDs and does not archive."""
        from memory.curator import scan_for_stale

        ids = scan_for_stale(stale_store)
        assert isinstance(ids, list)
        assert "stale" in ids or "expired" in ids
        assert len(stale_store.deleted) == 0

    def test_archive_expired_archives_requested_ids(self, stale_store):
        """Legacy archive_expired archives each ID in the list."""
        from memory.curator import archive_expired

        count = archive_expired(stale_store, ["stale"])
        assert count == 1
        assert "stale" in stale_store.deleted
        assert "fresh" in stale_store.memories

    def test_archive_superseded_delegates_action(self, empty_store):
        """Legacy archive_superseded runs ArchiveSuperseded action."""
        from memory.curator import archive_superseded

        empty_store.memories["orig"] = MockMemory("orig", "orig", zone="core", supersedes=[])
        empty_store.memories["v1"] = MockMemory("v1", "v1", zone="core", supersedes=["orig"])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "newest", zone="core", supersedes=["v2"])

        count = archive_superseded(empty_store)
        assert count >= 1

    def test_compact_superseded_chains_delegates_action(self, empty_store):
        """Legacy compact_superseded_chains runs CompactChains action."""
        from memory.curator import compact_superseded_chains

        empty_store.memories["v1"] = MockMemory("v1", "oldest", zone="core", supersedes=[])
        empty_store.memories["v2"] = MockMemory("v2", "v2", zone="core", supersedes=["v1"])
        empty_store.memories["v3"] = MockMemory("v3", "v3", zone="core", supersedes=["v2"])
        empty_store.memories["v4"] = MockMemory("v4", "newest", zone="core", supersedes=["v3"])

        count = compact_superseded_chains(empty_store)
        assert count == 2  # v2, v3

    def test_scan_for_similar_returns_candidate_pairs(self, empty_store):
        """Legacy scan_for_similar returns (a, b, score) tuples."""
        from memory.curator import scan_for_similar

        empty_store.memories["a"] = MockMemory("a", "OpenSpec SDD design workflow")
        empty_store.memories["b"] = MockMemory("b", "OpenSpec SDD workflow design")

        pairs = scan_for_similar(empty_store)
        assert isinstance(pairs, list)
        if pairs:
            a, b, score = pairs[0]
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0

    def test_merge_similar_delegates_action(self, empty_store):
        """Legacy merge_similar runs MergeSimilar action."""
        from memory.curator import merge_similar

        empty_store.memories["a"] = MockMemory("a", "exactly the same body")
        empty_store.memories["b"] = MockMemory("b", "exactly the same body")

        count = merge_similar(empty_store)
        assert count == 1

    def test_clean_orphan_edges_delegates_action(self, empty_store, monkeypatch):
        """Legacy clean_orphan_edges runs CleanOrphanEdges action."""
        from memory.curator import clean_orphan_edges

        class FakeGraphStore:
            def clean_orphan_edges(self, active_ids):
                return 9

        class FakeManager:
            store = FakeGraphStore()

        monkeypatch.setattr(
            "memory.curator.actions.get_graph_manager_compat", lambda: FakeManager()
        )
        empty_store.memories["m1"] = MockMemory("m1", "body")

        count = clean_orphan_edges(empty_store)
        assert count == 9

