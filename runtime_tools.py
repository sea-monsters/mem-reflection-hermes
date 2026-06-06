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

from .store import (
    LoadedMemory, MemoryFrontmatter, SkillFrontmatter, LoadedSkill,
    hermes_home as _hermes_home, plugin_data_dir as _plugin_data_dir,
    serialize_frontmatter, read_memory, record_memory_stat,
    _lineage_latest, _lineage_root, _lineage_depth, _lineage_cycle_check,
    _is_expired, _is_context_mismatch, _classify_update_intent,
)
try:
    from .search import _extract_keywords
except ImportError:
    import importlib.util as _i_util
    from pathlib import Path as _Path
    _search_path = _Path(__file__).resolve().parent.parent / "search.py"
    _spec = _i_util.spec_from_file_location(
        "mem_reflection_hermes.search", str(_search_path))
    _search_mod = _i_util.module_from_spec(_spec)
    import sys as _sys
    _sys.modules["mem_reflection_hermes.search"] = _search_mod
    _spec.loader.exec_module(_search_mod)
    _extract_keywords = _search_mod._extract_keywords

from .reflect import (
    _append_reflect_log, _recent_reflect_outcomes,
    _run_full_reflection, _run_micro_reflection,
    _run_embedding_reflection, _run_embedding_micro_reflection,
)

logger = logging.getLogger(__name__)

# P2-33: safe JSON serialization.
# default=str converts non-serializable types (datetime, Path, etc.) to their
# string representation. This is intentional for tool output formatting.
def _jd(obj, **kw) -> str:
    """json.dumps wrapper with default=str. Default ensure_ascii=False."""
    if "ensure_ascii" not in kw:
        kw["ensure_ascii"] = False
    return json.dumps(obj, default=str, **kw)

def _lb(name: str):
    from mem_reflection_hermes import __dict__ as _pkg_dict
    return _pkg_dict[name]

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

def build_palace_index(*a, **kw):
    return _lb("build_palace_index")(*a, **kw)

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

from . import match_skills  # noqa: E402

# Tool handlers
# ---------------------------------------------------------------------------

def _tool_srh_memory_search(args: dict, **kwargs) -> str:
    query = args.get("query", "")
    k = int(args.get("k", 5))
    zone_filter = args.get("zone")  # Optional zone scope
    include_history = bool(args.get("include_history", False))
    mem_store = _get_mem_store()
    # ── Scheme C: Fusion search (BM25 × Graph × Supersedes) instead of two-stage ──
    results = mem_store.fusion_search(query, k, zone=_normalize_zone(zone_filter) if zone_filter else None,
                                       include_history=include_history)
    out = []
    for m in results:
        is_superseded = mem_store.is_superseded(m.id())
        out.append({
            "id": m.id(),
            "scope": m.scope,
            "confidence": m.frontmatter.confidence,
            "pinned": m.frontmatter.pinned,
            "tags": m.frontmatter.tags,
            "zone": m.frontmatter.zone,
            "body": m.body[:500],
            "lineage_status": "superseded" if is_superseded else "active",
        })
        record_memory_stat(m.id(), "accessed")

    # ── Enrich with graph neighbors (now as supplement, not primary ranking) ──
    graph_expanded = _get_graph_neighbors([m.id() for m in results], max_results=k,
                                          zone_filter=_normalize_zone(zone_filter) if zone_filter else None)
    for neigh_id, _ in graph_expanded:
        record_memory_stat(neigh_id, "accessed")
    return json.dumps({
        "results": out,
        "graph_expanded": [{"id": mid, "weight": round(w, 3)} for mid, w in graph_expanded],
    }, ensure_ascii=False)


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
            if mem_store.get(sid) is None:
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

    # Conflict check — skip targets being superseded to avoid rejecting
    # intentional replacements (P1)
    conflict = mem_store.check_conflict(body, exclude_ids=supersedes)
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

    fm = MemoryFrontmatter.new(source="user", confidence=confidence, tags=tags, zone=zone)
    fm.pinned = pinned
    fm.supersedes = supersedes
    fm.supersedes_reason = supersedes_reason
    path = mem_store.put(scope, fm, body)

    # ── Dir B: Mirror qualifying plugin writes to built-in MEMORY.md ────
    try:
        from .memory_bridge import bridge_enabled as _b_enabled
        if _b_enabled():
            from .memory_bridge import mirror_plugin_to_builtin as _mirror_b
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
    if not mem_id:
        return _jd({"error": "id is required"})

    ok = mem_store.delete(scope, mem_id)
    return _jd({"success": ok, "id": mem_id})


# ── P2-4: Memory version history (supersedes chain) ───────

def _tool_srh_memory_history(args: dict, **kwargs) -> str:
    """Trace supersedes chain for a memory, returning full version lineage."""
    memory_id = args.get("id", "")
    if not memory_id:
        return _jd({"error": "id is required"})
    max_depth = min(int(args.get("max_depth", 5)), 20)

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

    return json.dumps({
        "memory_id": memory_id,
        "chain_length": len(chain),
        "chain_depth": len(chain) - 1,
        "current_id": latest_id,
        "chain": chain,
    }, ensure_ascii=False)


def _tool_srh_skill_search(args: dict, **kwargs) -> str:
    query = args.get("query", "")
    k = int(args.get("k", 3))
    skill_store = _get_skill_store()
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

    mem_store = _get_mem_store()
    results = mem_store.search(query, k=k * 3)  # Over-fetch for zone filtering

    # Apply zone filter if specified
    if zone:
        results = [m for m in results if m.frontmatter.zone == zone][:k]
    else:
        results = results[:k]

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
        out.append({
            "rank": i + 1,
            "id": m.id(),
            "zone": m.frontmatter.zone,
            "confidence": m.frontmatter.confidence,
            "tags": m.frontmatter.tags,
            "body": m.body[:500],
        })
    # ── Graph-enhanced expansion ─────────────────────────
    # Enrich palace recall results with graph-neighbor memories.
    result_mids = [m.id() for m in results]
    return json.dumps(
        _enrich_with_graph(result_mids, out, k, zone_filter=zone),
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

    mem_store = _get_mem_store()
    results = mem_store.fusion_search(query, k=k * 2)

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
        grouped.setdefault(zone, []).append({
            "id": m.id(),
            "confidence": m.frontmatter.confidence,
            "tags": m.frontmatter.tags,
            "body": m.body[:500],
        })
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
    if not ctx:
        return _jd({
            "error": "Reflection requires ctx with LLM access. Run via /reflect slash command or wait for session-end auto-reflection.",
            "recent_outcomes": _recent_reflect_outcomes(5),
        })
    if not messages:
        return _jd({"error": "No messages to reflect on"})
    try:
        result = _run_full_reflection(ctx, messages)
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


def _compile_profile_via_llm(ctx, mode: str = "profile") -> Dict[str, Any]:
    """Compile active memories into a structured markdown document via LLM.

    Args:
        ctx: Hermes agent context with ctx.llm access
        mode: "profile" (profile.md), "palace_index" (palace-index.md), or "zone" (zone-cache/*)

    Returns:
        Dict with 'success', 'path', 'mode', 'token_count' or 'error'
    """
    # P2-32: quick return when profile mode is disabled
    from .store import profile_mode_enabled as _profile_mode_enabled
    if not _profile_mode_enabled():
        return {"error": "Profile mode is disabled (enable via config.yaml memory.palace_mode or memory.profile_mode)"}

    if not hasattr(ctx, "llm"):
        return {"error": "No LLM available for compilation"}

    mem_store = _get_mem_store()
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
                if result and not result.error:
                    text = result.text.strip() if hasattr(result, 'text') else str(result)
                    save_zone_summary(zone, text)
                    results[zone] = {"tokens": len(text.split())}
                else:
                    results[zone] = {"error": str(result.error) if result and hasattr(result, 'error') else "unknown"}
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

        if not result or result.error:
            return {"error": f"LLM compilation failed: {getattr(result, 'error', 'unknown')}"}

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
    result = _compile_profile_via_llm(ctx, mode)
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the mem-reflection-hermes plugin."""
    # Register tools
    ctx.register_tool(
        name="srh_memory_search",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_memory_search",
            "description": "Search active memories by TF-IDF relevance (or embedding if available). Use 'zone' parameter to filter by zone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "k": {"type": "integer", "description": "Max results", "default": 5, "minimum": 1, "maximum": 100},
                    "zone": {"type": "string", "description": "Optional: filter to a specific zone (core/work/episode/general/project:xxx)"},
                    "include_history": {"type": "boolean", "description": "Include superseded memories in search results (lineage-aware recall)", "default": False},
                },
                "required": ["query"],
            },
        },
        handler=_tool_srh_memory_search,
        description="Search memories by relevance",
        emoji="🧠",
    )
    ctx.register_tool(
        name="srh_memory_write",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_memory_write",
            "description": "Write a new structured memory with YAML frontmatter. Checks for conflicts. Specify zone to organize memories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {"type": "string", "description": "Memory content (one short fact)"},
                    "scope": {"type": "string", "enum": ["user", "project"], "default": "user"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
                    "tags": {"type": "array", "items": {"type": "string"}, "default": [], "maxItems": 20},
                    "pinned": {"type": "boolean", "default": False},
                    "supersedes": {"type": "array", "items": {"type": "string"}, "default": [], "maxItems": 5},
                    "supersedes_reason": {"type": "string", "description": "Human-readable reason why this memory supersedes the referenced memory IDs", "default": ""},
                    "zone": {"type": "string", "description": "Memory zone: core (identity/preferences), work (current focus), episode (session summaries), general (default), or project:<name>"},
                },
                "required": ["body"],
            },
        },
        handler=_tool_srh_memory_write,
        description="Write a structured memory",
        emoji="📝",
    )
    ctx.register_tool(
        name="srh_memory_delete",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_memory_delete",
            "description": "Delete a memory by id from a scope.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Memory id"},
                    "scope": {"type": "string", "enum": ["user", "project"], "default": "user"},
                },
                "required": ["id"],
            },
        },
        handler=_tool_srh_memory_delete,
        description="Delete a memory",
        emoji="🗑️",
    )

    # ── P2-4: srh_memory_history — supersedes chain lineage ──
    ctx.register_tool(
        name="srh_memory_history",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_memory_history",
            "description": "Trace the supersedes chain for a memory, returning its full version lineage — from the current active memory back through all archived predecessors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Memory ID to trace history for"},
                    "max_depth": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20, "description": "Max chain depth to follow"},
                },
                "required": ["id"],
            },
        },
        handler=_tool_srh_memory_history,
        description="Trace supersedes chain history",
        emoji="📜",
    )

    ctx.register_tool(
        name="srh_skill_search",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_skill_search",
            "description": "Search skills by token overlap relevance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "default": 3, "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
            },
        },
        handler=_tool_srh_skill_search,
        description="Search skills by relevance",
        emoji="🔧",
    )
    ctx.register_tool(
        name="srh_reflect_now",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_reflect_now",
            "description": "Trigger or check status of reflection pipeline.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        handler=_tool_srh_reflect_now,
        description="Trigger reflection",
        emoji="🔍",
    )

    # Palace tools (zone-based memory navigation)
    ctx.register_tool(
        name="srh_palace_zones",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_palace_zones",
            "description": "List all Memory Palace zones with memory counts. Use this to discover what zones exist before reading details.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        handler=_tool_srh_palace_zones,
        description="List memory zones",
        emoji="🏰",
    )
    ctx.register_tool(
        name="srh_palace_read_zone",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_palace_read_zone",
            "description": "Load all memories from a specific zone. Returns cached zone summary if available, otherwise raw memory bodies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "zone": {"type": "string", "description": "Zone name (core, work, episode, general, or project:<name>)"},
                },
                "required": ["zone"],
            },
        },
        handler=_tool_srh_palace_read_zone,
        description="Read a memory zone",
        emoji="📂",
    )
    ctx.register_tool(
        name="srh_palace_recall",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_palace_recall",
            "description": "Search memories by topic, optionally scoped to a zone. More focused than srh_memory_search — use this for palace navigation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "What to recall (e.g. 'editor preference', 'error handling convention')"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5, "minimum": 1, "maximum": 50},
                    "zone": {"type": "string", "description": "Optional: restrict to a specific zone"},
                },
                "required": ["topic"],
            },
        },
        handler=_tool_srh_palace_recall,
        description="Recall by topic",
        emoji="🔎",
    )

    # ── P2-1: srh_palace_search — cross-zone aggregate search ──
    ctx.register_tool(
        name="srh_palace_search",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_palace_search",
            "description": "Search across all zones, returning results grouped by zone with counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results per zone", "default": 10, "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
        },
        handler=_tool_srh_palace_search,
        description="Cross-zone search grouped by zone",
        emoji="🔍",
    )

    # ── P2-2: srh_palace_rebalance — zone auto-rebalance ──
    ctx.register_tool(
        name="srh_palace_rebalance",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_palace_rebalance",
            "description": "Rebalance memory zones: split zones with >20 memories, merge zones with <3 into general. Default is dry_run (no changes). Pass dry_run=false to execute.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dry_run": {"type": "boolean", "description": "If true (default), only report what would be done without making changes.", "default": True},
                },
            },
        },
        handler=_tool_srh_palace_rebalance,
        description="Auto-rebalance memory zones",
        emoji="⚖️",
    )

    # Profile compilation tool (LLM-driven)
    ctx.register_tool(
        name="srh_compile_profile",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_compile_profile",
            "description": "Compile all active memories into a structured profile document via LLM. Modes: 'profile' (profile.md), 'palace_index' (palace index), 'zone' (per-zone summaries).",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["profile", "palace_index", "zone"], "default": "profile", "description": "Compilation mode"},
                },
            },
        },
        handler=_tool_srh_compile_profile,
        description="Compile memories into profile",
        emoji="📋",
    )

    # Hooks are registered separately by runtime_hooks.register_hooks(ctx)
    # to avoid duplicate registration with runtime_graph.register_graph_features().


# Hooks imported from the public runtime facade for backward compat.
from .runtime_hooks import _on_session_start, _on_session_end, _pre_llm_call, _post_tool_call  # noqa: F401


register_tools = register
__all__ = list(__all__) + ["register_tools"]
