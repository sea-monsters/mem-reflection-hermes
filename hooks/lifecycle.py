"""hooks/lifecycle.py — Plugin lifecycle hooks, graph utils, and slash commands.
Entry points: _on_session_start, _on_session_end, _pre_llm_call.
Plus: graph manager, slash commands for reflect/skills/memory management.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core import (
    LoadedMemory, MemoryFrontmatter,
    hermes_home as _hermes_home, plugin_data_dir as _plugin_data_dir,
)
from ..reflection.engine import (
    _run_full_reflection, _run_micro_reflection,
    _run_embedding_micro_reflection,
    _approve_skill, _reject_skill,
    _load_pending_skill_candidates,
    _format_pending_skills_for_display,
)

logger = logging.getLogger(__name__)


__all__ = [
    "_enrich_with_graph",
    "_get_graph_mgr",
    "_get_graph_neighbors",
    "_gm_singleton",
    "_on_session_end",
    "_on_session_start",
    "_pre_llm_call",
    "_slash_approve_skill",
    "_slash_compile_profile",
    "_slash_memories",
    "_slash_pending_skills",
    "_slash_reflect",
    "_slash_reject_skill",
    "_slash_skills",
    "_turns_since_reflect",
]

# Late-binding imports cached at module level to avoid repeated dict lookups
# (P2-23: was doing import on every call)
_late_bindings: Dict[str, Any] = {}
_plugin_ctx: Any = None
_session_messages: Dict[str, List[Dict[str, Any]]] = {}

def _set_plugin_context(ctx: Any) -> None:
    """Remember the host plugin context for hooks that run without ctx kwargs."""
    global _plugin_ctx
    _plugin_ctx = ctx

def _lb(name: str):
    """Get a late-bound function, caching the lookup.

    Always resolves from the root plugin module (mem_reflection_hermes)
    rather than the child package so that functions defined in __init__.py
    (e.g. _micro_reflection_enabled, _build_context_block) are found
    regardless of which sub-module calls _lb.
    """
    fn = _late_bindings.get(name)
    if fn is None:
        mod = sys.modules.get("mem_reflection_hermes")
        if mod is None:
            raise KeyError("Plugin module not loaded for late binding: mem_reflection_hermes")
        fn = getattr(mod, name, None)
        if fn is None:
            raise KeyError(f"Root plugin module has no attribute: {name}")
        _late_bindings[name] = fn
    return fn

def _get_mem_store():
    return _lb("_get_mem_store")()

def _get_skill_store():
    return _lb("_get_skill_store")()

def _build_context_block(query=""):
    return _lb("_build_context_block")(query)

def _estimate_tokens(text):
    return _lb("_estimate_tokens")(text)

def _auto_rebalance_zones():
    return _lb("_auto_rebalance_zones")()

def _save_pending_skill_candidates(candidates):
    return _lb("_save_pending_skill_candidates")(candidates)

def _recent_reflect_outcomes(n=10):
    return _lb("_recent_reflect_outcomes")(n)

def _micro_reflection_enabled():
    return _lb("_micro_reflection_enabled")()

_turns_since_reflect: int = 0

# ── Graph manager global singleton (set during plugin init) ─────────
_gm_getter_func = None
_gm_getter_path = None
_gm_singleton = None
_gm_singleton_lock = threading.Lock()


# === Hooks ===
def _on_session_start(**kwargs) -> None:
    global _turns_since_reflect
    _turns_since_reflect = 0
    logger.debug("mem-reflection-hermes: session started")



def _on_session_end(**kwargs) -> None:
    session_id = kwargs.get("session_id", "")
    messages = kwargs.get("messages") or _session_messages.pop(session_id, [])

    # ── Periodic graph decay ──────────────────────────────
    # Run Ebbinghaus decay on graph edges every session end so weights
    # naturally fade for connections that are no longer reinforced.
    try:
        gm = _get_graph_mgr()
        if gm is not None:
            gm.run_decay()
    except Exception as e:
        logger.warning("ahe_graph decay skipped: %s", e)

    if not messages:
        return
    # Attempt full reflection via LLM if available
    ctx = kwargs.get("ctx") or _plugin_ctx
    if ctx is not None:
        try:
            _run_full_reflection(ctx, messages)
        except Exception as e:
            logger.warning("Full reflection failed: %s", e)
    else:
        logger.info("mem-reflection-hermes: session ended with %d messages — full reflection queued (no ctx)", len(messages))


def _pre_llm_call(**kwargs) -> Optional[Dict[str, str]]:
    """Inject layered context into the user message; also trigger micro-reflection."""
    messages = kwargs.get("messages") or kwargs.get("conversation_history") or []
    user_message = kwargs.get("user_message")
    if user_message:
        messages = list(messages) + [{"role": "user", "content": user_message}]
    session_id = kwargs.get("session_id", "")
    if session_id:
        _session_messages[session_id] = list(messages)[-80:]
    ctx = kwargs.get("ctx") or _plugin_ctx

    # Extract latest user query and assistant response for micro-reflection
    user_msg = ""
    assistant_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and not assistant_msg:
            content = msg.get("content", "")
            if isinstance(content, str):
                assistant_msg = content
        elif msg.get("role") == "user" and not user_msg:
            content = msg.get("content", "")
            if isinstance(content, str):
                user_msg = content
        if user_msg and assistant_msg:
            break

    # Trigger micro-reflection: explicit intent always, otherwise every 3 turns
    # (mirrors small-rust-hermes simplified heuristic)
    if _micro_reflection_enabled() and user_msg and assistant_msg:
        global _turns_since_reflect
        has_intent = _is_explicit_memory_intent(user_msg)
        if has_intent or _turns_since_reflect >= 3:
            # Guard: LLM-based micro-reflection requires ctx; skip if unavailable
            if ctx is None and _reflection_mode() == "llm":
                logger.debug("Micro-reflection skipped: ctx unavailable in llm mode")
            else:
                try:
                    _run_micro_reflection(ctx, user_msg, assistant_msg)
                    _turns_since_reflect = 0
                except Exception as e:
                    logger.debug("Micro-reflection failed: %s", e)
        else:
            _turns_since_reflect += 1

    # Build context block
    query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg.get("content"):
            query = msg.get("content", "")
            if isinstance(query, str):
                break
            query = ""
    try:
        context = _build_context_block(query)
    except Exception as e:
        # P2-22: phase failure should silently skip rather than fail the whole hook
        logger.warning("Context block build failed in pre_llm_call, skipping injection: %s", e)
        context = None
    if context:
        return {"context": context}
    return None


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

# === Graph utilities ===
def _get_graph_mgr():
    """Get or create graph manager singleton (thread-safe, module-level)."""
    global _gm_singleton
    if _gm_singleton is None and _gm_getter_func is not None:
        with _gm_singleton_lock:
            if _gm_singleton is None:
                _gm_singleton = _gm_getter_func(_gm_getter_path)
    return _gm_singleton


# ── Graph neighbor helper (used by search/palace tools) ───────────────────

def _get_graph_neighbors(memory_ids: List[str], max_results: int = 5,
                         zone_filter: Optional[str] = None) -> List[Tuple[str, float]]:
    """Look up graph neighbors for the given memory IDs.

    Returns deduplicated (memory_id, weight) pairs, sorted by weight descending.
    If zone_filter is provided, only returns neighbors whose zone matches.
    Gracefully returns empty list if ahe_graph is not available or has no data.
    """
    try:
        gm = _get_graph_mgr()
        if gm is None:
            return []
        results = gm.store.propagate_activation(
            seed_ids=memory_ids,
            max_depth=1,
            decay_factor=0.7,
            min_weight=0.2,
            limit=max_results,
        )
        # Deduplicate by memory_id, keep highest weight
        seen = {}
        for r in results:
            mid = r.get("memory_id", "")
            w = r.get("weight", 0.0)
            if mid and (mid not in seen or w > seen[mid]):
                seen[mid] = w
        # Filter out seed IDs (prevent self-return)
        seed_set = set(memory_ids)
        deduped = [(mid, w) for mid, w in seen.items() if mid not in seed_set]
        # Apply zone filter if specified (look up each neighbor's zone)
        if zone_filter:
            filtered = []
            for mid, w in deduped:
                meta = gm.store.get_meta(mid)
                if meta and meta.get("zone") == zone_filter:
                    filtered.append((mid, w))
            deduped = filtered
        deduped.sort(key=lambda x: -x[1])
        return deduped[:max_results]
    except Exception as e:
        logger.debug("Graph neighbor lookup failed: %s", e)
        return []


def _enrich_with_graph(result_ids: List[str], result_out: List[dict], k: int,
                       zone_filter: Optional[str] = None) -> dict:
    """Enrich search results with graph-neighbor memories (shared helper).

    Always returns a dict with "results" and "graph_expanded" for uniform schema.
    If zone_filter is provided, graph neighbors are restricted to that zone.
    """
    graph_expanded = _get_graph_neighbors(result_ids, max_results=k, zone_filter=zone_filter)
    for neigh_id, _ in graph_expanded:
        record_memory_stat(neigh_id, "accessed")
    return {
        "results": result_out,
        "graph_expanded": [{"id": mid, "weight": round(w, 3)} for mid, w in graph_expanded],
    }



def _slash_reflect(raw_args: str) -> str:
    return "🔍 Full reflection is now integrated with LLM. It runs automatically at session end, or you can trigger it via the srh_reflect_now tool."


def _slash_pending_skills(raw_args: str) -> str:
    """Show pending skill candidates for approval."""
    return _format_pending_skills_for_display()


def _slash_approve_skill(raw_args: str) -> str:
    """Approve a pending skill candidate by ID."""
    pending_id = raw_args.strip()
    if not pending_id:
        return "Usage: /approve-skill <pending_id>"
    result = _approve_skill(pending_id)
    if result and result.get("success"):
        return f"✅ Approved skill '{result['name']}' and saved to {result['path']}"
    return f"❌ Failed to approve: {result.get('error', 'Unknown error')}" if result else "❌ Failed to approve"


def _slash_reject_skill(raw_args: str) -> str:
    """Reject a pending skill candidate by ID."""
    parts = raw_args.strip().split(None, 1)
    if not parts:
        return "Usage: /reject-skill <pending_id> [reason]"
    pending_id = parts[0]
    reason = parts[1] if len(parts) > 1 else ""
    if _reject_skill(pending_id, reason):
        return f"❌ Rejected skill candidate {pending_id}"
    return f"❌ Failed to reject skill candidate {pending_id}"


def _slash_memories(raw_args: str) -> str:
    query = raw_args.strip()
    mem_store = _get_mem_store()
    if query:
        results = mem_store.search(query, k=10)
    else:
        results = mem_store.list_active()
    lines = [f"🧠 Active memories ({len(results)}):"]
    for m in results:
        pin = "📌" if m.frontmatter.pinned else "  "
        lines.append(f"{pin} [{m.id()}] {m.body[:120]}")
    return "\n".join(lines) if lines else "No memories found."


def _slash_skills(raw_args: str) -> str:
    query = raw_args.strip()
    skill_store = _get_skill_store()
    if query:
        skills = match_skills(skill_store.list(), query, k=10)
    else:
        skills = skill_store.list()
    lines = [f"🔧 Skills ({len(skills)}):"]
    for s in skills:
        lines.append(f"- {s.frontmatter.name}: {s.frontmatter.description}")
    return "\n".join(lines) if lines else "No skills found."


def _slash_compile_profile(raw_args: str) -> str:
    """Handle /compile-profile [mode] slash command."""
    mode = raw_args.strip() or "profile"
    if mode not in ("profile", "palace_index", "zone"):
        return f"⚠️ Unknown mode: {mode}. Use: profile, palace_index, or zone."
    # This requires ctx — can only work when called from a session with LLM access
    return (
        f"📋 Compile Profile command received (mode={mode}).\n"
        f"Use the srh_compile_profile tool with ctx access to execute, "
        f"or wait for session-end auto-compilation."
    )


# Embedding engine extracted to embed.py
from ..search.embed import *  # noqa: F401, F403



# ---------------------------------------------------------------------------
# Pending skill candidate approval system

# Reflection pipeline extracted to reflection.py
from ..reflection.engine import *  # noqa: F401, F403
