"""
Tests for memory_bridge.py — bidirectional sync bridge.

RED phase: tests define expected behavior and should fail until
the bridge module is correctly implemented.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from memory_bridge import (
    _append_to_builtin,
    _char_count_builtin,
    _get_builtin_memory_dir,
    _is_duplicate_in_builtin,
    _is_duplicate_in_plugin,
    _read_builtin_entries,
    bridge_enabled,
    get_bridge_stats,
    mirror_builtin_to_plugin,
    mirror_plugin_to_builtin,
    reset_bridge_stats,
)
from store import MemoryFrontmatter
from tests._helpers import make_memory


# =====================================================================
# Dir A: built-in → plugin mirror
# =====================================================================


class TestMirrorBuiltinToPlugin:
    """Test that built-in memory tool writes are mirrored to plugin store."""

    def test_mirror_add_new_entry(self, temp_store):
        """After mirroring an added entry, plugin store should contain it."""
        reset_bridge_stats()

        body = "User prefers concise commit messages with semantic prefixes"
        result = mirror_builtin_to_plugin(
            action="add",
            target="memory",
            content=body,
            entries_after=[body],
            mem_store=temp_store,
        )

        assert result["mirrored"] == 1
        # Verify it made it into the plugin store
        hits = temp_store.search(body, k=5)
        assert any(body in r.body for r in hits), (
            f"Expected '{body}' in plugin store, not found"
        )
        stats = get_bridge_stats()
        assert stats["dir_a_mirror"] >= 1

    def test_mirror_add_duplicate_skipped(self, temp_store):
        """Duplicate entries should be skipped (not mirrored again)."""
        body = "This is a test memory for duplicate checking"
        # First write via mirror
        mirror_builtin_to_plugin(
            action="add", target="memory", content=body,
            entries_after=[body], mem_store=temp_store,
        )
        # Second write of same content
        result = mirror_builtin_to_plugin(
            action="add", target="memory", content=body,
            entries_after=[body, "another"],
            mem_store=temp_store,
        )

        assert result["mirrored"] == 0
        assert result.get("reason") == "duplicate" or result["skipped"] == 1

    def test_mirror_add_no_mem_store(self):
        """Without a mem_store, mirror should return 0 mirrored."""
        result = mirror_builtin_to_plugin(
            action="add", target="memory", content="something",
            entries_after=["something"], mem_store=None,
        )
        assert result["mirrored"] == 0
        assert "no mem_store" in result["errors"]

    def test_mirror_replace_finds_and_supersedes(self, temp_store):
        """Replace action should find matching entry and supersede it."""
        old_body = "Old preference: I like Python 3.9"
        new_body = "Updated: I now use Python 3.12 for all projects"

        # First seed the plugin store
        mirror_builtin_to_plugin(
            action="add", target="memory", content=old_body,
            entries_after=[old_body], mem_store=temp_store,
        )

        result = mirror_builtin_to_plugin(
            action="replace", target="memory",
            content=new_body, old_text="Python 3.9",
            entries_after=[new_body],
            mem_store=temp_store,
        )

        assert result["mirrored"] == 1
        # Verify old entry was superseded (search finds new, not old)
        hits = temp_store.search("Python 3.12", k=5)
        assert any("Python 3.12" in r.body for r in hits)
        old_hits = temp_store.search("Python 3.9", k=5)
        assert not any("Python 3.9" in r.body for r in old_hits)

    def test_mirror_remove_marks_superseded(self, temp_store):
        """Remove action should mark the corresponding entry as superseded."""
        body = "Temporary note: Remember to check this later"

        # Seed the plugin
        mirror_builtin_to_plugin(
            action="add", target="memory", content=body,
            entries_after=[body], mem_store=temp_store,
        )
        # Confirm it's there
        hits_before = temp_store.search("Temporary note", k=10)
        assert any(body in r.body for r in hits_before)

        result = mirror_builtin_to_plugin(
            action="remove", target="memory",
            content="", old_text="Temporary note",
            entries_after=[],
            mem_store=temp_store,
        )

        # Should have created a tombstone
        assert result["mirrored"] == 1, f"Expected tombstone, got {result}"
        assert result["skipped"] == 0, f"Expected no skip, got {result}"
        # Verify the tombstone is in the store
        hits_after = temp_store.search("Temporary note", k=10, include_history=True)
        tombstone = next((r for r in hits_after if r.body.startswith("[removed:")), None)
        assert tombstone is not None, "No tombstone entry found"
        assert "Temporary note" in tombstone.body

    def test_mirror_empty_content_noop(self, temp_store):
        """Empty content should result in 0 mirrored entries."""
        result = mirror_builtin_to_plugin(
            action="add", target="memory",
            content="", entries_after=[],
            mem_store=temp_store,
        )
        assert result["mirrored"] == 0

    def test_mirror_user_target_to_general(self, temp_store):
        """USER.md changes should mirror to zone='general'."""
        body = "The user prefers dark mode"
        result = mirror_builtin_to_plugin(
            action="add", target="user",
            content=body, entries_after=[body],
            mem_store=temp_store,
        )
        assert result["mirrored"] == 1
        # Verify it went to 'general' zone
        general = temp_store.list_by_zone("general")
        assert any(body in r.body for r in general)


# =====================================================================
# Dir B: plugin → built-in mirror
# =====================================================================


class TestMirrorPluginToBuiltin:
    """Test that plugin memory writes are mirrored to MEMORY.md."""

    def test_mirror_core_zone_and_short_body(self, tmp_path, monkeypatch):
        """Zone=core with short body should write to MEMORY.md."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        body = "Testing is essential for software quality"

        result = mirror_plugin_to_builtin(body=body, zone="core")

        assert result["mirrored"] is True
        assert result["target"] == "memory"
        # Verify file was written
        mem_path = tmp_path / "MEMORY.md"
        assert mem_path.exists()
        content = mem_path.read_text(encoding="utf-8")
        assert body in content

    def test_mirror_non_core_zone_skipped(self, tmp_path, monkeypatch):
        """Non-core zones should not trigger Dir B."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        body = "This is a work-related note"

        result = mirror_plugin_to_builtin(body=body, zone="work")

        assert result["mirrored"] is False
        assert result.get("reason", "").startswith("zone")

    def test_mirror_long_body_skipped(self, tmp_path, monkeypatch):
        """Bodies longer than DIR_B_MAX_CHARS should not sync."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        body = "A" * 300  # > 200 char limit

        result = mirror_plugin_to_builtin(body=body, zone="core")

        assert result["mirrored"] is False
        assert "long" in result.get("reason", "")

    def test_mirror_duplicate_skipped(self, tmp_path, monkeypatch):
        """Already-existing content should not be duplicated."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        body = "This entry already exists"

        # First write succeeds
        r1 = mirror_plugin_to_builtin(body=body, zone="core")
        assert r1["mirrored"] is True

        # Second write should be skipped as duplicate
        r2 = mirror_plugin_to_builtin(body=body, zone="core")
        assert r2["mirrored"] is False
        assert "duplicate" in r2.get("reason", "")

    def test_mirror_empty_body_noop(self, tmp_path, monkeypatch):
        """Empty body should not write anything."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        result = mirror_plugin_to_builtin(body="", zone="core")
        assert result["mirrored"] is False

    def test_mirror_capacity_check(self, tmp_path, monkeypatch):
        """When MEMORY.md is near capacity, new entries should be rejected."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        # Fill MEMORY.md over capacity (memory limit is 2200 chars)
        mem_path = tmp_path / "MEMORY.md"
        large_entry = "X" * 2300
        mem_path.write_text(large_entry, encoding="utf-8")

        result = mirror_plugin_to_builtin(body="extra entry", zone="core")
        assert result["mirrored"] is False


# =====================================================================
# Built-in file operations
# =====================================================================


class TestBuiltinFileOps:
    """Test direct MEMORY.md/USER.md file operations."""

    def test_read_builtin_entries_missing_file(self, tmp_path, monkeypatch):
        """Non-existent file returns empty list."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        entries = _read_builtin_entries("memory")
        assert entries == []

    def test_read_builtin_entries_parses_delimiter(self, tmp_path, monkeypatch):
        """Entries separated by § should be correctly parsed."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        mem_path = tmp_path / "MEMORY.md"
        content = "First entry\n§\nSecond entry"
        mem_path.write_text(content, encoding="utf-8")

        entries = _read_builtin_entries("memory")
        assert len(entries) == 2
        assert "First entry" in entries

    def test_is_duplicate_in_builtin_exists(self, tmp_path, monkeypatch):
        """Should detect existing entry."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        _append_to_builtin("memory", "test dupe")
        assert _is_duplicate_in_builtin("test dupe") is True

    def test_is_duplicate_in_builtin_missing(self, tmp_path, monkeypatch):
        """Should return False for non-existent entry."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        assert _is_duplicate_in_builtin("nonexistent") is False

    def test_append_to_builtin_adds_entry(self, tmp_path, monkeypatch):
        """Appending should write to the file."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        result = _append_to_builtin("memory", "new entry")
        assert result is True
        mem_path = tmp_path / "MEMORY.md"
        assert mem_path.exists()
        assert "new entry" in mem_path.read_text(encoding="utf-8")

    def test_append_to_builtin_duplicate(self, tmp_path, monkeypatch):
        """Duplicate append should be rejected."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        _append_to_builtin("memory", "dupe entry")
        result = _append_to_builtin("memory", "dupe entry")
        assert result is False

    def test_char_count_calculation(self, tmp_path, monkeypatch):
        """Char count should reflect total delimited size."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        _append_to_builtin("memory", "hello")
        count = _char_count_builtin("memory")
        assert count > 0

    def test_user_target_ops(self, tmp_path, monkeypatch):
        """USER.md operations should work independently."""
        monkeypatch.setattr(
            "memory_bridge._get_builtin_memory_dir",
            lambda: tmp_path,
        )
        _append_to_builtin("user", "user detail")
        user_path = tmp_path / "USER.md"
        assert user_path.exists()
        assert "user detail" in user_path.read_text(encoding="utf-8")


# =====================================================================
# Plugin store helpers
# =====================================================================


class TestPluginHelpers:
    """Test helpers that query the plugin store."""

    def test_is_duplicate_in_plugin_found(self, temp_store):
        """Should detect existing entry in plugin store."""
        body = "A plugin-only memory"
        fm = MemoryFrontmatter.new(
            source="test", confidence="medium",
            tags=[], zone="core",
        )
        temp_store.put("user", fm, body)
        assert _is_duplicate_in_plugin(body, temp_store) is True

    def test_is_duplicate_in_plugin_not_found(self, temp_store):
        """Should return False for non-existent entry."""
        assert _is_duplicate_in_plugin("definitely not there", temp_store) is False


# =====================================================================
# Config
# =====================================================================


class TestBridgeConfig:
    """Test bridge configuration helpers."""

    def test_bridge_enabled_default(self, monkeypatch):
        """Without config, bridge should be enabled by default."""
        import store
        monkeypatch.setattr(store, "plugin_config", lambda: {})
        assert bridge_enabled() is True


# =====================================================================
# Thread safety & stats
# =====================================================================


class TestStats:
    """Test bridge statistics tracking."""

    def test_stats_tracking(self, temp_store):
        """Mirror operations should increment counters."""
        reset_bridge_stats()
        mirror_builtin_to_plugin(
            action="add", target="memory",
            content="stats test",
            entries_after=["stats test", "other"],
            mem_store=temp_store,
        )
        stats = get_bridge_stats()
        assert stats["dir_a_mirror"] >= 1

    def test_reset_stats(self):
        """reset_bridge_stats should clear all counters."""
        reset_bridge_stats()
        stats = get_bridge_stats()
        for v in stats.values():
            assert v == 0


