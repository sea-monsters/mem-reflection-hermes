"""Backend API for the mem-reflection-hermes dashboard plugin.

Exposes endpoints for memory graph visualization, skill inventory,
reflection history, and memory management (CRUD + reorder).

v0.9.2: Full ahe_graph integration — Hebbian edges, SUPERSEDES edges,
PageRank scores, cross-zone analysis, CLUQI queries.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from .. import __dict__ as _srh_dict
except ImportError:
    plugin_dir = Path(__file__).resolve().parent.parent
    if str(plugin_dir.parent) not in sys.path:
        sys.path.insert(0, str(plugin_dir.parent))
    import mem_reflection_hermes as srh  # type: ignore
else:
    class _ModuleProxy:
        def __getattr__(self, name: str) -> Any:
            return _srh_dict[name]
        def __dir__(self) -> List[str]:
            return list(_srh_dict.keys())
    srh = _ModuleProxy()

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models for request validation
# ---------------------------------------------------------------------------

class MemoryCreate(BaseModel):
    body: str
    zone: str = "general"
    confidence: str = "medium"
    tags: List[str] = []
    pinned: bool = False
    scope: Literal["user", "project"] = "user"


class MemoryUpdate(BaseModel):
    body: Optional[str] = None
    zone: Optional[str] = None
    confidence: Optional[str] = None
    tags: Optional[List[str]] = None
    pinned: Optional[bool] = None


class MemoryReorder(BaseModel):
    memory_ids: List[str]  # New ordering of memory IDs


# ---------------------------------------------------------------------------
# Helper: serialize LoadedMemory to dict
# ---------------------------------------------------------------------------

def _get_store():
    """Get the memory store instance."""
    return srh._get_mem_store()


def _memory_to_dict(m: srh.LoadedMemory) -> Dict[str, Any]:
    created_val = m.frontmatter.created
    if hasattr(created_val, 'isoformat'):
        created_str = created_val.isoformat()
    elif isinstance(created_val, str):
        created_str = created_val
    else:
        created_str = str(created_val) if created_val else None
    return {
        "id": m.id(),
        "scope": m.scope,
        "body": m.body,
        "confidence": m.frontmatter.confidence,
        "pinned": m.frontmatter.pinned,
        "tags": m.frontmatter.tags,
        "supersedes": m.frontmatter.supersedes,
        "supersedes_reason": m.frontmatter.supersedes_reason,
        "zone": m.frontmatter.zone,
        "rank": getattr(m.frontmatter, "rank", 0),
        "created": created_str,
        "valid_from": m.frontmatter.valid_from,
        "valid_until": m.frontmatter.valid_until,
        "context_scope": m.frontmatter.context_scope,
        "source": m.frontmatter.source,
    }


def _get_graph_manager():
    """Get the graph manager if available."""
    try:
        from ..graph.ahe_graph import get_graph_manager
        return get_graph_manager()
    except Exception:
        return None


def _get_cluqi():
    """Get CLUQI instance if available."""
    try:
        from ..graph.cluqi import CLUQI
        gm = _get_graph_manager()
        return CLUQI(srh._get_mem_store(), gm)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Memories (CRUD + reorder)
# ---------------------------------------------------------------------------

@router.get("/memories")
async def list_memories(
    zone: Optional[str] = None,
    query: Optional[str] = None,
    sort: Literal["rank", "created", "confidence", "zone"] = "rank",
):
    """List memories with optional zone filter, search, and sorting."""
    memories = _get_store().list_active()
    if zone:
        memories = [m for m in memories if m.frontmatter.zone == zone]
    if query:
        memories = _get_store().search(query, k=100)
        if zone:
            memories = [m for m in memories if m.frontmatter.zone == zone]

    sort_key = {
        "rank": lambda m: getattr(m.frontmatter, "rank", 0),
        "created": lambda m: m.frontmatter.created or "",
        "confidence": lambda m: {"high": 3, "medium": 2, "low": 1}.get(
            m.frontmatter.confidence, 0
        ),
        "zone": lambda m: m.frontmatter.zone or "",
    }.get(sort, lambda m: 0)

    memories.sort(key=sort_key, reverse=(sort == "rank"))
    return {"memories": [_memory_to_dict(m) for m in memories]}


@router.post("/memories")
async def create_memory(payload: MemoryCreate):
    """Create a new memory and auto-associate in graph."""
    result = srh._tool_srh_memory_write({
        "body": payload.body,
        "zone": payload.zone,
        "confidence": payload.confidence,
        "tags": payload.tags,
        "pinned": payload.pinned,
        "scope": payload.scope,
    })
    # Parse tool result and propagate errors
    try:
        result_obj = json.loads(result)
        if isinstance(result_obj, dict) and "error" in result_obj:
            raise HTTPException(status_code=400, detail=result_obj["error"])
    except json.JSONDecodeError:
        pass  # Non-JSON result (e.g., success string), proceed

    # Auto-associate with related memories in graph
    gm = _get_graph_manager()
    if gm:
        try:
            # Find memories with overlapping tags
            all_mems = _get_store().list_active()
            new_mem = None
            for m in all_mems:
                if m.body == payload.body:
                    new_mem = m
                    break
            if new_mem:
                related = [m.id() for m in all_mems
                          if m.id() != new_mem.id()
                          and set(m.frontmatter.tags or []) & set(payload.tags)]
                if related:
                    gm.associator.on_memory_coactivation([new_mem.id()] + related)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Graph auto-associate failed: %s", e)
    return {"status": "ok", "result": result}


@router.put("/memories/{mem_id}")
async def update_memory(mem_id: str, payload: MemoryUpdate):
    """Update a memory's content or metadata."""
    mem = _get_store().update(
        mem_id,
        body=payload.body,
        zone=payload.zone,
        confidence=payload.confidence,
        tags=payload.tags,
        pinned=payload.pinned,
    )
    # Update graph meta if zone changed
    if payload.zone:
        gm = _get_graph_manager()
        if gm:
            try:
                gm.store.ensure_meta(mem_id, zone=payload.zone)
            except Exception:
                pass
    return _memory_to_dict(mem)


@router.delete("/memories/{mem_id}")
async def delete_memory(mem_id: str):
    """Delete a memory and clean up graph edges."""
    ok = _get_store().delete("user", mem_id)
    if not ok:
        ok = _get_store().delete("project", mem_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    # Clean up graph edges
    gm = _get_graph_manager()
    if gm:
        try:
            # Remove all edges connected to this memory
            with gm.store._connect() as conn:
                conn.execute("DELETE FROM graph_edges WHERE source_id=? OR target_id=?", (mem_id, mem_id))
                conn.execute("DELETE FROM graph_memory_meta WHERE id=?", (mem_id,))
                conn.commit()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Graph cleanup failed for %s: %s", mem_id, e)
    return {"status": "deleted", "id": mem_id}


@router.post("/memories/reorder")
async def reorder_memories(payload: MemoryReorder):
    """Reorder memories by rank."""
    _get_store().reorder(payload.memory_ids)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Graph (v0.9.2: real ahe_graph integration)
# ---------------------------------------------------------------------------

@router.get("/graph")
async def get_graph(
    zone: Optional[str] = None,
    min_weight: float = 0.1,
    include_supersedes: bool = True,
):
    """Return the memory graph with real Hebbian edges from ahe_graph.

    v0.9.2: Now includes:
    - Hebbian co_occurs edges from SQLite graph_store
    - SUPERSEDES edges from graph_store
    - Skill tag overlap edges (computed)
    - PageRank scores on nodes
    """
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    seen_nodes: set = set()

    # Get all active memories as nodes
    memories = _get_store().list_active()
    if zone:
        memories = [m for m in memories if m.frontmatter.zone == zone]

    # Build memory nodes
    for mem in memories:
        seen_nodes.add(mem.id())
        nodes.append({
            "id": mem.id(),
            "type": "memory",
            "label": mem.body[:60] + "..." if len(mem.body) > 60 else mem.body,
            "zone": mem.frontmatter.zone,
            "confidence": mem.frontmatter.confidence,
            "pinned": mem.frontmatter.pinned,
            "tags": mem.frontmatter.tags,
        })

    # Get graph edges from ahe_graph
    gm = _get_graph_manager()
    pagerank_scores: Dict[str, float] = {}
    if gm:
        try:
            # Get real Hebbian edges
            for mem in memories:
                neighbors = gm.store.get_neighbors(
                    mem.id(), min_weight=min_weight, limit=50
                )
                for n in neighbors:
                    tgt = n.get("target_id")
                    if tgt and tgt in seen_nodes:
                        edges.append({
                            "source": mem.id(),
                            "target": tgt,
                            "relation": n.get("relation", "co_occurs"),
                            "weight": round(n.get("weight", 0.0), 3),
                            "type": "hebbian",
                        })

            # Get SUPERSEDES edges
            if include_supersedes:
                for mem in memories:
                    if mem.frontmatter.supersedes:
                        for old_id in mem.frontmatter.supersedes:
                            if old_id in seen_nodes:
                                edges.append({
                                    "source": old_id,
                                    "target": mem.id(),
                                    "relation": "SUPERSEDES",
                                    "weight": 0.95,
                                    "type": "supersedes",
                                })
                                # Also add to graph store if not already there
                                try:
                                    gm.store.add_supersedes_edge(old_id, mem.id())
                                except Exception:
                                    pass

            # Compute PageRank
            try:
                from ..graph.pagerank import compute_pagerank
                pagerank_scores = compute_pagerank(gm.store)
                for node in nodes:
                    node["pagerank"] = round(pagerank_scores.get(node["id"], 0.0), 4)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("PageRank computation failed: %s", e)

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Graph query failed: %s", e)
    else:
        # Fallback: only supersedes edges from flat files
        if include_supersedes:
            for mem in memories:
                if mem.frontmatter.supersedes:
                    for old_id in mem.frontmatter.supersedes:
                        if old_id in seen_nodes:
                            edges.append({
                                "source": old_id,
                                "target": mem.id(),
                                "relation": "SUPERSEDES",
                                "weight": 0.95,
                                "type": "supersedes",
                            })

    # Skill tag overlap edges
    try:
        skill_store = srh.SkillStore(
            srh._user_memories_dir(),
            srh._project_memories_dir(),
        )
        skills = skill_store.list()
        skill_nodes = []
        for sk in skills:
            sk_name = sk.frontmatter.name if sk.frontmatter else "unknown"
            if sk_name not in seen_nodes:
                seen_nodes.add(sk_name)
                skill_nodes.append({
                    "id": sk_name,
                    "type": "skill",
                    "label": sk_name,
                    "tags": sk.frontmatter.triggers if sk.frontmatter else [],
                })
        nodes.extend(skill_nodes)

        # Tag overlap edges between memories and skills
        mem_tags: Dict[str, List[str]] = {
            m.id(): m.frontmatter.tags or [] for m in memories
        }
        skill_tags: Dict[str, List[str]] = {
            (sk.frontmatter.name if sk.frontmatter else "unknown"): (sk.frontmatter.triggers if sk.frontmatter else [])
            for sk in skills
        }
        for mem_id, mtags in mem_tags.items():
            for sk_name, stags in skill_tags.items():
                overlap = set(mtags) & set(stags)
                if overlap:
                    edges.append({
                        "source": mem_id,
                        "target": sk_name,
                        "relation": "tag_overlap",
                        "weight": round(len(overlap) * 0.3, 2),
                        "type": "skill",
                    })
    except Exception:
        pass

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "hebbian_edges": len([e for e in edges if e.get("type") == "hebbian"]),
            "supersedes_edges": len([e for e in edges if e.get("type") == "supersedes"]),
            "skill_edges": len([e for e in edges if e.get("type") == "skill"]),
            "pagerank_computed": len(pagerank_scores) > 0,
            "graph_semantics": "associative_coactivation",
            "graph_semantics_note": "Hebbian co-occurrence edges (memories used together), not factual entity relations",
        },
    }


@router.get("/graph/neighbors/{mem_id}")
async def get_graph_neighbors(
    mem_id: str,
    min_weight: float = 0.1,
    limit: int = 20,
):
    """Get graph neighbors for a specific memory with metadata enrichment."""
    cluqi = _get_cluqi()
    if cluqi:
        neighbors = cluqi.get_neighbors(mem_id, min_weight=min_weight, limit=limit)
        return {"memory_id": mem_id, "neighbors": neighbors}

    # Fallback to raw graph store
    gm = _get_graph_manager()
    if gm:
        neighbors = gm.store.get_neighbors(mem_id, min_weight=min_weight, limit=limit)
        return {"memory_id": mem_id, "neighbors": neighbors}

    raise HTTPException(status_code=503, detail="Graph system not available")


@router.get("/graph/zones")
async def get_zone_analysis():
    """Get cross-zone graph analysis."""
    try:
        from ..graph.cross_zone import analyze_zone_connections
        gm = _get_graph_manager()
        if gm:
            result = analyze_zone_connections(_get_store(), gm.store)
            return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Zone analysis failed: %s", e)
    return {"zone_matrix": {}, "bridge_memories": [], "zone_centrality": {}, "isolated_zones": []}


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

@router.get("/skills")
async def list_skills():
    """Return all loaded skills."""
    skill_store = srh._get_skill_store()
    skills = skill_store.list()
    return {
        "skills": [
            {
                "name": sk.frontmatter.name if sk.frontmatter else "unknown",
                "triggers": sk.frontmatter.triggers if sk.frontmatter else [],
                "description": sk.frontmatter.description if sk.frontmatter else "",
                "version": sk.frontmatter.version if sk.frontmatter else "",
                "always_active": sk.frontmatter.always_active if sk.frontmatter else False,
            }
            for sk in skills
        ]
    }


# ---------------------------------------------------------------------------
# Reflections
# ---------------------------------------------------------------------------

@router.get("/reflections")
async def list_reflections(limit: int = 50, mode: Optional[str] = None):
    """Return recent reflection history.

    Args:
        limit: Maximum number of entries to return
        mode: Filter by reflection mode (full_llm, micro_llm, embedding, embedding_micro)
    """
    log_path = srh._plugin_data_dir() / "reflect-log.jsonl"
    entries = []
    if log_path.exists():
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if mode and entry.get("mode") != mode:
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    entries.reverse()
    return {"reflections": entries[:limit]}


@router.get("/reflections/audit")
async def list_reflection_audit(limit: int = 100, decision: Optional[str] = None):
    """Return flattened reflection audit entries from all reflection logs.

    Args:
        limit: Maximum number of audit entries to return
        decision: Filter by decision type (accepted, rejected, skipped, superseded, pending)
    """
    log_path = srh._plugin_data_dir() / "reflect-log.jsonl"
    audit_entries = []
    if log_path.exists():
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp", "")
                    mode = entry.get("mode", "unknown")
                    for ae in entry.get("audit_entries", []):
                        if decision and ae.get("decision") != decision:
                            continue
                        audit_entries.append({
                            "timestamp": ts,
                            "mode": mode,
                            **ae,
                        })
                except json.JSONDecodeError:
                    continue
    audit_entries.reverse()
    return {"audit_entries": audit_entries[:limit], "total": len(audit_entries)}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_stats():
    """Return aggregate statistics."""
    memories = _get_store().list_active()
    zones: Dict[str, int] = {}
    for m in memories:
        z = m.frontmatter.zone or "general"
        zones[z] = zones.get(z, 0) + 1

    # Graph stats
    graph_stats = {"available": False}
    gm = _get_graph_manager()
    if gm:
        try:
            graph_stats = {
                "available": True,
                **gm.store.stats(),
            }
        except Exception:
            pass

    # Cache stats
    cache_stats = {"available": False}
    try:
        from ..query.cache import get_cache
        cache_stats = {
            "available": True,
            **get_cache().stats(),
        }
    except Exception:
        pass

    # Health metrics (WS-5)
    health = _get_store().health_metrics()

    return {
        "memory_count": len(memories),
        "zones": zones,
        "graph": graph_stats,
        "cache": cache_stats,
        "health": health,
    }


# ---------------------------------------------------------------------------
# CLUQI Query (v0.9.2)
# ---------------------------------------------------------------------------

@router.get("/query")
async def cluqi_query(
    q: str,
    zone: Optional[str] = None,
    k: int = 10,
):
    """Cross-layer unified query across MemoryStore, GraphStore, and Supersedes chains."""
    cluqi = _get_cluqi()
    if not cluqi:
        raise HTTPException(status_code=503, detail="CLUQI not available")
    try:
        results = cluqi.query(q, zone=zone, k=k)
        return {
            "query": q,
            "results": [
                {
                    "memory_id": r.memory_id,
                    "score": round(r.total_score(), 4),
                    "layer_scores": r.layer_scores,
                    "sources": r.sources,
                    "metadata": r.metadata,
                }
                for r in results
            ],
        }
    except Exception as e:
        import logging, uuid
        trace_id = str(uuid.uuid4())[:8]
        logging.getLogger(__name__).exception("CLUQI query failed (trace=%s)", trace_id)
        raise HTTPException(
            status_code=500,
            detail=f"Query failed. Trace ID: {trace_id}. Error: {type(e).__name__}",
        )


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------

@router.get("/zones")
async def get_zones():
    """Return all available zones and their counts."""
    result = json.loads(srh._tool_srh_palace_zones({}))
    return result
