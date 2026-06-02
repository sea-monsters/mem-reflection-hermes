"""cross_zone.py — Cross-zone graph analysis for mem-reflection-hermes.

Analyzes relationships between memory zones using the Hebbian graph:
- Zone-to-zone association strength
- Bridge memories (connecting multiple zones)
- Zone centrality (which zones are most connected)

v0.9.1 feature (2026-05-31).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def analyze_zone_connections(memory_store, graph_store) -> Dict[str, Any]:
    """Analyze connections between all zones in the memory palace.

    Returns:
        Dict with:
        - zone_matrix: zone_a -> zone_b -> total edge weight
        - bridge_memories: memories with edges to other zones
        - zone_centrality: zone -> centrality score
        - isolated_zones: zones with no cross-zone connections
    """
    memories = memory_store.list_active()
    zones: Set[str] = set()
    mem_zone: Dict[str, str] = {}
    for m in memories:
        z = m.frontmatter.zone or "general"
        zones.add(z)
        mem_zone[m.id()] = z

    # Zone-to-zone edge weight matrix
    zone_matrix: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    bridge_memories: List[Dict[str, Any]] = []
    zone_degree: Dict[str, int] = defaultdict(int)

    for mem in memories:
        mid = mem.id()
        src_zone = mem_zone.get(mid, "general")
        neighbors = graph_store.get_neighbors(mid, min_weight=0.1, limit=100)
        cross_zone_edges = []
        for n in neighbors:
            tgt = n.get("target_id")
            tgt_zone = mem_zone.get(tgt)
            if tgt_zone and tgt_zone != src_zone:
                w = n.get("weight", 0.0)
                zone_matrix[src_zone][tgt_zone] += w
                cross_zone_edges.append({
                    "target_id": tgt,
                    "target_zone": tgt_zone,
                    "weight": w,
                    "relation": n.get("relation", "unknown"),
                })
        if cross_zone_edges:
            bridge_memories.append({
                "memory_id": mid,
                "zone": src_zone,
                "body_preview": mem.body[:100],
                "cross_zone_edges": cross_zone_edges,
                "bridge_strength": sum(e["weight"] for e in cross_zone_edges),
            })
            zone_degree[src_zone] += len(cross_zone_edges)

    # Zone centrality: sum of outgoing cross-zone weights
    zone_centrality: Dict[str, float] = {}
    for z in zones:
        total = sum(zone_matrix[z].values())
        zone_centrality[z] = total

    # Normalize centrality
    max_cent = max(zone_centrality.values()) if zone_centrality else 1.0
    if max_cent > 0:
        zone_centrality = {k: v / max_cent for k, v in zone_centrality.items()}

    # Isolated zones
    connected_zones = set(zone_centrality.keys())
    isolated = [z for z in zones if z not in connected_zones or zone_centrality.get(z, 0) == 0]

    # Sort bridge memories by strength
    bridge_memories.sort(key=lambda x: x["bridge_strength"], reverse=True)

    return {
        "zone_matrix": dict(zone_matrix),
        "bridge_memories": bridge_memories[:50],  # Cap at 50
        "zone_centrality": zone_centrality,
        "zone_degree": dict(zone_degree),
        "isolated_zones": isolated,
        "zone_count": len(zones),
        "total_bridge_memories": len(bridge_memories),
    }


def get_zone_recommendations(memory_store, graph_store,
                             target_zone: str,
                             k: int = 5) -> List[Dict[str, Any]]:
    """Recommend memories from other zones that should be linked to target_zone.

    Based on bridge memories: find memories in other zones that are strongly
    connected to memories already in target_zone.
    """
    memories = memory_store.list_active()
    target_mems = [m for m in memories if m.frontmatter.zone == target_zone]
    recommendations: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for tm in target_mems:
        neighbors = graph_store.get_neighbors(tm.id(), min_weight=0.15, limit=50)
        for n in neighbors:
            mid = n.get("target_id")
            if not mid or mid in seen:
                continue
            mem = memory_store.get_by_id(mid)
            if mem is None or mem.frontmatter.zone == target_zone:
                continue
            seen.add(mid)
            recommendations.append({
                "memory_id": mid,
                "zone": mem.frontmatter.zone,
                "body_preview": mem.body[:100],
                "connection_to_target": tm.id(),
                "target_preview": tm.body[:50],
                "weight": n.get("weight", 0.0),
                "relation": n.get("relation", "unknown"),
            })

    recommendations.sort(key=lambda x: x["weight"], reverse=True)
    return recommendations[:k]
