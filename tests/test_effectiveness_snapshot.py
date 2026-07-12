"""Tests for the dual-track stats persistence (event stream + aggregate snapshot).

Covers:
- load_effectiveness() reads the event stream when no snapshot exists (back-compat)
- load_effectiveness() reads snapshot baseline + event-stream tail when both exist
- compact_stats_snapshot() folds the stream into the snapshot and truncates it
- compaction is idempotent (result unchanged after fold)
- post-compaction new events merge correctly with the snapshot baseline
- compaction skips when below the configured threshold
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import mem_reflection_hermes.core.store as store_mod
from mem_reflection_hermes.core.store import (
    MemoryEffectiveness,
    MemoryFrontmatter,
    record_memory_stat,
    load_effectiveness,
)


@pytest.fixture
def isolated_stats_dir(monkeypatch, tmp_path):
    """Isolate effectiveness stats to a temp HERMES_HOME.

    Both memory-stats.jsonl and effectiveness-index.jsonl resolve through
    plugin_data_dir(), which reads HERMES_HOME at call time. Setting the env
    var (rather than monkeypatching module attributes) works regardless of
    which module instance (_lb_fn vs direct import) resolves the path -- both
    read the same env var. This mirrors how conftest's curator graph fixture
    isolates state without touching module globals.

    The plugin_data_dir for stats lives under <HERMES_HOME>/memory/, matching
    production layout.
    """
    home = tmp_path / "hermes_home"
    (home / "memory").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    store_mod._invalidate_effectiveness_cache()
    yield home / "memory"
    store_mod._invalidate_effectiveness_cache()


def _write_event(path: Path, memory_id: str, event: str, at: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"memory_id": memory_id, "event": event, "at": at}) + "\n")


class TestReadPathBackwardCompat:
    """No snapshot -> full event-stream scan (pre-compaction / fresh install)."""

    def test_load_reads_event_stream_when_no_snapshot(self, isolated_stats_dir):
        stats_path = store_mod._stats_path()
        t = "2026-06-01T00:00:00+00:00"
        _write_event(stats_path, "m1", "accessed", t)
        _write_event(stats_path, "m1", "accessed", t)
        _write_event(stats_path, "m1", "loaded", t)
        _write_event(stats_path, "m2", "referenced", t)

        eff = store_mod.load_effectiveness()
        assert eff["m1"].accessed == 2
        assert eff["m1"].loaded == 1
        assert eff["m2"].referenced == 1
        assert eff["m1"].last_event_at == t

    def test_load_empty_when_no_files(self, isolated_stats_dir):
        assert store_mod.load_effectiveness() == {}

    def test_deprecated_record_stat_forwards_to_jsonl(self, isolated_stats_dir):
        """P2-1: MemoryStore.record_stat() still records stats (to JSONL) but warns."""
        from mem_reflection_hermes.core.store import MemoryStore
        import warnings

        home = isolated_stats_dir.parent
        store = MemoryStore(user_root=home / "memories", db_path=home / "memories.db")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            store.record_stat("legacy-mem", "accessed")
        assert any(issubclass(x.category, DeprecationWarning) for x in w)

        eff = store_mod.load_effectiveness()
        assert "legacy-mem" in eff
        assert eff["legacy-mem"].accessed == 1


    def test_deprecated_store_methods_effectiveness_forwards_to_jsonl(self, isolated_stats_dir):
        """P2-1: store_methods.effectiveness() forwards to JSONL truth path."""
        import warnings
        from mem_reflection_hermes.core import store_methods
        from mem_reflection_hermes.core.store import MemoryStore

        home = isolated_stats_dir.parent
        store = MemoryStore(user_root=home / "memories", db_path=home / "memories.db")
        _write_event(store_mod._stats_path(), "m1", "accessed", "2026-06-01T00:00:00+00:00")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            eff = store_methods.effectiveness(store, memory_id="m1")
        assert any(issubclass(x.category, DeprecationWarning) for x in w)
        assert eff["m1"].accessed == 1

    def test_deprecated_store_methods_record_stat_forwards_to_jsonl(self, isolated_stats_dir):
        """P2-1: store_methods.record_stat() writes JSONL, not the SQLite stats table."""
        import warnings
        from mem_reflection_hermes.core import store_methods
        from mem_reflection_hermes.core.store import MemoryStore

        home = isolated_stats_dir.parent
        store = MemoryStore(user_root=home / "memories", db_path=home / "memories.db")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            store_methods.record_stat(store, "legacy-sm", "accessed")
        assert any(issubclass(x.category, DeprecationWarning) for x in w)

        eff = store_mod.load_effectiveness()
        assert "legacy-sm" in eff
        assert eff["legacy-sm"].accessed == 1


class TestDualTrackRead:
    """Snapshot baseline + event-stream tail merge."""

    def test_snapshot_baseline_plus_tail(self, isolated_stats_dir):
        # Snapshot has m1 with folded counts.
        folded_at = "2026-06-01T00:00:00+00:00"
        store_mod._write_effectiveness_snapshot(
            {"m1": MemoryEffectiveness(loaded=5, referenced=3, accessed=10, last_event_at=folded_at)},
            folded_at,
        )
        # Event stream has NEW events after the fold (at > folded_at).
        stats_path = store_mod._stats_path()
        _write_event(stats_path, "m1", "accessed", "2026-06-02T00:00:00+00:00")
        _write_event(stats_path, "m2", "accessed", "2026-06-02T00:00:00+00:00")

        # Call via the module attribute (store_mod.load_effectiveness) so the
        # patched plugin_data_dir is honored. The top-level `load_effectiveness`
        # import may bind to a different module instance under pytest's package
        # registration; the module-attribute form is always consistent.
        eff = store_mod.load_effectiveness()
        # m1 = baseline (10 accessed) + 1 new = 11
        assert eff["m1"].accessed == 11
        assert eff["m1"].loaded == 5
        assert eff["m1"].last_event_at == "2026-06-02T00:00:00+00:00"
        # m2 only in the tail
        assert eff["m2"].accessed == 1

    def test_events_at_or_before_folded_at_are_skipped(self, isolated_stats_dir):
        folded_at = "2026-06-01T12:00:00+00:00"
        store_mod._write_effectiveness_snapshot(
            {"m1": MemoryEffectiveness(accessed=5, last_event_at=folded_at)},
            folded_at,
        )
        stats_path = store_mod._stats_path()
        # An event with at == folded_at (already folded) -> must NOT double-count.
        _write_event(stats_path, "m1", "accessed", folded_at)
        # An older event (at < folded_at) -> also skipped.
        _write_event(stats_path, "m1", "accessed", "2026-05-01T00:00:00+00:00")

        eff = store_mod.load_effectiveness()
        assert eff["m1"].accessed == 5  # baseline only, no double-count


class TestCompaction:
    """compact_stats_snapshot folds + truncates."""

    def test_fold_truncates_and_preserves_result(self, isolated_stats_dir, monkeypatch):
        # Use the package-prefixed import path so compact_stats_snapshot shares
        # the same _stat_write_lock instance as record_memory_stat in core.store.
        from mem_reflection_hermes.memory.curator.helpers import compact_stats_snapshot
        from tests._helpers import MockStore

        # Lower the threshold so we can trigger compaction with few lines.
        store = MockStore()
        store._plugin_config_override = {"curator": {"stats": {"compact_threshold_lines": 3}}}

        t = "2026-06-01T00:00:00+00:00"
        stats_path = store_mod._stats_path()
        for _ in range(5):
            _write_event(stats_path, "m1", "accessed", t)
        _write_event(stats_path, "m2", "loaded", t)

        # P2-16: compact_stats_snapshot GCs rows for memories that no longer exist.
        # Put the memories into the mock store so they are retained.
        for mid in ("m1", "m2"):
            fm = MemoryFrontmatter.new(source="test", confidence="medium")
            # Override the auto-generated id so it matches the event memory_id.
            fm.id = mid
            store.put("user", fm, f"body {mid}")

        eff_before = store_mod.load_effectiveness()
        assert eff_before["m1"].accessed == 5

        result = compact_stats_snapshot(store)
        assert result["compacted"] is True
        assert result["lines_before"] == 6
        assert result["lines_after"] == 0
        assert result.get("removed_dead_rows", 0) == 0

        # Event stream truncated.
        assert store_mod._stats_path().read_text().strip() == ""
        # Snapshot file now exists with one row per memory.
        snap = store_mod._effectiveness_index_path().read_text(encoding="utf-8").splitlines()
        assert len(snap) == 2

        # load_effectiveness() returns the SAME aggregate (idempotent fold).
        eff_after = store_mod.load_effectiveness()
        assert eff_after["m1"].accessed == 5
        assert eff_after["m2"].loaded == 1

    def test_skip_when_below_threshold(self, isolated_stats_dir):
        from mem_reflection_hermes.memory.curator.helpers import compact_stats_snapshot
        from tests._helpers import MockStore

        store = MockStore()  # default threshold 5000
        _write_event(store_mod._stats_path(), "m1", "accessed", "2026-06-01T00:00:00+00:00")

        result = compact_stats_snapshot(store)
        assert result["compacted"] is False
        assert result["lines_before"] == 1
        # Event stream untouched.
        assert store_mod._stats_path().exists()
        assert len(store_mod._stats_path().read_text().strip().splitlines()) == 1

    def test_post_compaction_new_events_merge(self, isolated_stats_dir):
        from mem_reflection_hermes.memory.curator.helpers import compact_stats_snapshot
        from tests._helpers import MockStore

        store = MockStore()
        store._plugin_config_override = {"curator": {"stats": {"compact_threshold_lines": 2}}}

        # Seed the mock store with the memory so compaction retains its stats.
        fm = MemoryFrontmatter.new(source="test", confidence="medium")
        fm.id = "m1"
        store.put("user", fm, "body m1")

        # Initial events.
        _write_event(store_mod._stats_path(), "m1", "accessed", "2026-06-01T00:00:00+00:00")
        _write_event(store_mod._stats_path(), "m1", "accessed", "2026-06-01T01:00:00+00:00")
        compact_stats_snapshot(store)
        assert store_mod.load_effectiveness()["m1"].accessed == 2

        # New events after compaction.
        _write_event(store_mod._stats_path(), "m1", "accessed", "2026-06-02T00:00:00+00:00")
        _write_event(store_mod._stats_path(), "m2", "loaded", "2026-06-02T00:00:00+00:00")

        eff = store_mod.load_effectiveness()
        assert eff["m1"].accessed == 3  # snapshot(2) + tail(1)
        assert eff["m2"].loaded == 1

    def test_compaction_removes_dead_rows(self, isolated_stats_dir):
        """P2-16: rows for deleted/archived memories are GC'd during compaction."""
        from mem_reflection_hermes.memory.curator.helpers import compact_stats_snapshot
        from tests._helpers import MockStore

        store = MockStore()
        store._plugin_config_override = {"curator": {"stats": {"compact_threshold_lines": 2}}}

        fm = MemoryFrontmatter.new(source="test", confidence="medium")
        fm.id = "alive"
        store.put("user", fm, "body alive")

        _write_event(store_mod._stats_path(), "alive", "accessed", "2026-06-01T00:00:00+00:00")
        _write_event(store_mod._stats_path(), "dead", "accessed", "2026-06-01T00:00:00+00:00")

        result = compact_stats_snapshot(store)
        assert result["compacted"] is True
        assert result.get("removed_dead_rows", 0) == 1

        eff = store_mod.load_effectiveness()
        assert "alive" in eff
        assert "dead" not in eff

    def test_compaction_holds_lock_against_concurrent_appends(self, isolated_stats_dir, monkeypatch):
        """P1-4: concurrent stat appends during compaction must not be lost."""
        import threading
        import time
        from mem_reflection_hermes.memory.curator.helpers import compact_stats_snapshot
        from tests._helpers import MockStore

        store = MockStore()
        store._plugin_config_override = {
            "curator": {"stats": {"compact_threshold_lines": 3}}
        }

        # Seed m1 so compaction retains its stats.
        fm = MemoryFrontmatter.new(source="test", confidence="medium")
        fm.id = "m1"
        store.put("user", fm, "body m1")

        t = "2026-06-01T00:00:00+00:00"
        stats_path = store_mod._stats_path()
        _write_event(stats_path, "m1", "accessed", t)
        _write_event(stats_path, "m1", "accessed", t)
        _write_event(stats_path, "m1", "accessed", t)

        appended = []
        barrier = threading.Barrier(2)

        def _concurrent_appender():
            # Synchronize with compaction so we append right inside the critical window.
            barrier.wait()
            time.sleep(0.01)
            for i in range(3):
                record_memory_stat("m1", "accessed")
                appended.append(i)

        appender = threading.Thread(target=_concurrent_appender)
        appender.start()
        barrier.wait()
        result = compact_stats_snapshot(store)
        appender.join(timeout=5)

        assert result["compacted"] is True
        # After compaction the snapshot plus any post-fold tail must account for all events.
        eff = store_mod.load_effectiveness()
        assert eff["m1"].accessed >= 3 + len(appended)

    def test_write_snapshot_skips_allzero_rows(self, isolated_stats_dir):
        # A memory with no activity should not appear in the snapshot.
        store_mod._write_effectiveness_snapshot(
            {
                "active": MemoryEffectiveness(accessed=3, last_event_at="2026-06-01T00:00:00+00:00"),
                "empty": MemoryEffectiveness(),  # all zero
            },
            "2026-06-01T00:00:00+00:00",
        )
        lines = store_mod._effectiveness_index_path().read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["memory_id"] == "active"
