"""test_query_cache.py — Query templates and TTL result cache tests.

Tests:
- Template registration and query building
- TTL-based caching (set, get, expiry)
- Cache invalidation

Run: pytest tests/test_query_cache.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_spec_search = importlib.util.spec_from_file_location("_search", str(_REPO / "search.py"))
_search = importlib.util.module_from_spec(_spec_search)
sys.modules["_search"] = _search
assert _spec_search is not None and _spec_search.loader is not None
_spec_search.loader.exec_module(_search)

QueryTemplate = _search.QueryTemplate
ResultCache = _search.ResultCache
QUERY_TEMPLATES = _search.QUERY_TEMPLATES
build_query = _search.build_query
get_cache = _search.get_cache


class TestQueryTemplates:
    def test_recent_template(self):
        result = build_query("recent")
        assert result["type"] == "recent"
        assert "k" in result

    def test_recent_with_zone(self):
        result = build_query("recent", zone="work")
        assert result["zone"] == "work"

    def test_by_zone_template(self):
        result = build_query("by_zone", zone="core")
        assert result["type"] == "zone"
        assert result["zone"] == "core"

    def test_by_tag_template(self):
        result = build_query("by_tag", tags=["python", "backend"])
        assert result["type"] == "tag"
        assert result["tags"] == ["python", "backend"]

    def test_unknown_template_raises(self):
        with pytest.raises(ValueError, match="Unknown template"):
            build_query("nonexistent_template")

    def test_all_templates_registered(self):
        expected = {"recent", "by_zone", "by_tag", "by_effectiveness",
                    "graph_neighbors", "cross_zone_bridge", "pagerank_hubs",
                    "supersedes_chain"}
        assert expected.issubset(set(QUERY_TEMPLATES.keys()))


class TestResultCache:
    def test_set_and_get(self):
        cache = ResultCache(default_ttl=60.0)
        cache.set("result_data", "key1", query="test")
        result = cache.get("key1", query="test")
        assert result == "result_data"

    def test_miss_returns_none(self):
        cache = ResultCache(default_ttl=60.0)
        result = cache.get("nonexistent_key")
        assert result is None

    def test_ttl_expiry(self):
        cache = ResultCache(default_ttl=0.05)  # 50ms TTL
        cache.set("data", "short_lived")
        # Should be available immediately
        assert cache.get("short_lived") == "data"
        # Wait for expiry
        time.sleep(0.1)
        assert cache.get("short_lived") is None

    def test_invalidate_specific(self):
        cache = ResultCache(default_ttl=60.0)
        cache.set("data_a", "key_a")
        cache.set("data_b", "key_b")
        cache.invalidate("key_a")
        assert cache.get("key_a") is None
        assert cache.get("key_b") == "data_b"

    def test_clear_all(self):
        cache = ResultCache(default_ttl=60.0)
        cache.set("data1", "k1")
        cache.set("data2", "k2")
        cache.clear()
        assert cache.get("k1") is None
        assert cache.get("k2") is None

    def test_stats(self):
        cache = ResultCache(default_ttl=60.0)
        cache.set("data", "k1")
        cache.get("k1")   # hit
        cache.get("miss")  # miss
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_max_size_eviction(self):
        cache = ResultCache(default_ttl=60.0, max_size=2)
        cache.set("a", "k1")
        cache.set("b", "k2")
        cache.set("c", "k3")  # Should evict oldest
        assert cache.stats()["size"] <= 2
