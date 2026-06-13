"""Regression test for filter-after-truncate bug in _tool_srh_palace_recall.

Issue: palace_recall called mem_store.search(query, k=k*3) without passing zone,
then filtered the truncated results by zone. If out-of-zone memories ranked higher,
the user got zero in-zone results even when relevant in-zone memories existed.

Fix: pass zone=zone to search so the backend filters before ranking/truncation.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO = Path(__file__).resolve().parent.parent

# Import tools module via package (avoids relative import issues)
from mem_reflection_hermes.runtime import tools as _tools_mod

from tests._helpers import make_memory_with_id


class MockStore:
    """Minimal store that supports zone-scoped search."""

    def __init__(self, memories):
        self._memories = {m.id(): m for m in memories}
        self.search_calls: list[dict[str, Any]] = []

    def get(self, mem_id):
        return self._memories.get(mem_id)

    def search(self, query: str, k: int = 5, zone: str | None = None, **kwargs) -> list:
        """Return memories matching query, optionally filtered by zone before truncation."""
        self.search_calls.append({"query": query, "k": k, "zone": zone, **kwargs})
        # Simple relevance: count term overlap
        query_terms = set(query.lower().split())
        scored = []
        for m in self._memories.values():
            if zone is not None and m.frontmatter.zone != zone:
                continue
            filters = kwargs.get("filters") or {}
            if any(getattr(m.frontmatter, key, None) != value for key, value in filters.items()):
                continue
            mem_terms = set(m.body.lower().split())
            score = len(query_terms & mem_terms)
            if score > 0:
                scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:k]]

    def is_superseded(self, mem_id: str) -> bool:
        return False


class TestPalaceRecallZoneFilter:
    """Regression: zone filter must be applied before truncation."""

    def test_zone_filter_before_truncation_returns_in_zone_memory(self, monkeypatch):
        """When k=1 and zone is set, the in-zone memory must be returned even if
        an out-of-zone memory has higher global relevance.
        """
        # Out-of-zone memory matches more query terms → higher global relevance
        out_zone = make_memory_with_id(
            "mem-out", "apple banana cherry date elderberry", zone="general"
        )
        # In-zone memory matches fewer terms → lower global relevance
        in_zone = make_memory_with_id(
            "mem-in", "apple", zone="work"
        )

        mock_store = MockStore([out_zone, in_zone])

        # Patch _get_mem_store to return our mock
        monkeypatch.setattr(_tools_mod, "_get_mem_store", lambda: mock_store)
        # Patch graph enrichment to no-op (we only care about recall ranking)
        monkeypatch.setattr(
            _tools_mod,
            "_enrich_with_graph",
            lambda result_mids, out, k, zone_filter=None: {"results": out},
        )
        # Patch record_memory_stat to no-op
        monkeypatch.setattr(_tools_mod, "record_memory_stat", lambda mid, event: None)

        args = {
            "topic": "apple banana cherry",
            "limit": 1,
            "zone": "work",
        }
        result_json = _tools_mod._tool_srh_palace_recall(args)
        result = json.loads(result_json)

        results = result.get("results", [])
        assert len(results) == 1, f"Expected 1 result, got {len(results)}: {results}"
        assert results[0]["id"] == "mem-in", (
            f"Expected in-zone memory 'mem-in', got {results[0]['id']}"
        )
        assert results[0]["zone"] == "work"

    def test_no_zone_falls_back_to_global_recall(self, monkeypatch):
        """Without zone, global top-k should still work."""
        mem1 = make_memory_with_id(
            "mem-1", "apple banana cherry date elderberry", zone="general"
        )
        mem2 = make_memory_with_id("mem-2", "apple", zone="work")

        mock_store = MockStore([mem1, mem2])

        monkeypatch.setattr(_tools_mod, "_get_mem_store", lambda: mock_store)
        monkeypatch.setattr(
            _tools_mod,
            "_enrich_with_graph",
            lambda result_mids, out, k, zone_filter=None: {"results": out},
        )
        monkeypatch.setattr(_tools_mod, "record_memory_stat", lambda mid, event: None)

        args = {
            "topic": "apple banana cherry",
            "limit": 1,
        }
        result_json = _tools_mod._tool_srh_palace_recall(args)
        result = json.loads(result_json)

        results = result.get("results", [])
        assert len(results) == 1
        # Global top-1 should be the higher-relevance memory
        assert results[0]["id"] == "mem-1"

    def test_empty_zone_returns_empty_results(self, monkeypatch):
        """When zone has no matching memories, return empty with message."""
        mem = make_memory_with_id("mem-1", "apple banana", zone="general")

        mock_store = MockStore([mem])

        monkeypatch.setattr(_tools_mod, "_get_mem_store", lambda: mock_store)
        monkeypatch.setattr(
            _tools_mod,
            "_enrich_with_graph",
            lambda result_mids, out, k, zone_filter=None: {"results": out},
        )
        monkeypatch.setattr(_tools_mod, "record_memory_stat", lambda mid, event: None)
        # _normalize_zone maps unknown zones to "general", so patch it to preserve the test zone
        monkeypatch.setattr(_tools_mod, "_normalize_zone", lambda z: z)

        args = {
            "topic": "apple banana",
            "limit": 5,
            "zone": "nonexistent",
        }
        result_json = _tools_mod._tool_srh_palace_recall(args)
        result = json.loads(result_json)

        # MockStore.search filters by zone, so nonexistent zone returns no results
        assert result.get("results") == []
        assert "nonexistent" in result.get("message", "")

    def test_scope_filters_are_passed_to_search(self, monkeypatch):
        """Palace recall must not bypass v1.6 scope filters."""
        mem_u1 = make_memory_with_id("mem-u1", "apple scoped", zone="work")
        object.__setattr__(mem_u1.frontmatter, "user_id", "u1")
        mem_u2 = make_memory_with_id("mem-u2", "apple scoped", zone="work")
        object.__setattr__(mem_u2.frontmatter, "user_id", "u2")
        mock_store = MockStore([mem_u1, mem_u2])

        monkeypatch.setattr(_tools_mod, "_get_mem_store", lambda: mock_store)
        monkeypatch.setattr(
            _tools_mod,
            "_enrich_with_graph",
            lambda result_mids, out, k, zone_filter=None: {"results": out},
        )
        monkeypatch.setattr(_tools_mod, "record_memory_stat", lambda mid, event: None)

        result_json = _tools_mod._tool_srh_palace_recall({
            "topic": "apple",
            "limit": 5,
            "zone": "work",
            "filters": {"user_id": "u1"},
        })
        result = json.loads(result_json)

        assert mock_store.search_calls[-1]["filters"] == {"user_id": "u1"}
        assert [m["id"] for m in result["results"]] == ["mem-u1"]
