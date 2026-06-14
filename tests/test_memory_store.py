"""Tests for MemoryStore save behavior."""
from __future__ import annotations

from core.store import MemoryFrontmatter


def test_memory_store_save_duplicate(temp_store):
    """Saving the same content twice should not create a duplicate entry."""
    store = temp_store
    body = "This is a unique memory body for duplicate testing."

    # First save
    fm1 = MemoryFrontmatter(
        id="dup-test-mem-1",
        created="2025-01-01T00:00:00",
        source="test",
        confidence="medium",
        zone="general",
    )
    store.save("user", fm1, body)

    # Second save with same body but different id
    fm2 = MemoryFrontmatter(
        id="dup-test-mem-2",
        created="2025-01-01T00:00:00",
        source="test",
        confidence="medium",
        zone="general",
    )
    store.save("user", fm2, body)

    # Count should be 1 (duplicate body detected, second save skipped)
    count = len(store.list_active())
    assert count == 1, f"Expected 1 memory after duplicate save, got {count}"
