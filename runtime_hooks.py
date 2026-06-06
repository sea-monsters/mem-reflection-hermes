"""Runtime hook implementation for mem-reflection-hermes."""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .store import (
    LoadedMemory, MemoryFrontmatter, record_memory_stat,
    hermes_home as _hermes_home, plugin_data_dir as _plugin_data_dir,
)
from .reflect import (
    _run_full_reflection, _run_micro_reflection,
    _run_embedding_micro_reflection,
    _approve_skill, _reject_skill,
    _load_pending_skill_candidates,
    _format_pending_skills_for_display,
    _reset_current_session_memory_ids,
)
try:
    from .search import _is_explicit_memory_intent
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
    _is_explicit_memory_intent = _search_mod._is_explicit_memory_intent

logger = logging.getLogger(__name__)


__all__ = [
    "_enrich_with_graph",
    "_get_graph_mgr",
    "_get_graph_neighbors",
    "_gm_singleton",
    "_on_session_end",
    "_on_session_start",
    "_on_session_reset",
    "_on_api_request_error",
    "_on_subagent_start",
    "_on_subagent_stop",
    "_pre_llm_call",
    "_slash_approve_skill",
    "_slash_compile_profile",
    "_slash_memories",
    "_slash_pending_skills",
    "_slash_reflect",
    "_slash_reject_skill",
    "_slash_skills",
    "_turns_since_reflect",
    "_ensure_session_state",
    "_cleanup_session_state",
]

_plugin_ctx: Any = None
_session_messages: Dict[str, List[Dict[str, Any]]] = {}
_session_messages_lock = threading.Lock()  # H20: protect concurrent session access

# ── Session lifecycle tracking (v0.16.0 enhanced hooks) ─────────
_SessionState = dict  # lightweight runtime state bag
_session_states: Dict[str, _SessionState] = {}
_session_states_lock = threading.Lock()

def _ensure_session_state(session_id: str) -> _SessionState:
    """Get or create a lightweight state bag for a session."""
    with _session_states_lock:
        if session_id not in _session_states:
            _session_states[session_id] = {
                "api_error_count": 0,
                "rewind_count": 0,
                "subagent_count": 0,
                "created_at": time.time(),
            }
        return _session_states[session_id]

def _cleanup_session_state(session_id: str) -> None:
    """Remove session state after session ends."""
    with _session_states_lock:
        _session_states.pop(session_id, None)

def _set_plugin_context(ctx: Any) -> None:
    """Remember the host plugin context for hooks that run without ctx kwargs."""
    global _plugin_ctx
    _plugin_ctx = ctx

def _lb(name: str):
    from mem_reflection_hermes import __dict__ as _pkg_dict
    return _pkg_dict[name]

def _get_mem_store():
    return _lb("_get_mem_store")()

def _get_skill_store():
    return _lb("_get_skill_store")()

def _build_context_block(query=""):
    return _lb("_build_context_block")(query)

def _estimate_tokens(text):
    return _lb("_estimate_tokens")(text)


def _reflection_mode():
    return _lb("_reflection_mode")()

def _auto_rebalance_zones():
    return _lb("_auto_rebalance_zones")()

def _save_pending_skill_candidates(candidates):
    return _lb("_save_pending_skill_candidates")(candidates)

def _recent_reflect_outcomes(n=10):
    return _lb("_recent_reflect_outcomes")(n)

def _micro_reflection_enabled():
    return _lb("_micro_reflection_enabled")()

_turns_since_reflect: int = 0
_turns_since_reflect_lock = threading.Lock()  # H21: protect concurrent access

# ── Graph manager global singleton (set during plugin init) ─────────
_gm_getter_func = None
_gm_getter_path = None
_gm_singleton = None
_gm_singleton_lock = threading.Lock()


# === Hooks ===
def _on_session_start(**kwargs) -> None:
    global _turns_since_reflect
    with _turns_since_reflect_lock:
        _turns_since_reflect = 0
    _reset_current_session_memory_ids()
    logger.debug("mem-reflection-hermes: session started")



def _on_session_end(**kwargs) -> None:
    session_id = kwargs.get("session_id", "")
    reason = kwargs.get("reason", "")  # v0.16.0: "shutdown" | "session_expired" | "new_session"
    with _session_messages_lock:
        messages = kwargs.get("messages") or _session_messages.pop(session_id, [])

    # Skip reflection on expired sessions (timeout, no substantive content)
    if reason == "session_expired":
        logger.debug("Session %s expired — skipping reflection", session_id)
        _cleanup_session_state(session_id)
        return

    # Harvest API error stats for the reflection summary
    stats = _session_states.get(session_id, {})
    api_errors = stats.get("api_error_count", 0)
    subagent_count = stats.get("subagent_count", 0)

    # ── Periodic graph decay ──────────────────────────────
    try:
        gm = _get_graph_mgr()
        if gm is not None:
            gm.run_decay()
    except Exception as e:
        logger.warning("graph decay skipped: %s", e)

    try:
        if not messages:
            return
        ctx = kwargs.get("ctx") or _plugin_ctx
        if ctx is not None:
            try:
                _run_full_reflection(ctx, messages)
            except Exception as e:
                logger.warning("Full reflection failed: %s", e)
                logger.warning("Full reflection traceback:", exc_info=True)
        else:
            extras = []
            if api_errors:
                extras.append(f"{api_errors} API errors")
            if subagent_count:
                extras.append(f"{subagent_count} subagents")
            extra_info = f" ({', '.join(extras)})" if extras else ""
            logger.info(
                "mem-reflection-hermes: session ended with %d messages%s — full reflection queued (no ctx)",
                len(messages), extra_info,
            )
    finally:
        # ── Episode compaction (v1.1) ──────────────────────
        # Run after reflection to compact raw episode entries into summaries.
        try:
            from .runtime_reflection import _compact_episode_zone as _compact
            from . import _config_compaction as _cc
            if _cc():
                result = _compact(_lb("_get_mem_store")(), ctx)
                if result.get("compacted", 0) > 0:
                    logger.info(
                        "Episode compaction: %d clusters compressed "
                        "(consumed %d raw entries)",
                        result["compacted"],
                        result.get("total_raw_consumed", 0),
                    )
        except Exception as _ce:
            logger.debug("Episode compaction skipped: %s", _ce)

        _reset_current_session_memory_ids()
        _cleanup_session_state(session_id)


def _pre_llm_call(**kwargs) -> Optional[Dict[str, str]]:
    """Inject layered context into the user message; also trigger micro-reflection."""
    global _turns_since_reflect
    messages = kwargs.get("messages") or kwargs.get("conversation_history") or []
    user_message = kwargs.get("user_message")
    if user_message:
        messages = list(messages) + [{"role": "user", "content": user_message}]
    session_id = kwargs.get("session_id", "")
    if session_id:
        with _session_messages_lock:
            _session_messages[session_id] = list(messages)[-80:]
    ctx = kwargs.get("ctx") or _plugin_ctx

    # Extract latest user query and assistant response for micro-reflection
    # Skip assistant messages that contain tool_calls (these are tool invocations)
    user_msg = ""
    assistant_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and not assistant_msg:
            # Skip if this is a tool invocation message (contains tool_calls)
            if "tool_calls" in msg:
                continue
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
        has_intent = _is_explicit_memory_intent(user_msg)
        should_reflect = False
        with _turns_since_reflect_lock:
            if has_intent or _turns_since_reflect >= 3:
                should_reflect = True
            else:
                _turns_since_reflect += 1

        if should_reflect:
            reflection_ran = False
            # Guard: LLM-based micro-reflection requires ctx; skip if unavailable
            if ctx is None and _reflection_mode() == "llm":
                logger.debug("Micro-reflection skipped: ctx unavailable in llm mode")
            else:
                try:
                    _run_micro_reflection(ctx, user_msg, assistant_msg)
                    reflection_ran = True
                except Exception as e:
                    logger.debug("Micro-reflection failed: %s", e)
            if reflection_ran:
                with _turns_since_reflect_lock:
                    _turns_since_reflect = 0

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

    # Token budget enforcement (beta3: truncate or skip if context too large)
    if context:
        try:
            tok = _estimate_tokens(context)
            # Default 2000-token budget for injected context; overridable via config
            budget = 2000
            try:
                from .store import plugin_config
                budget = plugin_config().get("memory", {}).get("context_token_budget", 2000)
            except Exception:
                pass
            if tok > budget:
                # Hard truncate to budget (rough: 4 chars/token for ASCII)
                trunc_len = int(budget * 3.5)
                context = context[:trunc_len] + "\n...[context truncated]"
                logger.debug("Context truncated from %d to ~%d tokens", tok, budget)
        except Exception:
            pass  # Budget check failure is non-fatal
        return {"context": context}
    return None


def _post_tool_call(**kwargs) -> None:
    """Hook called after a tool invocation (v0.16.0 enhanced).

    Three responsibilities:
    1. Bridge Dir A: mirror built-in ``memory`` tool writes to plugin store.
    2. Enrich graph with co-accessed memories (existing).
    3. Track tool status/duration for effectiveness adjustments.

    v0.16.0+ kwargs received:
      tool_name, args, result,
      session_id, task_id, tool_call_id, turn_id, api_request_id,
      status, error_type, error_message, duration_ms
    """
    tool_name = kwargs.get("tool_name", "")
    result = kwargs.get("result", "")
    args = kwargs.get("args", {})
    status = kwargs.get("status", "ok")
    duration_ms = kwargs.get("duration_ms", 0)
    session_id = kwargs.get("session_id", "")
    turn_id = kwargs.get("turn_id", "")
    tool_call_id = kwargs.get("tool_call_id", "")
    _turn_tag = f" [{turn_id}]" if turn_id else ""

    mem_ids: List[str] = []

    # Parse result once (shared by Dir A and graph enrichment)
    result_obj: Any = None
    try:
        if isinstance(result, str):
            result_obj = json.loads(result)
        else:
            result_obj = result
    except Exception:
        result_obj = None

    # ── Dir A: Bridge built-in memory writes to plugin store ──────────────
    if tool_name == "memory" and status != "error":
        try:
            if isinstance(result_obj, dict) and result_obj.get("success"):
                action = args.get("action", "")
                target = result_obj.get("target", "memory")
                content = args.get("content", result_obj.get("message", ""))
                old_text = args.get("old_text", "")
                entries_after = result_obj.get("entries", None)

                from .memory_bridge import bridge_enabled as _bridge_enabled
                if _bridge_enabled():
                    from .memory_bridge import mirror_builtin_to_plugin as _mirror
                    try:
                        _mirror(
                            action=action,
                            target=target,
                            content=content,
                            old_text=old_text,
                            entries_after=entries_after,
                            mem_store=_lb("_get_mem_store")(),
                        )
                    except Exception as _be:
                        logger.debug("Bridge Dir A failed: %s", _be)
        except Exception:
            pass

    # ── Effectiveness tracking (v0.16.0 enhanced) ─────────────────────────
    # Log slow operations (>10s) for diagnostics
    if duration_ms > 10_000:
        logger.debug(
            "Slow tool call%s: %s took %dms with status=%s",
            _turn_tag, tool_name, duration_ms, status,
        )

    # ── Graph enrichment (existing logic) ─────────────────────────────────
    if not any(t in tool_name for t in ("memory", "palace", "skill")):
        return

    if isinstance(result_obj, dict):
        if "id" in result_obj:
            mem_ids.append(result_obj["id"])
        for key in ("results", "memories", "chain", "graph_expanded"):
            for item in result_obj.get(key, []):
                if isinstance(item, dict):
                    mid = item.get("id") or item.get("memory_id")
                    if mid:
                        mem_ids.append(mid)

    if len(mem_ids) >= 2:
        try:
            gm = _get_graph_mgr()
            if gm:
                # v0.16.0: use status to gate graph enrichment —
                # on error, skip association entirely (no point linking
                # memories from a failed operation)
                if status == "error":
                    logger.debug(
                        "Skipped graph enrichment%s: %s (status=%s)",
                        _turn_tag, tool_name, status,
                    )
                else:
                    gm.associator.on_co_occurrence(
                        mem_ids, context=tool_name,
                    )
        except Exception as e:
            logger.debug("Graph enrichment failed in post_tool_call: %s", e)


def _on_api_request_error(**kwargs) -> None:
    """Track API request failures for reflection triggers."""
    session_id = kwargs.get("session_id", "")
    if not session_id:
        return
    state = _ensure_session_state(session_id)
    state["api_error_count"] = state.get("api_error_count", 0) + 1
    error_type = kwargs.get("error", {}).get("type", "unknown")
    # Log threshold crossing for debugging
    n = state["api_error_count"]
    if n in (1, 5, 10, 25, 50):
        logger.info(
            "mem-reflection: session %s hit %d API errors (latest: %s)",
            session_id, n, error_type,
        )


def _on_subagent_start(**kwargs) -> None:
    """Track subagent lifecycle start for session reflection context (v0.16.0 enhanced)."""
    session_id = kwargs.get("session_id", "")
    if not session_id:
        return
    state = _ensure_session_state(session_id)
    state.setdefault("subagent_count", 0)
    # Record start time for duration tracking on stop
    state["_subagent_start_time"] = time.time()
    state["_subagent_active"] = state.get("_subagent_active", 0) + 1
    logger.debug(
        "mem-reflection: subagent started in session %s (active=%d)",
        session_id, state.get("_subagent_active", 0),
    )


def _on_subagent_stop(**kwargs) -> None:
    """Track subagent lifecycle for session reflection context."""
    session_id = kwargs.get("session_id", "")
    if not session_id:
        return
    state = _ensure_session_state(session_id)
    state["subagent_count"] = state.get("subagent_count", 0) + 1


def _on_session_reset(**kwargs) -> None:
    """Handle session rotation with enhanced v0.16.0 kwargs."""
    reason = kwargs.get("reason", "unknown")
    old_sid = kwargs.get("old_session_id", "")
    new_sid = kwargs.get("new_session_id", "")
    logger.debug(
        "mem-reflection: session rotated %s -> %s (reason=%s)",
        old_sid, new_sid, reason,
    )


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
    Gracefully returns empty list if graph data is not available or has no data.
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
        from . import match_skills as _match_skills
        skills = _match_skills(skill_store.list(), query, k=10)
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


def register_commands(ctx) -> None:
    """Register the non-graph slash command surface."""
    ctx.register_command(
        name="reflect",
        handler=lambda raw: _slash_reflect(raw),
        description="Trigger a full reflection on the current session",
        args_hint="",
    )
    ctx.register_command(
        name="pending-skills",
        handler=lambda raw: _slash_pending_skills(raw),
        description="Show pending skill candidates awaiting approval",
        args_hint="",
    )
    ctx.register_command(
        name="approve-skill",
        handler=lambda raw: _slash_approve_skill(raw),
        description="Approve a pending skill candidate by ID",
        args_hint="<pending_id>",
    )
    ctx.register_command(
        name="reject-skill",
        handler=lambda raw: _slash_reject_skill(raw),
        description="Reject a pending skill candidate by ID",
        args_hint="<pending_id> [reason]",
    )
    ctx.register_command(
        name="memories",
        handler=lambda raw: _slash_memories(raw),
        description="List active memories",
        args_hint="[query]",
    )
    ctx.register_command(
        name="skills",
        handler=lambda raw: _slash_skills(raw),
        description="List or search skills",
        args_hint="[query]",
    )
    ctx.register_command(
        name="compile-profile",
        handler=lambda raw: _slash_compile_profile(raw),
        description="Compile all memories into a structured profile via LLM",
        args_hint="[profile|palace_index|zone]",
    )


set_plugin_context = _set_plugin_context
on_session_start = _on_session_start
on_session_end = _on_session_end
pre_llm_call = _pre_llm_call
post_tool_call = _post_tool_call
on_api_request_error = _on_api_request_error
on_subagent_start = _on_subagent_start
on_subagent_stop = _on_subagent_stop
on_session_reset = _on_session_reset


def register_hooks(ctx) -> None:
    """Register the public hook surface on the host context (v0.16.0 telemetry hooks)."""
    set_plugin_context(ctx)
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("on_session_end", on_session_end)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    # v0.16.0+ enhanced hooks — zero-cost via has_hook() when no listener
    ctx.register_hook("api_request_error", on_api_request_error)
    ctx.register_hook("subagent_start", on_subagent_start)
    ctx.register_hook("subagent_stop", on_subagent_stop)
    ctx.register_hook("on_session_reset", on_session_reset)


register = register_hooks


__all__ = list(__all__) + [
    "on_session_end",
    "on_session_start",
    "on_session_reset",
    "on_api_request_error",
    "on_subagent_start",
    "on_subagent_stop",
    "post_tool_call",
    "pre_llm_call",
    "register",
    "register_commands",
    "register_hooks",
    "set_plugin_context",
]
