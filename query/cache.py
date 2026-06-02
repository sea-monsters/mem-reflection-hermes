"""query_cache.py — Query templates and result cache for mem-reflection-hermes.

Provides:
- Predefined query templates for common access patterns
- TTL-based result cache to avoid redundant BM25/graph computations

v0.9.1 feature (2026-05-31).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Query Templates
# ---------------------------------------------------------------------------

@dataclass
class QueryTemplate:
    """Named query template with parameters."""
    name: str
    description: str
    builder: Callable[..., Dict[str, Any]]


QUERY_TEMPLATES: Dict[str, QueryTemplate] = {}


def register_template(name: str, description: str):
    """Decorator to register a query template."""
    def decorator(fn: Callable[..., Dict[str, Any]]):
        QUERY_TEMPLATES[name] = QueryTemplate(name, description, fn)
        return fn
    return decorator


@register_template("recent", "Most recently created memories")
def tpl_recent(zone: Optional[str] = None, k: int = 10) -> Dict[str, Any]:
    return {"type": "recent", "zone": zone, "k": k, "sort": "created"}


@register_template("by_zone", "Memories in a specific zone")
def tpl_by_zone(zone: str, k: int = 50) -> Dict[str, Any]:
    return {"type": "zone", "zone": zone, "k": k}


@register_template("by_tag", "Memories matching any of the given tags")
def tpl_by_tag(tags: List[str], k: int = 20) -> Dict[str, Any]:
    return {"type": "tag", "tags": tags, "k": k}


@register_template("by_effectiveness", "Highest effectiveness memories")
def tpl_by_effectiveness(k: int = 10) -> Dict[str, Any]:
    return {"type": "effectiveness", "k": k, "sort": "effectiveness"}


@register_template("graph_neighbors", "Graph neighbors of a seed memory")
def tpl_graph_neighbors(memory_id: str, min_weight: float = 0.1,
                        k: int = 20) -> Dict[str, Any]:
    return {"type": "graph_neighbors", "memory_id": memory_id,
            "min_weight": min_weight, "k": k}


@register_template("cross_zone_bridge", "Memories bridging two zones")
def tpl_cross_zone_bridge(zone_a: str, zone_b: str,
                          min_weight: float = 0.2) -> Dict[str, Any]:
    return {"type": "cross_zone_bridge", "zone_a": zone_a, "zone_b": zone_b,
            "min_weight": min_weight}


@register_template("pagerank_hubs", "Top PageRank hub memories")
def tpl_pagerank_hubs(k: int = 10, zone: Optional[str] = None) -> Dict[str, Any]:
    return {"type": "pagerank", "k": k, "zone": zone}


@register_template("supersedes_chain", "Version lineage of a memory")
def tpl_supersedes_chain(memory_id: str) -> Dict[str, Any]:
    return {"type": "supersedes_chain", "memory_id": memory_id}


def build_query(template_name: str, **kwargs) -> Dict[str, Any]:
    """Build a query dict from a registered template."""
    tpl = QUERY_TEMPLATES.get(template_name)
    if tpl is None:
        raise ValueError(f"Unknown template: {template_name}. "
                         f"Available: {list(QUERY_TEMPLATES.keys())}")
    return tpl.builder(**kwargs)


# ---------------------------------------------------------------------------
# Result Cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    result: Any
    expires_at: float
    last_access: float = 0.0
    access_count: int = 0


class ResultCache:
    """TTL-based result cache for query results.

    Usage:
        cache = ResultCache(default_ttl=300)  # 5 minutes
        key = cache_key("bm25", query="python", zone="work")
        result = cache.get(key)
        if result is None:
            result = expensive_query(...)
            cache.set(key, result)
    """

    def __init__(self, default_ttl: float = 300.0, max_size: int = 1000):
        self.default_ttl = default_ttl
        self.max_size = max_size
        self._store: Dict[str, _CacheEntry] = {}
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def _make_key(self, *args, **kwargs) -> str:
        """Deterministic cache key from args."""
        data = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True,
                          default=str)
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    def get(self, *args, **kwargs) -> Optional[Any]:
        """Get cached result if not expired."""
        key = self._make_key(*args, **kwargs)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                self._misses += 1
                return None
            entry.access_count += 1
            entry.last_access = time.monotonic()
            self._hits += 1
            return entry.result

    def set(self, result: Any, *args, ttl: Optional[float] = None,
            **kwargs) -> str:
        """Cache a result with optional custom TTL."""
        key = self._make_key(*args, **kwargs)
        expires = time.monotonic() + (ttl or self.default_ttl)
        with self._lock:
            # M17: evict least recently accessed, not earliest expiry
            if len(self._store) >= self.max_size:
                lru = min(self._store.items(), key=lambda x: x[1].last_access)
                del self._store[lru[0]]
            self._store[key] = _CacheEntry(result, expires, last_access=time.monotonic())
        return key

    def invalidate(self, *args, **kwargs) -> bool:
        """Invalidate a specific cache entry."""
        key = self._make_key(*args, **kwargs)
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def invalidate_pattern(self, pattern: str) -> int:
        """Pattern invalidation is unsupported because cache keys are opaque hashes."""
        logger.warning("ResultCache.invalidate_pattern ignored opaque hash pattern: %s", pattern)
        return 0

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._store.clear()

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0.0
            return {
                "size": len(self._store),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 4),
                "default_ttl": self.default_ttl,
            }


# Global cache instance (lazy-initialized)
_global_cache: Optional[ResultCache] = None
_global_cache_lock = threading.Lock()


def get_cache() -> ResultCache:
    """Get the global result cache instance."""
    global _global_cache
    if _global_cache is None:
        with _global_cache_lock:
            if _global_cache is None:
                _global_cache = ResultCache()
    return _global_cache
