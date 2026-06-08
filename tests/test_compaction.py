"""
Tests for runtime_reflection.py episode compaction (Phase 3 — v1.1).

RED phase: tests define expected behavior and should fail until
the compaction feature is correctly implemented.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import store as _store_mod

# Set up package namespace for runtime_reflection's relative imports
_PKG = "mem_reflection_hermes_compaction_test"


def _load_runtime_reflection():
    """Load reflection/runtime.py with proper package context for relative imports."""
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_REPO)]
    pkg.__package__ = _PKG
    sys.modules[_PKG] = pkg

    # Register subpackages so relative imports work
    for sub in ("core", "reflection", "memory", "runtime"):
        sub_mod = types.ModuleType(f"{_PKG}.{sub}")
        sub_mod.__path__ = [str(_REPO / sub)]
        sub_mod.__package__ = f"{_PKG}.{sub}"
        sys.modules[f"{_PKG}.{sub}"] = sub_mod

    # Register core.store and core.search so ..core.store resolves
    sys.modules[f"{_PKG}.core.store"] = _store_mod
    from core import search as _search_mod
    sys.modules[f"{_PKG}.core.search"] = _search_mod

    mod_path = _REPO / "reflection" / "runtime.py"
    spec = importlib.util.spec_from_file_location(
        f"{_PKG}.reflection.runtime", str(mod_path))
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = f"{_PKG}.reflection"
        sys.modules[f"{_PKG}.reflection.runtime"] = mod
        spec.loader.exec_module(mod)
        return mod
    return None


_ref_mod = _load_runtime_reflection()
_compact_episode_zone = _ref_mod._compact_episode_zone if _ref_mod else None

from core.store import MemoryFrontmatter


def _seed_episode_entries(store, count: int, base_day: str = "2026-06-05"):
    """Create *count* raw episode entries on *base_day*."""
    for i in range(count):
        fm = MemoryFrontmatter.new(
            source="session",
            confidence="low",
            tags=["raw_chunk"],
            zone="episode",
        )
        # Override created date to put all on same day
        fm.created = f"{base_day}T{10 + i % 12:02d}:00:00+00:00"
        store.put("user", fm, f"Episode entry {i}: sample conversation content about topic {i}")


# =====================================================================
# Compaction basic
# =====================================================================


class TestCompactEpisodeZone:
    """Test the episode compaction pipeline."""

    def test_below_threshold_noop(self, temp_store):
        """Fewer than threshold entries should not trigger compaction."""
        _seed_episode_entries(temp_store, 5)  # 5 < default threshold of 20
        result = _compact_episode_zone(temp_store)
        assert result["compacted"] == 0
        assert "below threshold" in result.get("skipped", "")

    def test_no_episode_entries_noop(self, temp_store):
        """Empty episode zone should not trigger compaction."""
        result = _compact_episode_zone(temp_store)
        assert result["compacted"] == 0

    def test_compacts_daily_clusters(self, temp_store):
        """Entries on the same day should be compacted into a summary."""
        _seed_episode_entries(temp_store, 25)  # > threshold
        result = _compact_episode_zone(temp_store)
        assert result["compacted"] >= 1
        assert result["total_raw_consumed"] >= 25
        # New summary should be in episode zone
        summaries = temp_store.list_by_zone("episode")
        compacted = [
            m for m in summaries
            if "compacted" in (m.frontmatter.tags or [])
        ]
        assert len(compacted) >= 1

    def test_compacted_tag_added(self, temp_store):
        """Compacted entries should have 'compacted' and 'auto-summary' tags."""
        _seed_episode_entries(temp_store, 25)
        _compact_episode_zone(temp_store)
        compacted = [
            m for m in temp_store.list_by_zone("episode")
            if "compacted" in (m.frontmatter.tags or [])
        ]
        assert len(compacted) >= 1
        for m in compacted:
            assert "auto-summary" in (m.frontmatter.tags or [])

    def test_original_entries_superseded(self, temp_store):
        """Original raw entries should be marked as superseded."""
        _seed_episode_entries(temp_store, 25)
        result = _compact_episode_zone(temp_store)

        # Check that at least one original entry was superseded
        all_episode = temp_store.list_by_zone("episode")
        superseded = [
            m for m in all_episode
            if m.frontmatter.supersedes
        ]
        assert len(superseded) >= 1
        assert result["total_raw_consumed"] >= 25

    def test_multiple_day_clusters(self, temp_store):
        """Entries on different days should be clustered separately."""
        # 12 entries on day 1, 12 on day 2 (24 total > threshold of 20)
        _seed_episode_entries(temp_store, 12, base_day="2026-06-05")
        _seed_episode_entries(temp_store, 12, base_day="2026-06-06")
        result = _compact_episode_zone(temp_store)
        # Both days have < 20 each, but combined > 20 threshold
        # Each day ≥ 2 entries → each cluster is compacted
        assert result["compacted"] == 2
        assert result["total_raw_consumed"] == 24

    def test_single_entry_day_skipped(self, temp_store):
        """Days with only 1 entry should not be compacted."""
        for i in range(22):
            fm = MemoryFrontmatter.new(
                source="session", confidence="low",
                tags=["raw_chunk"], zone="episode",
            )
            # All but one on the same day; one isolated entry on another day
            day = "2026-06-05" if i < 21 else "2026-06-06"
            fm.created = f"{day}T12:00:00+00:00"
            temp_store.put("user", fm, f"Entry {i}")
        result = _compact_episode_zone(temp_store)
        assert result["compacted"] == 1  # Only 2026-06-05 cluster compacted
        assert result["total_raw_consumed"] == 21

    def test_fallback_longest_body(self, temp_store):
        """Without LLM, fallback should pick the longest body."""
        _seed_episode_entries(temp_store, 25)
        result = _compact_episode_zone(temp_store, ctx=None)
        assert result["compacted"] >= 1
        for s in result["summaries"]:
            assert len(s["summary"]) > 0

    def test_compaction_can_run_multiple_times(self, temp_store):
        """Running compaction again should not re-compact already compacted entries."""
        _seed_episode_entries(temp_store, 30)
        # First pass
        r1 = _compact_episode_zone(temp_store)
        assert r1["compacted"] >= 1
        # Second pass — should find nothing new
        r2 = _compact_episode_zone(temp_store)
        assert r2["compacted"] == 0


# =====================================================================
# Return shape
# =====================================================================


class TestCompactionReturnShape:
    """Test that _compact_episode_zone returns the correct shape."""

    def test_return_shape_on_success(self, temp_store):
        """Return dict should have expected keys when compaction runs."""
        _seed_episode_entries(temp_store, 25)
        result = _compact_episode_zone(temp_store)
        assert isinstance(result, dict)
        assert "compacted" in result
        assert "summaries" in result
        assert "total_raw_consumed" in result

    def test_return_shape_on_noop(self, temp_store):
        """Return dict should have expected keys even on no-op."""
        result = _compact_episode_zone(temp_store)
        assert isinstance(result, dict)
        assert "compacted" in result
        assert "skipped" in result
