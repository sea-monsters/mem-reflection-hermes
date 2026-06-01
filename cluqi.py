"""cluqi.py — Cross-Layer Unified Query Interface (CLUQI)

Unifies queries across three storage layers:
- Layer 1: MemoryStore (flat-file structured memories with YAML frontmatter)
- Layer 2: GraphStore (SQLite-backed Hebbian association edges)
- Layer 3: Supersedes chains (version lineage in memory frontmatter)

Provides a single query interface that joins results from all layers
with configurable scoring and filtering.

v0.9.1 feature (2026-05-31).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CLUQIResult:
    """Unified result from cross-layer query."""
    memory_id: str
    layer_scores: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)

    def total_score(self, weights: Optional[Dict[str, float]] = None) -> float:
        """Weighted sum of layer scores."""
        if weights is None:
            weights = {"memory": 0.4, "graph": 0.35, "supersedes": 0.25}
        return sum(self.layer_scores.get(k, 0.0) * weights.get(k, 0.0)
                   for k in weights)


class CLUQI:
    """Cross-Layer Unified Query Interface.

    Usage:
        cluqi = CLUQI(memory_store, graph_manager)
        results = cluqi.query("python performance", zone="work", k=10)
    """

    def __init__(self, memory_store, graph_manager=None):
        self.store = memory_store
        self.gm = graph_manager

    def query(self, query: str, *, zone: Optional[str] = None,
              tags: Optional[List[str]] = None,
              min_confidence: Optional[str] = None,
              include_superseded: bool = False,
              k: int = 10,
              weights: Optional[Dict[str, float]] = None) -> List[CLUQIResult]:
        """Unified query across all three layers.

        Args:
            query: Search query string
            zone: Filter by memory zone
            tags: Filter by tags (any match)
            min_confidence: Minimum confidence level
            include_superseded: Include superseded memories
            k: Max results
            weights: Layer scoring weights (memory, graph, supersedes)

        Returns:
            List of CLUQIResult, sorted by total score
        """
        start = time.monotonic()
        results: Dict[str, CLUQIResult] = {}

        # ---- Layer 1: MemoryStore (BM25 + effectiveness) ----
        mem_results = self._query_memory_layer(
            query, zone=zone, tags=tags,
            min_confidence=min_confidence,
            include_superseded=include_superseded,
            k=k * 3
        )
        for mem, score in mem_results:
            r = results.get(mem.id())
            if r is None:
                r = CLUQIResult(
                    memory_id=mem.id(),
                    metadata={
                        "body": mem.body[:200],
                        "zone": mem.frontmatter.zone,
                        "tags": mem.frontmatter.tags,
                        "confidence": mem.frontmatter.confidence,
                        "pinned": mem.frontmatter.pinned,
                        "supersedes": mem.frontmatter.supersedes,
                    },
                    sources=["memory"],
                )
                results[mem.id()] = r
            r.layer_scores["memory"] = max(r.layer_scores.get("memory", 0.0), score)

        # ---- Layer 2: GraphStore (Hebbian activation) ----
        if self.gm is not None:
            graph_results = self._query_graph_layer(
                query, list(results.keys()), k=k * 2
            )
            for mem_id, score in graph_results:
                r = results.get(mem_id)
                if r is None:
                    # Load memory for metadata
                    mem = self.store.get_by_id(mem_id)
                    if mem is None:
                        continue
                    r = CLUQIResult(
                        memory_id=mem_id,
                        metadata={
                            "body": mem.body[:200],
                            "zone": mem.frontmatter.zone,
                            "tags": mem.frontmatter.tags,
                            "confidence": mem.frontmatter.confidence,
                        },
                        sources=["graph"],
                    )
                    results[mem_id] = r
                r.layer_scores["graph"] = max(r.layer_scores.get("graph", 0.0), score)
                if "graph" not in r.sources:
                    r.sources.append("graph")

        # ---- Layer 3: Supersedes chain (version lineage boost) ----
        for mem_id, r in list(results.items()):
            sup_score = self._query_supersedes_layer(mem_id)
            if sup_score > 0:
                r.layer_scores["supersedes"] = sup_score
                if "supersedes" not in r.sources:
                    r.sources.append("supersedes")

        # ---- Sort and return ----
        sorted_results = sorted(
            results.values(),
            key=lambda r: r.total_score(weights),
            reverse=True,
        )
        elapsed = (time.monotonic() - start) * 1000
        logger.debug("CLUQI query '%s' returned %d results in %.1fms",
                     query, len(sorted_results[:k]), elapsed)
        return sorted_results[:k]

    def _query_memory_layer(self, query: str, **kwargs) -> List[Tuple[Any, float]]:
        """Query MemoryStore using BM25 search."""
        # Use the store's bm25_search method if available
        if hasattr(self.store, 'bm25_search'):
            return self.store.bm25_search(query, **kwargs)
        # Fallback: use _bm25_search_scored from core
        from .core import _bm25_search_scored
        memories = self.store.list_active()
        return _bm25_search_scored(memories, query, k=kwargs.get('k', 30))

    def _query_graph_layer(self, query: str, seed_ids: List[str],
                           k: int) -> List[Tuple[str, float]]:
        """Query GraphStore using activation propagation from seed memories."""
        if self.gm is None or not seed_ids:
            return []
        try:
            # Propagate activation from seed memories (up to 5 hops)
            activated = self.gm.propagate_activation(
                seed_ids, max_depth=5, min_weight=0.05
            )
            results = []
            for mem_id, info in activated.items():
                if mem_id in seed_ids:
                    continue  # Skip seeds
                score = info.get("activation", 0.0) * info.get("weight", 1.0)
                results.append((mem_id, score))
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:k]
        except Exception as e:
            logger.warning("Graph layer query failed: %s", e)
            return []

    def _query_supersedes_layer(self, mem_id: str) -> float:
        """Calculate supersedes chain score for a memory.

        Memories deeper in a supersedes chain (more recent versions)
        get a small boost. Root memories (no superseder) get base score.
        """
        mem = self.store.get_by_id(mem_id)
        if mem is None:
            return 0.0
        supersedes = mem.frontmatter.supersedes
        if not supersedes:
            return 0.1  # Base score for root memories
        # Check if this memory is the latest in its chain
        all_mems = self.store.list_active()
        is_latest = True
        for m in all_mems:
            if mem_id in (m.frontmatter.supersedes or []):
                is_latest = False
                break
        return 0.3 if is_latest else 0.15

    def get_neighbors(self, mem_id: str, min_weight: float = 0.1,
                      limit: int = 20) -> List[Dict[str, Any]]:
        """Get graph neighbors with memory metadata enrichment."""
        if self.gm is None:
            return []
        neighbors = self.gm.store.get_neighbors(mem_id, min_weight=min_weight,
                                                 limit=limit)
        for n in neighbors:
            mid = n.get("target_id")
            mem = self.store.get_by_id(mid) if mid else None
            if mem:
                n["zone"] = mem.frontmatter.zone
                n["tags"] = mem.frontmatter.tags
                n["body_preview"] = mem.body[:100]
        return neighbors

    def cross_zone_bridge(self, zone_a: str, zone_b: str,
                          min_weight: float = 0.2) -> List[Dict[str, Any]]:
        """Find memories that bridge two zones via graph edges.

        Returns memories in zone_a that have strong edges to memories in zone_b.
        """
        if self.gm is None:
            return []
        bridges = []
        mems_a = [m for m in self.store.list_active()
                  if m.frontmatter.zone == zone_a]
        for mem in mems_a:
            neighbors = self.gm.store.get_neighbors(
                mem.id(), min_weight=min_weight, limit=50
            )
            for n in neighbors:
                mid = n.get("target_id")
                target = self.store.get_by_id(mid) if mid else None
                if target and target.frontmatter.zone == zone_b:
                    bridges.append({
                        "source_id": mem.id(),
                        "source_body": mem.body[:100],
                        "target_id": mid,
                        "target_body": target.body[:100],
                        "weight": n.get("weight", 0.0),
                        "relation": n.get("relation", "unknown"),
                    })
        bridges.sort(key=lambda x: x["weight"], reverse=True)
        return bridges
