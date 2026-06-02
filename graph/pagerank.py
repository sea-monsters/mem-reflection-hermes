"""pagerank.py — PageRank centrality for ahe_graph.

Implements iterative PageRank computation on the Hebbian association graph.
Used for identifying "hub" memories that are highly connected and influential.

v0.9.1 feature (2026-05-31).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def compute_pagerank(graph_store, damping: float = 0.85,
                     max_iterations: int = 100,
                     convergence_threshold: float = 1e-6) -> Dict[str, float]:
    """Compute PageRank scores for all nodes in the graph.

    Args:
        graph_store: GraphStore instance with get_all_nodes() and get_neighbors()
        damping: Damping factor (default 0.85)
        max_iterations: Max iterations before stopping
        convergence_threshold: L1 norm difference threshold for convergence

    Returns:
        Dict mapping node_id -> PageRank score
    """
    nodes = graph_store.get_all_nodes()
    if not nodes:
        return {}

    node_ids = [n["memory_id"] for n in nodes]
    n = len(node_ids)
    if n == 0:
        return {}

    # Initialize scores uniformly
    scores: Dict[str, float] = {nid: 1.0 / n for nid in node_ids}

    # Pre-compute outgoing edges and build reverse adjacency table
    # (beta3: O(n^2*d) -> O(n*d) by indexing incoming contributions)
    outgoing: Dict[str, List[Tuple[str, float]]] = {}
    incoming: Dict[str, List[Tuple[str, float]]] = {nid: [] for nid in node_ids}
    for nid in node_ids:
        neighbors = graph_store.get_neighbors(nid, min_weight=0.0, limit=1000)
        edges = []
        for n in neighbors:
            tgt = n.get("target_id")
            if tgt and tgt in scores:
                w = n.get("weight", 1.0)
                edges.append((tgt, w))
                incoming[tgt].append((nid, w))
        outgoing[nid] = edges

    for iteration in range(max_iterations):
        new_scores: Dict[str, float] = {}
        for nid in node_ids:
            rank = (1.0 - damping) / n
            for src_id, weight in incoming.get(nid, []):
                out_degree = len(outgoing.get(src_id, []))
                if out_degree > 0:
                    rank += damping * scores[src_id] * weight / out_degree
            new_scores[nid] = rank

        # Check convergence (L1 norm)
        diff = sum(abs(new_scores[nid] - scores[nid]) for nid in node_ids)
        scores = new_scores
        if diff < convergence_threshold:
            logger.debug("PageRank converged in %d iterations (diff=%.2e)",
                         iteration + 1, diff)
            break

    # Normalize to [0, 1]
    max_score = max(scores.values()) if scores else 1.0
    if max_score > 0:
        scores = {k: v / max_score for k, v in scores.items()}

    return scores


def get_top_pagerank(graph_store, k: int = 10,
                     zone: Optional[str] = None) -> List[Tuple[str, float]]:
    """Get top-k nodes by PageRank score, optionally filtered by zone.

    Args:
        graph_store: GraphStore instance
        k: Number of top nodes to return
        zone: Optional zone filter (requires graph_store nodes to have zone metadata)

    Returns:
        List of (node_id, score) tuples, sorted by score descending
    """
    scores = compute_pagerank(graph_store)
    if zone:
        # Filter by zone if metadata available
        filtered = []
        for nid, score in scores.items():
            meta = graph_store.get_meta(nid)
            if meta and meta.get("zone") == zone:
                filtered.append((nid, score))
        filtered.sort(key=lambda x: x[1], reverse=True)
        return filtered[:k]
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_scores[:k]
