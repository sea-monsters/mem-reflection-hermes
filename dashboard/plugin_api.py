"""Backend API for the mem-reflection-hermes dashboard plugin.

Exposes endpoints for memory graph visualization, skill inventory,
reflection history, and memory management (CRUD + reorder).
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Ensure the plugin's __init__ is importable
plugin_dir = Path(__file__).resolve().parent.parent
if str(plugin_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dir))

import __init__ as srh

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
    scope: str = "user"


class MemoryUpdate(BaseModel):
    body: Optional[str] = None
    zone: Optional[str] = None
    confidence: Optional[str] = None
    tags: Optional[List[str]] = None
    pinned: Optional[bool] = None


class MemoryReorder(BaseModel):
    memory_ids: List[str]  # New ordering of memory IDs


class MemoryDelete(BaseModel):
    id: str


# ---------------------------------------------------------------------------
# Existing endpoints (graph, skills, reflections, stats)
# ---------------------------------------------------------------------------

@router.get("/memories")
async def get_memories():
    """Return all active memories with metadata."""
    store = srh._get_mem_store()
    memories = store.list_active()
    return {
        "count": len(memories),
        "memories": [
            {
                "id": m.id(),
                "scope": m.scope,
                "body": m.body,
                "confidence": m.frontmatter.confidence,
                "pinned": m.frontmatter.pinned,
                "tags": m.frontmatter.tags,
                "supersedes": m.frontmatter.supersedes,
                "created": m.frontmatter.created,
                "source": m.frontmatter.source,
                "zone": m.frontmatter.zone,
            }
            for m in memories
        ],
    }


@router.get("/skills")
async def get_skills():
    """Return all skills with metadata."""
    store = srh._get_skill_store()
    skills = store.list()
    return {
        "count": len(skills),
        "skills": [
            {
                "name": s.frontmatter.name,
                "description": s.frontmatter.description,
                "triggers": s.frontmatter.triggers,
                "scope": s.scope,
                "version": s.frontmatter.version,
                "license": s.frontmatter.license,
            }
            for s in skills
        ],
    }


@router.get("/reflections")
async def get_reflections(limit: int = 20):
    """Return recent reflection outcomes."""
    outcomes = srh._recent_reflect_outcomes(limit)
    return {"count": len(outcomes), "reflections": outcomes}


@router.get("/graph")
async def get_graph():
    """Return memory graph data for visualization (nodes + edges).

    Nodes: memories and skills.
    Edges: supersedes links, tag overlaps, skill triggers.
    """
    mem_store = srh._get_mem_store()
    skill_store = srh._get_skill_store()

    memories = mem_store.list_active()
    skills = skill_store.list()

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    # Memory nodes
    for m in memories:
        nodes.append({
            "id": m.id(),
            "type": "memory",
            "label": m.body[:60] + "..." if len(m.body) > 60 else m.body,
            "scope": m.scope,
            "confidence": m.frontmatter.confidence,
            "pinned": m.frontmatter.pinned,
            "tags": m.frontmatter.tags,
        })
        # Supersedes edges
        for old_id in m.frontmatter.supersedes:
            edges.append({
                "source": m.id(),
                "target": old_id,
                "type": "supersedes",
            })

    # Skill nodes
    for s in skills:
        nodes.append({
            "id": s.frontmatter.name,
            "type": "skill",
            "label": s.frontmatter.name,
            "description": s.frontmatter.description,
            "scope": s.scope,
            "triggers": s.frontmatter.triggers,
        })
        # Link skills to memories by tag overlap
        skill_tags = set(srh._skill_tokenise(s.frontmatter.name))
        skill_tags.update(srh._skill_tokenise(s.frontmatter.description))
        for t in s.frontmatter.triggers:
            skill_tags.update(srh._skill_tokenise(t))

        for m in memories:
            mem_tags = set(m.frontmatter.tags)
            mem_tags.update(srh._tokenise(m.body))
            overlap = skill_tags & mem_tags
            if overlap:
                edges.append({
                    "source": s.frontmatter.name,
                    "target": m.id(),
                    "type": "tag_overlap",
                    "overlap": list(overlap),
                })

    return {"nodes": nodes, "edges": edges}


@router.get("/stats")
async def get_stats():
    """Return aggregate statistics."""
    mem_store = srh._get_mem_store()
    skill_store = srh._get_skill_store()

    memories = mem_store.list_active()
    skills = skill_store.list()

    mem_by_scope: Dict[str, int] = {}
    mem_by_confidence: Dict[str, int] = {}
    tag_counts: Dict[str, int] = {}
    zone_counts: Dict[str, int] = {}

    for m in memories:
        mem_by_scope[m.scope] = mem_by_scope.get(m.scope, 0) + 1
        c = m.frontmatter.confidence
        mem_by_confidence[c] = mem_by_confidence.get(c, 0) + 1
        for t in m.frontmatter.tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
        zone_counts[m.frontmatter.zone] = zone_counts.get(m.frontmatter.zone, 0) + 1

    return {
        "memory_count": len(memories),
        "skill_count": len(skills),
        "memories_by_scope": mem_by_scope,
        "memories_by_confidence": mem_by_confidence,
        "memories_by_zone": zone_counts,
        "top_tags": sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10],
    }


# ---------------------------------------------------------------------------
# NEW: Memory Management endpoints (CRUD + reorder)
# ---------------------------------------------------------------------------

@router.post("/memories")
async def create_memory(payload: MemoryCreate):
    """Create a new memory entry manually."""
    result = srh._tool_srh_memory_write({
        "body": payload.body,
        "zone": payload.zone,
        "confidence": payload.confidence,
        "tags": payload.tags,
        "pinned": payload.pinned,
        "scope": payload.scope,
    })
    data = json.loads(result)
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"])
    return data


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: str):
    """Get a single memory by ID."""
    store = srh._get_mem_store()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {
        "id": mem.id(),
        "scope": mem.scope,
        "body": mem.body,
        "confidence": mem.frontmatter.confidence,
        "pinned": mem.frontmatter.pinned,
        "tags": mem.frontmatter.tags,
        "supersedes": mem.frontmatter.supersedes,
        "created": mem.frontmatter.created,
        "source": mem.frontmatter.source,
        "zone": mem.frontmatter.zone,
    }


@router.put("/memories/{memory_id}")
async def update_memory(memory_id: str, payload: MemoryUpdate):
    """Update an existing memory's content or metadata.

    Uses write-then-delete swap to avoid data loss on write failure.
    Re-indexes embedding after successful rewrite.
    """
    store = srh._get_mem_store()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Build updated frontmatter
    fm = srh.MemoryFrontmatter(
        id=memory_id,
        created=mem.frontmatter.created,
        source=mem.frontmatter.source,
        confidence=payload.confidence or mem.frontmatter.confidence,
        pinned=payload.pinned if payload.pinned is not None else mem.frontmatter.pinned,
        tags=payload.tags if payload.tags is not None else mem.frontmatter.tags,
        supersedes=mem.frontmatter.supersedes,
        zone=payload.zone or mem.frontmatter.zone,
    )

    body = payload.body if payload.body is not None else mem.body

    # Write new file FIRST (preserves data on write failure)
    new_path = store._root_for(mem.scope) / f"{fm.created[:10]}-{fm.id[:16]}.md"
    srh._write_memory(new_path, fm, body)

    # Delete old file only after successful write
    store.delete(mem.scope, memory_id)

    # Update cache and re-index embedding
    store._id_to_path[fm.id] = new_path
    store._update_cache_for_put(mem.scope, fm, body, new_path)
    store._try_index(fm.id, body)

    return {
        "success": True,
        "id": memory_id,
        "message": "Memory updated",
    }


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a memory by ID."""
    store = srh._get_mem_store()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    success = store.delete(mem.scope, memory_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete memory")

    return {"success": True, "id": memory_id}


@router.post("/memories/reorder")
async def reorder_memories(payload: MemoryReorder):
    """Reorder memories by rewriting their created timestamps.

    The new order is determined by the provided memory_ids list.
    Each memory gets a new created timestamp based on its position,
    preserving the relative ordering. Uses timedelta to avoid microsecond overflow.
    """
    from datetime import datetime, timezone, timedelta

    store = srh._get_mem_store()
    now = datetime.now(timezone.utc)

    updated = []
    for i, mem_id in enumerate(payload.memory_ids):
        mem = store.get(mem_id)
        if not mem:
            continue

        # Generate a new timestamp using timedelta (safe for any list size)
        new_created = (now + timedelta(milliseconds=i)).isoformat()

        fm = srh.MemoryFrontmatter(
            id=mem_id,
            created=new_created,
            source=mem.frontmatter.source,
            confidence=mem.frontmatter.confidence,
            pinned=mem.frontmatter.pinned,
            tags=mem.frontmatter.tags,
            supersedes=mem.frontmatter.supersedes,
            zone=mem.frontmatter.zone,
        )

        # Write new file FIRST, then delete old (atomic swap pattern)
        new_path = store._root_for(mem.scope) / f"{fm.created[:10]}-{fm.id[:16]}.md"
        srh._write_memory(new_path, fm, mem.body)
        store.delete(mem.scope, mem_id)

        # Update cache and re-index embedding
        store._id_to_path[fm.id] = new_path
        store._update_cache_for_put(mem.scope, fm, mem.body, new_path)
        store._try_index(fm.id, mem.body)
        updated.append(mem_id)

    # Invalidate cache to ensure fresh ordering
    store._invalidate_cache()

    return {"success": True, "updated": updated, "count": len(updated)}


@router.get("/zones")
async def get_zones():
    """Return all available zones and their counts."""
    result = json.loads(srh._tool_srh_palace_zones({}))
    return result
