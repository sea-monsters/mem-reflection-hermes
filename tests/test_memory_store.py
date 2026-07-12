"""Tests for MemoryStore save/put behavior."""
from __future__ import annotations

import pytest

from core.store import MemoryFrontmatter


def test_memory_store_save_duplicate(temp_store):
    """MemoryStore.put() dedups on memory id, not on body content.

    Contract: a memory is uniquely identified by its id. Saving the same id
    again raises ValueError (rejected, not silently skipped). Two different
    ids with identical body content are distinct memories and both are kept --
    body-based dedup is intentionally NOT performed (replacement is expressed
    via frontmatter.supersedes, not via matching bodies).
    """
    store = temp_store
    body = "This is a unique memory body for duplicate testing."

    fm1 = MemoryFrontmatter(
        id="dup-test-mem-1",
        created="2025-01-01T00:00:00",
        source="test",
        confidence="medium",
        zone="general",
    )
    store.put("user", fm1, body)

    # Second put with a DIFFERENT id but same body -> both stored (no body dedup)
    fm2 = MemoryFrontmatter(
        id="dup-test-mem-2",
        created="2025-01-01T00:00:00",
        source="test",
        confidence="medium",
        zone="general",
    )
    store.put("user", fm2, body)
    assert len(store.list_active()) == 2, "distinct ids must both be stored"

    # Re-putting the SAME id is rejected (id-level dedup), not silently skipped
    with pytest.raises(ValueError, match="Duplicate memory id"):
        store.put("user", fm1, body)
