"""test_store.py — Tests for MemoryStore index tooling.

Coverage:
- rebuild_index (drop + rescan)
- validate_index (orphan detection, hash mismatch)
- prune_index (stale row removal)

Run: pytest tests/test_store.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

_spec_store = importlib.util.spec_from_file_location("_store", str(_REPO / "core" / "store.py"))
_store = importlib.util.module_from_spec(_spec_store)
sys.modules["_store"] = _store
_spec_store.loader.exec_module(_store)
MemoryStore = _store.MemoryStore
MemoryFrontmatter = _store.MemoryFrontmatter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_store():
    tmpdir = tempfile.mkdtemp(prefix="hermes_store_")
    root = Path(tmpdir) / "memories"
    root.mkdir(parents=True, exist_ok=True)
    db_path = Path(tmpdir) / "memories.db"
    store = MemoryStore(user_root=root, db_path=db_path)
    yield store
    try:
        conn = getattr(store._local, "conn", None)
        if conn is not None:
            conn.close()
    except Exception:
        pass
    import shutil as _shutil
    import time as _time
    for _attempt in range(5):
        try:
            _shutil.rmtree(tmpdir)
            break
        except PermissionError:
            _time.sleep(0.1)
    _shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# rebuild_index
# ---------------------------------------------------------------------------

class TestRebuildIndex:
    def test_rebuild_empty_store(self, temp_store):
        """rebuild_index on empty store returns zero counts."""
        store = temp_store
        result = store.rebuild_index()
        assert result["total_rows"] == 0
        assert result["orphaned_rows"] == []
        assert result["hash_mismatches"] == []

    def test_rebuild_restores_memories(self, temp_store):
        """After writing memories, rebuild restores them all."""
        store = temp_store
        for i in range(3):
            fm = MemoryFrontmatter.new(source="test")
            store.put("user", fm, f"Memory content {i}")

        result = store.rebuild_index()
        assert result["total_rows"] == 3
        assert result["orphaned_rows"] == []
        assert result["orphaned_files"] == []
        assert result["hash_mismatches"] == []

    def test_rebuild_drops_stale_rows(self, temp_store):
        """rebuild_index drops SQLite rows for deleted files."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test")
        path = store.put("user", fm, "Will be deleted")

        # Manually delete the file but keep the DB row
        path.unlink()

        result = store.rebuild_index()
        # rebuild drops tables and rescans, so stale row is gone
        assert result["total_rows"] == 0
        assert store.get(fm.id) is None


# ---------------------------------------------------------------------------
# validate_index
# ---------------------------------------------------------------------------

class TestValidateIndex:
    def test_validate_empty(self, temp_store):
        """validate_index on empty store reports zero counts."""
        store = temp_store
        result = store.validate_index()
        assert result["total_rows"] == 0
        assert result["total_disk_files"] == 0
        assert result["orphaned_rows"] == []
        assert result["orphaned_files"] == []
        assert result["hash_mismatches"] == []

    def test_validate_detects_orphaned_rows(self, temp_store):
        """validate_index detects SQLite rows with missing .md files."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test")
        path = store.put("user", fm, "Orphan candidate")
        path.unlink()  # Delete file, keep DB row

        result = store.validate_index()
        assert result["orphaned_row_count"] == 1
        assert fm.id in result["orphaned_rows"]

    def test_validate_detects_orphaned_files(self, temp_store):
        """validate_index detects .md files with no SQLite row."""
        store = temp_store
        # Write a file directly without going through put()
        fm = MemoryFrontmatter.new(source="test")
        path = store.user_root / "orphan.md"
        _store.write_memory_atomic(path, fm, "Orphan file")

        result = store.validate_index()
        assert result["orphaned_file_count"] == 1
        assert str(path) in result["orphaned_files"]

    def test_validate_no_hash_mismatch_for_fresh(self, temp_store):
        """Freshly written memories have no hash mismatches."""
        store = temp_store
        for i in range(3):
            fm = MemoryFrontmatter.new(source="test")
            store.put("user", fm, f"Fresh memory {i}")

        result = store.validate_index()
        assert result["hash_mismatch_count"] == 0


# ---------------------------------------------------------------------------
# prune_index
# ---------------------------------------------------------------------------

class TestPruneIndex:
    def test_prune_empty_store(self, temp_store):
        """prune_index on empty store returns zero pruned."""
        store = temp_store
        result = store.prune_index()
        assert result["pruned"] == 0
        assert result["pruned_ids"] == []

    def test_prune_removes_orphaned_rows(self, temp_store):
        """prune_index removes SQLite rows whose .md files are gone."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test")
        path = store.put("user", fm, "To be orphaned")
        path.unlink()

        # Verify row still exists in SQLite (get() returns None because file is gone)
        conn = store._get_conn()
        row = conn.execute("SELECT id FROM memories WHERE id = ?", (fm.id,)).fetchone()
        assert row is not None
        result = store.prune_index()
        assert result["pruned"] == 1
        assert fm.id in result["pruned_ids"]
        row_after = conn.execute("SELECT id FROM memories WHERE id = ?", (fm.id,)).fetchone()
        assert row_after is None

    def test_prune_keeps_valid_rows(self, temp_store):
        """prune_index does not touch valid rows."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test")
        store.put("user", fm, "Valid memory")

        result = store.prune_index()
        assert result["pruned"] == 0
        assert store.get(fm.id) is not None

    def test_prune_cleans_orphaned_tags(self, temp_store):
        """prune_index removes tags referencing pruned memories."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test", tags=["tag1", "tag2"])
        path = store.put("user", fm, "Tagged memory")
        path.unlink()

        # Verify tag exists before prune
        conn = store._get_conn()
        before = conn.execute(
            "SELECT COUNT(*) FROM tags WHERE memory_id = ?", (fm.id,)
        ).fetchone()[0]
        assert before == 2

        store.prune_index()

        after = conn.execute(
            "SELECT COUNT(*) FROM tags WHERE memory_id = ?", (fm.id,)
        ).fetchone()[0]
        assert after == 0


# ---------------------------------------------------------------------------
# lineage boundaries
# ---------------------------------------------------------------------------

class TestLineageBoundaries:
    def test_put_rejects_missing_supersedes_target(self, temp_store):
        """Direct store writes should not create dangling supersedes edges."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test")
        fm.supersedes = ["missing-memory"]

        with pytest.raises(ValueError, match="missing-memory"):
            store.put("user", fm, "Replacement without an existing target")

    def test_latest_for_picks_newest_successor_deterministically(self, temp_store):
        """Multiple successors are resolved by created/version/rank order."""
        store = temp_store

        root = MemoryFrontmatter.new(source="test")
        root.id = "root-memory"
        root.created = "2026-01-01T00:00:00+00:00"
        store.put("user", root, "Original preference")

        older = MemoryFrontmatter.new(source="test")
        older.id = "older-successor"
        older.created = "2026-01-02T00:00:00+00:00"
        older.supersedes = ["root-memory"]
        store.put("user", older, "Older replacement")

        newer = MemoryFrontmatter.new(source="test")
        newer.id = "newer-successor"
        newer.created = "2026-01-03T00:00:00+00:00"
        newer.supersedes = ["root-memory"]
        store.put("user", newer, "Newer replacement")

        latest = store.latest_for("root-memory")

        assert latest is not None
        assert latest.id() == "newer-successor"


# ---------------------------------------------------------------------------
# Post-delete callbacks (P2a)
# ---------------------------------------------------------------------------


class TestPostDeleteCallbacks:
    def test_callbacks_invoked_on_delete(self, temp_store):
        """Registered callbacks are called with the deleted memory's id."""
        store = temp_store
        called: list = []

        def cb(mem_id: str) -> None:
            called.append(mem_id)

        store._post_delete_callbacks.append(cb)

        fm = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", fm, "delete me")
        mid = fm.id

        result = store.delete("user", mid)
        assert result is True
        assert mid in called

    def test_callback_failure_does_not_block_delete(self, temp_store):
        """A failing callback does not prevent the delete from succeeding."""
        store = temp_store

        def failing_cb(mem_id: str) -> None:
            raise RuntimeError("callback failed")

        store._post_delete_callbacks.append(failing_cb)

        fm = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", fm, "delete me anyway")
        mid = fm.id

        result = store.delete("user", mid)
        assert result is True
        assert store.get(mid) is None


class TestEntityRebuildIntegrity:
    """P0-1: Verify _refresh_entity_links rebuild cleans orphans correctly."""

    def test_rebuild_entity_links_after_manual_corruption(self, temp_store):
        """Design intent: rebuild_index should restore entity_links from memory bodies."""
        store = temp_store

        fm = MemoryFrontmatter.new(source="test")
        fm.id = "rebuild-entity"
        store.put("user", fm, 'Use file "src/app/main.py" for testing')

        # Verify entity was extracted
        links_before = store.entity_links_for_memory("rebuild-entity")
        assert links_before, "Entity links should exist after put"

        # Manually corrupt the entity_links table - delete links but keep entities
        conn = store._get_conn()
        conn.execute("DELETE FROM entity_links WHERE memory_id = ?", ("rebuild-entity",))
        conn.commit()

        # Verify corruption - entity_links empty but memory still exists
        links_corrupt = store.entity_links_for_memory("rebuild-entity")
        assert links_corrupt == [], "Entity links should be empty after corruption"

        # Rebuild the index
        result = store.rebuild_index()

        # Verify entity links were recreated
        links_after = store.entity_links_for_memory("rebuild-entity")
        assert links_after, "Entity links should be restored after rebuild"
        assert any("main.py" in link["text"] for link in links_after)

        # No orphan entities
        orphan_count = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE id NOT IN (SELECT entity_id FROM entity_links)"
        ).fetchone()[0]
        assert orphan_count == 0


class TestMemoryUpdateConflict:
    """P0-2: Test MemoryStore.update() validates supersedes and checks conflicts."""

    def test_update_with_supersedes_does_not_crash_on_nonexistent_target(self, temp_store):
        """Design intent: update validates memory ID exists, raises ValueError for missing."""
        store = temp_store

        with pytest.raises(ValueError, match="Memory not found"):
            store.update("nonexistent-id", body="New body")


class TestHealthMetricsFallback:
    """P0-3: Test health_metrics() duplicate detection fallback when datasketch unavailable."""

    def test_duplicate_detection_without_datasketch(self, temp_store, monkeypatch):
        """Design intent: health_metrics should work when datasketch not installed."""
        store = temp_store

        for i in range(5):
            fm = MemoryFrontmatter.new(source="test")
            store.put("user", fm, f"Duplicate body text is shared across {i} similar memories")

        # Remove datasketch from sys.modules to trigger fallback
        import sys
        original = sys.modules.get("datasketch")
        sys.modules["datasketch"] = None

        try:
            metrics = store.health_metrics()
            assert "duplicate_clusters" in metrics
        finally:
            if original:
                sys.modules["datasketch"] = original
            else:
                sys.modules.pop("datasketch", None)
