"""Runtime tool handlers for mem-reflection-hermes."""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core.store import (
    LoadedMemory, MemoryFrontmatter, SkillFrontmatter, LoadedSkill,
    hermes_home as _hermes_home, plugin_data_dir as _plugin_data_dir,
    serialize_frontmatter, read_memory, record_memory_stat,
    _lineage_latest, _lineage_root, _lineage_depth, _lineage_cycle_check,
    _is_expired, _is_context_mismatch, _classify_update_intent,
)
from ..core.search import _extract_keywords
from ..core.scope import normalize_scope_filters, scope_from_values
from ..reflection.runtime import (
    _append_reflect_log, _recent_reflect_outcomes,
    _run_full_reflection, _run_micro_reflection,
    _run_embedding_reflection, _run_embedding_micro_reflection,
)
from ._lb import _lb

logger = logging.getLogger(__name__)

# P2-33: safe JSON serialization.
# default=str converts non-serializable types (datetime, Path, etc.) to their
# string representation. This is intentional for tool output formatting.
def _jd(obj, **kw) -> str:
    """json.dumps wrapper with default=str. Default ensure_ascii=False."""
    if "ensure_ascii" not in kw:
        kw["ensure_ascii"] = False
    return json.dumps(obj, default=str, **kw)

__all__ = [
    "register",
    "_auto_rebalance_zones",
    "_build_compile_palace_prompt",
    "_build_compile_profile_prompt",
    "_build_compile_zone_prompt",
    "_compile_profile_via_llm",
    "_tool_srh_compile_profile",
    "_tool_srh_memory_delete",
    "_tool_srh_memory_history",
    "_tool_srh_memory_search",
    "_tool_srh_memory_write",
    "_tool_srh_palace_read_zone",
    "_tool_srh_palace_rebalance",
    "_tool_srh_palace_recall",
    "_tool_srh_palace_search",
    "_tool_srh_palace_zones",
    "_tool_srh_reflect_now",
    "_tool_srh_skill_search",
]


def _get_mem_store():
    return _lb("_get_mem_store")()

def _get_skill_store():
    return _lb("_get_skill_store")()

def _build_context_block(query=""):
    return _lb("_build_context_block")(query)

def _auto_rebalance_zones():
    return _lb("_auto_rebalance_zones")()

def _normalize_zone(z):
    return _lb("_normalize_zone")(z)

def _estimate_tokens(text):
    return _lb("_estimate_tokens")(text)

def _palace_mode_enabled():
    return _lb("_palace_mode_enabled")()

def _profile_mode_enabled():
    return _lb("_profile_mode_enabled")()

def _get_graph_mgr():
    return _lb("_get_graph_mgr")()

def _get_graph_neighbors(*a, **kw):
    return _lb("_get_graph_neighbors")(*a, **kw)

def _enrich_with_graph(*a, **kw):
    return _lb("_enrich_with_graph")(*a, **kw)

def load_zone_summary(zone):
    return _lb("load_zone_summary")(zone)

def save_zone_summary(zone, content):
    return _lb("save_zone_summary")(zone, content)

def _palace_index_path():
    return _lb("_palace_index_path")()

def _sanitize_zone_filename(zone):
    return _lb("_sanitize_zone_filename")(zone)

def _serialize_frontmatter(data, body):
    return serialize_frontmatter(data, body)

def _read_memory(path):
    return read_memory(path)


# Tool handlers
# ---------------------------------------------------------------------------

def _tool_srh_memory_search(args: dict, **kwargs) -> str:
    query = args.get("query", "")
    k = int(args.get("k", 5))
    zone_filter = args.get("zone")  # Optional zone scope
    include_history = bool(args.get("include_history", False))
    explain = bool(args.get("explain", False))
    try:
        filters = normalize_scope_filters(args.get("filters") or None)
    except ValueError as e:
        return _jd({"error": str(e)})
    mem_store = _get_mem_store()
    # ── Scheme C: Fusion search (BM25 × Graph × Supersedes) instead of two-stage ──
    normalized_zone = _normalize_zone(zone_filter) if zone_filter else None
    explain_payload = None
    if explain:
        explain_payload = mem_store.fusion_search_explain(
            query,
            k,
            zone=normalized_zone,
            include_history=include_history,
            filters=filters,
        )
        results = explain_payload.get("results", [])
    else:
        results = mem_store.fusion_search(
            query,
            k,
            zone=normalized_zone,
            include_history=include_history,
            filters=filters,
        )
    out = []
    for m in results:
        is_superseded = mem_store.is_superseded(m.id())
        item = {
            "id": m.id(),
            "scope": m.scope,
            "confidence": m.frontmatter.confidence,
            "pinned": m.frontmatter.pinned,
            "tags": m.frontmatter.tags,
            "zone": m.frontmatter.zone,
            "body": m.body[:500],
            "lineage_status": "superseded" if is_superseded else "active",
        }
        for field in ("user_id", "agent_id", "run_id"):
            val = getattr(m.frontmatter, field, None)
            if val is not None:
                item[field] = val
        if explain and explain_payload is not None:
            item["explain"] = explain_payload.get("explain", {}).get(m.id(), {})
        out.append(item)
        record_memory_stat(m.id(), "accessed")

    # ── Enrich with graph neighbors (now as supplement, not primary ranking) ──
    # v1.6: graph expansion is scope-agnostic — when scope filters are active,
    # skip graph expansion to avoid leaking cross-scope memories as hints.
    graph_expanded: List[Tuple[str, float]] = []
    if not filters:
        graph_expanded = _get_graph_neighbors([m.id() for m in results], max_results=k,
                                              zone_filter=normalized_zone)
    for neigh_id, _ in graph_expanded:
        record_memory_stat(neigh_id, "accessed")
    response = {
        "results": out,
        "graph_expanded": [{"id": mid, "weight": round(w, 3)} for mid, w in graph_expanded],
    }
    if explain and explain_payload is not None:
        response["meta"] = explain_payload.get("meta", {})
    return json.dumps(response, ensure_ascii=False)


def _tool_srh_memory_write(args: dict, **kwargs) -> str:
    mem_store = _get_mem_store()
    body = args.get("body", "").strip()
    if not body:
        return _jd({"error": "body is required"})
    scope = args.get("scope", "user")
    confidence = args.get("confidence", "medium")
    tags = args.get("tags", [])
    pinned = bool(args.get("pinned", False))
    supersedes = args.get("supersedes", [])
    supersedes_reason = args.get("supersedes_reason")
    zone = _normalize_zone(args.get("zone"))

    # Validate supersedes targets exist and no cycles
    if supersedes:
        for sid in supersedes:
            target = mem_store.get(sid)
            if target is None:
                return _jd({
                    "error": f"supersedes target not found: {sid}",
                    "missing_id": sid,
                })
        # Cycle guard — check every target (beta3: was only checking supersedes[0])
        for sid in supersedes:
            cycle = _lineage_cycle_check(mem_store, sid)
            if cycle:
                return _jd({
                    "error": f"supersedes would create a cycle: {' -> '.join(cycle)}",
                    "cycle": cycle,
                })

    # Extract scope fields before conflict check so scoped writes only
    # conflict-detect within their own scope (v1.6).
    user_id = scope_from_values(user_id=args.get("user_id")).get("user_id")
    agent_id = scope_from_values(agent_id=args.get("agent_id")).get("agent_id")
    run_id = scope_from_values(run_id=args.get("run_id")).get("run_id")

    # Cross-scope supersedes guard — prevent scoped writes from superseding
    # memories owned by a different scope (v1.6).
    if supersedes and (user_id or agent_id or run_id):
        for sid in supersedes:
            target = mem_store.get(sid)
            if target is None:
                continue
            tfm = target.frontmatter
            for key, val in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)):
                if val is not None and getattr(tfm, key, None) is not None and getattr(tfm, key, None) != val:
                    return _jd({
                        "error": f"supersedes target {sid} belongs to a different scope ({key}: {getattr(tfm, key, None)} != {val})",
                        "conflict_id": sid,
                        "scope_field": key,
                    })
    scope_filters = scope_from_values(user_id=user_id, agent_id=agent_id, run_id=run_id)

    # Conflict check — skip targets being superseded to avoid rejecting
    # intentional replacements (P1).  Pass scope_filters so scoped writes
    # only compare against memories in the same scope.
    conflict = mem_store.check_conflict(body, exclude_ids=supersedes, filters=scope_filters)
    if conflict:
        existing_id, score = conflict
        existing = mem_store.get(existing_id)
        guidance = (
            "Conflict detected with an existing memory. "
            "Recommended actions: "
            "1) pass supersedes=[id] to replace, "
            "2) change zone/scope for a parallel memory, "
            "3) keep as episode/history if this is a temporal event."
        )
        return _jd({
            "error": guidance,
            "conflict_with": existing_id,
            "similarity": score,
            "existing_zone": existing.frontmatter.zone if existing else None,
        })

    fm = MemoryFrontmatter.new(
        source="user", confidence=confidence, tags=tags, zone=zone,
        pinned=pinned, supersedes=supersedes, supersedes_reason=supersedes_reason,
        user_id=user_id, agent_id=agent_id, run_id=run_id,
    )
    path = mem_store.put(scope, fm, body)

    # ── Dir B: Mirror qualifying plugin writes to built-in MEMORY.md ────
    try:
        from ..memory.bridge import bridge_enabled as _b_enabled
        if _b_enabled():
            from ..memory.bridge import mirror_plugin_to_builtin as _mirror_b
            _mirror_b(
                body=body,
                zone=zone,
                source="srh_memory_write",
                supersedes=fm.supersedes or None,
            )
    except Exception as _dir_b_err:
        logger.debug("Bridge Dir B failed: %s", _dir_b_err)

    return _jd({
        "success": True,
        "id": fm.id,
        "path": str(path),
    })


def _tool_srh_memory_delete(args: dict, **kwargs) -> str:
    mem_store = _get_mem_store()
    mem_id = args.get("id", "")
    scope = args.get("scope", "user")
    try:
        filters = normalize_scope_filters(args.get("filters") or None)
    except ValueError as e:
        return _jd({"error": str(e)})
    if not mem_id and not filters:
        return _jd({"error": "id or filters is required"})
    if filters:
        try:
            count = mem_store.delete_by_filters(filters)
            return _jd({"success": True, "deleted_count": count})
        except ValueError as e:
            return _jd({"error": str(e)})

    ok = mem_store.delete(scope, mem_id)
    return _jd({"success": ok, "id": mem_id})


# ── P2-4: Memory version history (supersedes chain) ───────

def _tool_srh_memory_history(args: dict, **kwargs) -> str:
    """Trace supersedes chain for a memory, returning full version lineage."""
    memory_id = args.get("id", "")
    if not memory_id:
        return _jd({"error": "id is required"})
    max_depth = min(int(args.get("max_depth", 5)), 20)
    include_events = bool(args.get("include_events", False))

    mem_store = _get_mem_store()
    # Cycle guard
    cycle = _lineage_cycle_check(mem_store, memory_id)
    if cycle:
        return _jd({
            "memory_id": memory_id,
            "error": f"Cycle detected in supersedes chain: {' -> '.join(cycle)}",
            "cycle": cycle,
        })

    # Walk backward to root, then forward to current
    full_chain = mem_store.lineage_chain(memory_id, max_depth=max_depth)
    chain = []
    root_id = _lineage_root(mem_store, memory_id)
    latest_id = _lineage_latest(mem_store, root_id)
    if latest_id is None:
        latest_id = memory_id  # beta3: fallback to queried id instead of root_id

    for idx, m in enumerate(full_chain):
        is_current = m.id() == latest_id
        is_superseded = mem_store.is_superseded(m.id())
        chain.append({
            "depth": idx,
            "id": m.id(),
            "zone": m.frontmatter.zone,
            "created": m.frontmatter.created,
            "confidence": m.frontmatter.confidence,
            "pinned": m.frontmatter.pinned,
            "tags": m.frontmatter.tags,
            "supersedes": m.frontmatter.supersedes,
            "supersedes_reason": m.frontmatter.supersedes_reason,
            "body": m.body[:200],
            "status": "current" if is_current else ("superseded" if is_superseded else "root"),
        })

    payload = {
        "memory_id": memory_id,
        "chain_length": len(chain),
        "chain_depth": len(chain) - 1,
        "current_id": latest_id,
        "chain": chain,
    }
    if include_events:
        event_types = args.get("event_types")
        session_id = args.get("session_id")
        if event_types or session_id:
            events = mem_store.get_memory_events(
                memory_id,
                event_types=event_types,
                session_id=session_id,
            )
        else:
            history = mem_store.get_memory_history(memory_id, include_events=True)
            events = history.get("events", [])
        events_out = []
        for e in events:
            e_out = dict(e)
            for key in ("old_frontmatter", "new_frontmatter"):
                raw = e_out.get(key)
                if raw:
                    try:
                        e_out[key] = json.loads(raw)
                    except json.JSONDecodeError:
                        pass
            events_out.append(e_out)
        payload["events"] = events_out
    return json.dumps(payload, ensure_ascii=False)


def _tool_srh_skill_search(args: dict, **kwargs) -> str:
    query = args.get("query", "")
    k = int(args.get("k", 3))
    skill_store = _get_skill_store()
    from .. import match_skills
    skills = match_skills(skill_store.list(), query, k)
    out = []
    for s in skills:
        out.append({
            "name": s.frontmatter.name,
            "description": s.frontmatter.description,
            "triggers": s.frontmatter.triggers,
            "scope": s.scope,
        })
    return json.dumps({"results": out}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Palace tools (zone-based memory navigation)
# ---------------------------------------------------------------------------

def _tool_srh_palace_zones(args: dict, **kwargs) -> str:
    """List all Memory Palace zones with memory counts."""
    mem_store = _get_mem_store()
    groups = mem_store.group_by_zone()
    if not groups:
        return _jd({"zones": [], "total": 0, "message": "Memory Palace is empty — no memories yet."})
    zones = []
    total = 0
    for zone, mems in sorted(groups.items()):
        zones.append({"zone": zone, "count": len(mems)})
        total += len(mems)
    return json.dumps({"zones": zones, "total": total}, ensure_ascii=False)


def _tool_srh_palace_read_zone(args: dict, **kwargs) -> str:
    """Load all memories from a specific zone. Returns cached summary if available."""
    zone = _normalize_zone(args.get("zone"))
    mem_store = _get_mem_store()

    # Try cached summary first
    cached = load_zone_summary(zone)
    if cached:
        return json.dumps({
            "zone": zone,
            "source": "cache",
            "content": cached,
        }, ensure_ascii=False)

    # Load raw memories from the zone
    zone_mems = mem_store.list_by_zone(zone)
    if not zone_mems:
        return json.dumps({
            "zone": zone,
            "source": "live",
            "memories": [],
            "message": f"Zone '{zone}' is empty or does not exist.",
        }, ensure_ascii=False)

    # Record access stats
    for m in zone_mems:
        record_memory_stat(m.id(), "accessed")

    memories = []
    for m in zone_mems:
        memories.append({
            "id": m.id(),
            "confidence": m.frontmatter.confidence,
            "pinned": m.frontmatter.pinned,
            "tags": m.frontmatter.tags,
            "body": m.body[:500],
        })
    return json.dumps({
        "zone": zone,
        "source": "live",
        "count": len(memories),
        "memories": memories,
    }, ensure_ascii=False)


# ── P2-2: Zone auto-rebalance ────────────────────────────

_ZONE_SPLIT_THRESHOLD = 20   # If zone has > this many memories, split
_ZONE_MERGE_THRESHOLD = 3    # If zone has < this many memories, merge into general

def _auto_rebalance_zones(dry_run: bool = False) -> dict:
    """Auto-rebalance zones: split large zones, merge small ones into general.

    Returns dict with actions taken (or would take if dry_run=True).
    """
    mem_store = _get_mem_store()
    all_mems = mem_store.list_active()
    if not all_mems:
        return {"message": "No memories to rebalance", "actions": []}

    groups = {}
    for m in all_mems:
        groups.setdefault(m.frontmatter.zone, []).append(m)

    actions = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for zone, mems in groups.items():
        # Skip custom project zones and general
        if zone.startswith("project:") or zone == "general":
            continue

        # Split large zones
        if len(mems) > _ZONE_SPLIT_THRESHOLD:
            # Simple split: move excess to project:<zone>-overflow
            overflow_zone = f"project:{zone}-overflow"
            overflow_mems = mems[_ZONE_SPLIT_THRESHOLD:]
            if not dry_run:
                for m in overflow_mems:
                    # Use MemoryStore.update for atomic write (beta3: was bypassing store)
                    mem_store.update(m.id(), zone=overflow_zone)
            actions.append({
                "action": "split",
                "source_zone": zone,
                "target_zone": overflow_zone,
                "move_count": len(overflow_mems),
                "dry_run": dry_run,
            })

        # Merge tiny zones into general
        elif len(mems) < _ZONE_MERGE_THRESHOLD and len(mems) > 0:
            if not dry_run:
                for m in mems:
                    # Use MemoryStore.update for atomic write (beta3: was bypassing store)
                    mem_store.update(m.id(), zone="general")
            actions.append({
                "action": "merge",
                "source_zone": zone,
                "target_zone": "general",
                "move_count": len(mems),
                "dry_run": dry_run,
            })

    return {
        "actions": actions,
        "total_actions": len(actions),
        "dry_run": dry_run,
        "timestamp": now,
    }


def _tool_srh_palace_rebalance(args: dict, **kwargs) -> str:
    """Manually trigger zone rebalance."""
    dry_run = bool(args.get("dry_run", True))  # Safe default: dry run
    result = _auto_rebalance_zones(dry_run=dry_run)
    return json.dumps(result, ensure_ascii=False)


def _tool_srh_palace_recall(args: dict, **kwargs) -> str:
    """Search memories by topic, optionally scoped to a zone."""
    query = args.get("topic", "")
    if not query:
        return _jd({"error": "topic is required"})
    k = int(args.get("limit", 5))
    zone = _normalize_zone(args.get("zone")) if args.get("zone") else None
    try:
        filters = normalize_scope_filters(args.get("filters") or None)
    except ValueError as e:
        return _jd({"error": str(e)})

    mem_store = _get_mem_store()
    if zone:
        results = mem_store.search(query, k=k, zone=zone, filters=filters)
    else:
        results = mem_store.search(query, k=k * 3, filters=filters)[:k]

    if not results:
        scope_msg = f" in zone '{zone}'" if zone else ""
        return json.dumps({
            "results": [],
            "message": f"No memories matching '{query}'{scope_msg}",
        }, ensure_ascii=False)

    # Record access stats
    for m in results:
        record_memory_stat(m.id(), "accessed")

    out = []
    for i, m in enumerate(results):
        item = {
            "rank": i + 1,
            "id": m.id(),
            "zone": m.frontmatter.zone,
            "confidence": m.frontmatter.confidence,
            "tags": m.frontmatter.tags,
            "body": m.body[:500],
        }
        for field in ("user_id", "agent_id", "run_id"):
            val = getattr(m.frontmatter, field, None)
            if val is not None:
                item[field] = val
        out.append(item)
    # ── Graph-enhanced expansion ─────────────────────────
    # Enrich palace recall results with graph-neighbor memories.
    result_mids = [m.id() for m in results]
    return json.dumps(
        {"results": out} if filters else _enrich_with_graph(result_mids, out, k, zone_filter=zone),
        ensure_ascii=False,
    )


# ── P2-1: Cross-zone aggregate search ─────────────────────────

def _tool_srh_palace_search(args: dict, **kwargs) -> str:
    """Search across all zones, returning results grouped by zone with counts.
    
    Unlike srh_palace_recall (zone-scoped) and srh_memory_search (flat),
    this tool returns zone-grouped results so the agent can understand
    which zones contain relevant memories.
    """
    query = args.get("query", "")
    if not query:
        return _jd({"error": "query is required"})
    k = int(args.get("limit", 10))
    try:
        filters = normalize_scope_filters(args.get("filters") or None)
    except ValueError as e:
        return _jd({"error": str(e)})

    mem_store = _get_mem_store()
    results = mem_store.fusion_search(query, k=k * 2, filters=filters)

    if not results:
        return json.dumps({
            "results": {},
            "total": 0,
            "message": f"No memories matching '{query}'",
        }, ensure_ascii=False)

    # Group by zone
    grouped = {}
    for m in results:
        zone = m.frontmatter.zone
        item = {
            "id": m.id(),
            "confidence": m.frontmatter.confidence,
            "tags": m.frontmatter.tags,
            "body": m.body[:500],
        }
        for field in ("user_id", "agent_id", "run_id"):
            val = getattr(m.frontmatter, field, None)
            if val is not None:
                item[field] = val
        grouped.setdefault(zone, []).append(item)
        record_memory_stat(m.id(), "accessed")

    # Sort zones by result count (descending)
    sorted_zones = sorted(grouped.keys(), key=lambda z: len(grouped[z]), reverse=True)

    total = sum(len(items) for items in grouped.values())
    return json.dumps({
        "results": {zone: grouped[zone] for zone in sorted_zones},
        "zone_counts": {zone: len(grouped[zone]) for zone in sorted_zones},
        "total": total,
        "zones_found": len(grouped),
    }, ensure_ascii=False)


def _tool_srh_reflect_now(args: dict, **kwargs) -> str:
    """Trigger a full reflection on the current session messages."""
    ctx = args.get("ctx")
    messages = args.get("messages", [])
    try:
        filters = normalize_scope_filters(args.get("filters") or None)
    except ValueError as e:
        return _jd({"error": str(e)})
    if not ctx:
        return _jd({
            "error": "Reflection requires ctx with LLM access. Run via /reflect slash command or wait for session-end auto-reflection.",
            "recent_outcomes": _recent_reflect_outcomes(5),
        })
    if not messages:
        return _jd({"error": "No messages to reflect on"})
    try:
        result = _run_full_reflection(ctx, messages, scope_filters=filters)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return _jd({"error": str(e)})


# ---------------------------------------------------------------------------
# Profile Compilation (LLM-compiled memory summary, mirrors small-rust-hermes compile.rs)
# ---------------------------------------------------------------------------

_COMPILE_PROFILE_SYSTEM = """You are a memory curator. Given a list of individual memory entries about a user accumulated over multiple conversations, compile them into a structured profile document.

Rules:
- Use ## markdown headers to organize by topic (categories emerge naturally from the content)
- Merge overlapping or redundant memories into single concise entries
- Use bullet points, one line per point
- Preserve the user's language (Chinese / English as found in entries)
- Drop entries that are trivially obvious or redundant after merging
- Output ONLY the profile markdown, no preamble or explanation"""

_COMPILE_PALACE_INDEX_SYSTEM = """You are a memory curator organizing a Memory Palace index. Given memories grouped by zone, produce a concise zone map.

Rules:
- Use ## Memory Palace as the top header
- Show total memory count and zone count in the first line
- For each zone, use ### zone_name (count) as header
- Under each zone, list 2-3 bullet points summarizing key content
- Keep the entire output under 300 tokens
- Preserve the user's language (Chinese / English as found in entries)
- Output ONLY the index markdown, no preamble"""

_COMPILE_ZONE_SYSTEM = """You are a memory curator. Given all memories from a single zone, compile them into a concise summary.

Rules:
- Use bullet points, one line per point
- Merge overlapping or redundant memories
- Preserve the user's language (Chinese / English as found in entries)
- Keep the output under 400 tokens
- Output ONLY the summary markdown, no preamble"""


def _compile_profile_via_llm(ctx, mode: str = "profile", filters: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, Any]:
    """Compile active memories into a structured markdown document via LLM.

    Args:
        ctx: Hermes agent context with ctx.llm access
        mode: "profile" (profile.md), "palace_index" (palace-index.md), or "zone" (zone-cache/*)

    Returns:
        Dict with 'success', 'path', 'mode', 'token_count' or 'error'
    """
    # P2-32: quick return when profile mode is disabled
    if not _profile_mode_enabled():
        return {"error": "Profile mode is disabled (enable via config.yaml memory.palace_mode or memory.profile_mode)"}

    if not hasattr(ctx, "llm"):
        return {"error": "No LLM available for compilation"}

    mem_store = _get_mem_store()
    filters = normalize_scope_filters(filters)
    try:
        active = mem_store.list_active(filters=filters)
    except TypeError:
        active = mem_store.list_active()
    if not active:
        return {"error": "No active memories to compile"}

    try:
        if mode == "profile":
            system = _COMPILE_PROFILE_SYSTEM
            prompt = _build_compile_profile_prompt(active)
            save_path = _plugin_data_dir() / "profile.md"
        elif mode == "palace_index":
            system = _COMPILE_PALACE_INDEX_SYSTEM
            prompt = _build_compile_palace_prompt(active)
            save_path = _palace_index_path()
        elif mode == "zone":
            # Compile all zones
            results = {}
            try:
                groups = mem_store.group_by_zone(filters=filters)
            except TypeError:
                groups = mem_store.group_by_zone()
            for zone, mems in groups.items():
                prompt = _build_compile_zone_prompt(zone, mems)
                result = ctx.llm.complete_structured(
                    instructions=prompt,
                    input=[{"type": "text", "text": prompt}],
                    system_prompt=_COMPILE_ZONE_SYSTEM,
                    purpose=f"compile_zone_{_sanitize_zone_filename(zone)}",
                    max_tokens=1024,
                )
                if result and result.content_type == "json" and result.parsed:
                    text = result.text.strip() if hasattr(result, 'text') else str(result)
                    save_zone_summary(zone, text)
                    results[zone] = {"tokens": len(text.split())}
                else:
                    err_msg = f"LLM returned {result.content_type}" if result else "no result"
                    results[zone] = {"error": err_msg}
            return {"success": True, "mode": "zone", "zones": results}
        else:
            return {"error": f"Unknown compilation mode: {mode}"}

        result = ctx.llm.complete_structured(
            instructions=prompt,
            input=[{"type": "text", "text": prompt}],
            system_prompt=system,
            purpose=f"compile_{mode}",
            max_tokens=4096,
        )

        if not result or result.content_type != "json" or not result.parsed:
            return {"error": f"LLM compilation failed: {result.content_type if result else 'no result'}"}

        text = result.text.strip() if hasattr(result, 'text') else str(result)
        if not text:
            return {"error": "LLM returned empty response"}

        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(text, encoding="utf-8")

        return {
            "success": True,
            "mode": mode,
            "path": str(save_path),
            "token_count": len(text.split()),
        }
    except Exception as e:
        logger.warning("Profile compilation failed: %s", e)
        return {"error": str(e)}


def _build_compile_profile_prompt(memories: List[LoadedMemory]) -> str:
    """Build user prompt for profile compilation."""
    buf = "Compile the following memory entries into a structured profile:\n\n"
    for m in memories:
        pin = "pinned, " if m.frontmatter.pinned else ""
        conf = m.frontmatter.confidence
        buf += f"- [{m.id()}] ({pin}{conf}, zone={m.frontmatter.zone}) {m.body.strip()}\n"
    return buf


def _build_compile_palace_prompt(memories: List[LoadedMemory]) -> str:
    """Build user prompt for palace index compilation."""
    groups: Dict[str, List[LoadedMemory]] = {}
    for m in memories:
        groups.setdefault(m.frontmatter.zone, []).append(m)
    buf = "Organize these memories into a palace index:\n\n"
    for zone, mems in sorted(groups.items()):
        buf += f"### {zone} ({len(mems)} memories)\n"
        for m in mems:
            buf += f"- {m.body.strip()}\n"
        buf += "\n"
    return buf


def _build_compile_zone_prompt(zone: str, memories: List[LoadedMemory]) -> str:
    """Build user prompt for zone summary compilation."""
    buf = f"Summarize zone '{zone}' ({len(memories)} memories):\n\n"
    for m in memories:
        buf += f"- ({m.frontmatter.confidence}) {m.body.strip()}\n"
    return buf


def _tool_srh_compile_profile(args: dict, **kwargs) -> str:
    """Compile memories into a structured profile via LLM."""
    ctx = args.get("ctx")
    if not ctx:
        return _jd({
            "error": "Compilation requires ctx with LLM access. Use /compile-profile slash command.",
        })
    mode = args.get("mode", "profile")
    try:
        filters = normalize_scope_filters(args.get("filters") or None)
    except ValueError as e:
        return _jd({"error": str(e)})
    result = _compile_profile_via_llm(ctx, mode, filters=filters)
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Backward-compatible registration entrypoint
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the mem-reflection-hermes plugin.

    Deprecated: this function exists only for backward compatibility with
    callers importing from runtime.tools. The canonical registration logic
    lives in runtime.registration and uses the schemas in runtime.schemas.
    """
    from .registration import register as _canonical_register
    return _canonical_register(ctx)


# Hooks imported from the public runtime facade for backward compat.
from .hooks import _on_session_start, _on_session_end, _pre_llm_call, _post_tool_call  # noqa: F401


register_tools = register
__all__ = list(__all__) + ["register_tools"]



# Public aliases for runtime/__init__.py exports
# Map public names to internal implementation
srh_memory_write = _tool_srh_memory_write
srh_memory_search = _tool_srh_memory_search
srh_memory_delete = _tool_srh_memory_delete
srh_memory_history = _tool_srh_memory_history
srh_palace_navigate = _tool_srh_palace_recall  # palace_navigate maps to palace_recall
srh_reflect_now = _tool_srh_reflect_now
srh_skill_query = _tool_srh_skill_search  # skill_query maps to skill_search
srh_compile_profile = _tool_srh_compile_profile

