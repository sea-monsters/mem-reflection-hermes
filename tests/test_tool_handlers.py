"""test_tool_handlers.py — Tool handler business logic tests.

Tests the pure logic portions of tool handlers:
- Lineage cycle detection
- Lineage helpers (root, latest)
- Memory write/read cycle

These tests mock the framework dependencies to isolate handler logic.

Run: pytest tests/test_tool_handlers.py -v
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from core import (
    MemoryFrontmatter,
    LoadedMemory,
    _lineage_cycle_check,
    _lineage_latest,
    _lineage_root,
    serialize_frontmatter,
    parse_frontmatter,
    read_memory,
)


# ---------------------------------------------------------------------------
# Minimal store mock for lineage tests
# ---------------------------------------------------------------------------

class _DictStore:
    """Minimal store-like object with .get() and .list() methods."""

    def __init__(self, mems: Dict[str, LoadedMemory]):
        self._mems = mems

    def get(self, mem_id: str):
        return self._mems.get(mem_id)

    def list(self):
        return list(self._mems.values())


def _make_store(chain: Dict[str, list]) -> _DictStore:
    """Create a store from {id: supersedes_list}."""
    mems = {}
    for mid, supersedes in chain.items():
        fm = MemoryFrontmatter(
            id=mid,
            created=datetime.now(timezone.utc).isoformat(),
            source="test",
            confidence="medium",
            supersedes=supersedes,
        )
        mems[mid] = LoadedMemory(
            frontmatter=fm,
            body=f"body {mid}",
            source_path=Path(f"/tmp/{mid}.md"),
            scope="user",
        )
    return _DictStore(mems)


# ---------------------------------------------------------------------------
# Cycle detection tests
# ---------------------------------------------------------------------------

class TestLineageCycleCheck:
    def test_no_cycle(self):
        store = _make_store({"a": [], "b": ["a"], "c": ["b"]})
        result = _lineage_cycle_check(store, "c")
        assert result is None

    def test_direct_cycle(self):
        store = _make_store({"a": ["b"], "b": ["a"]})
        result = _lineage_cycle_check(store, "a")
        assert result is not None and len(result) > 0

    def test_three_node_cycle(self):
        store = _make_store({"a": ["b"], "b": ["c"], "c": ["a"]})
        result = _lineage_cycle_check(store, "a")
        assert result is not None and len(result) > 0

    def test_self_cycle(self):
        store = _make_store({"a": ["a"]})
        result = _lineage_cycle_check(store, "a")
        assert result is not None and len(result) > 0

    def test_no_chain_no_cycle(self):
        store = _make_store({"a": [], "b": []})
        result = _lineage_cycle_check(store, "a")
        assert result is None


# ---------------------------------------------------------------------------
# Lineage helpers tests
# ---------------------------------------------------------------------------

class TestLineageHelpers:
    def test_lineage_root_chain(self):
        """Root of chain a->b->c is c."""
        store = _make_store({"a": ["b"], "b": ["c"], "c": []})
        root = _lineage_root(store, "a")
        assert root == "c"

    def test_lineage_root_self(self):
        """Memory with no supersedes returns itself."""
        store = _make_store({"a": []})
        root = _lineage_root(store, "a")
        assert root == "a"

    def test_lineage_latest_chain(self):
        """Latest finds forward successor in chain."""
        # a is superseded by b (someone wrote b with supersedes=["a"])
        # We need a store where b.frontmatter.supersedes includes "a"
        store = _make_store({"a": [], "b": ["a"], "c": ["b"]})
        latest = _lineage_latest(store, "a")
        assert latest is not None
        assert latest in ("b", "c")


# ---------------------------------------------------------------------------
# Write/read cycle
# ---------------------------------------------------------------------------

class TestWriteReadCycle:
    def test_write_and_read(self, temp_dir):
        body = "Integration test memory content"
        data = {
            "id": "integration-test-1",
            "created": datetime.now(timezone.utc).isoformat(),
            "source": "test",
            "confidence": "high",
            "tags": ["integration"],
            "zone": "work",
        }
        content = serialize_frontmatter(data, body)
        path = temp_dir / "integration_test.md"
        path.write_text(content, encoding="utf-8")

        loaded = read_memory(path, "user")
        assert loaded is not None
        assert loaded.id() == "integration-test-1"
        assert loaded.body == body
        assert loaded.frontmatter.tags == ["integration"]
        assert loaded.frontmatter.zone == "work"
