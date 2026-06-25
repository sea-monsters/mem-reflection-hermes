"""Runtime reflection pipelines for mem-reflection-hermes."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core.store import (
    LoadedMemory, MemoryFrontmatter,
    hermes_home as _hermes_home, plugin_data_dir as _plugin_data_dir,
    user_skills_dir as _user_skills_dir,
    micro_reflection_enabled, profile_mode_enabled, plugin_config,
    parse_frontmatter, serialize_frontmatter,
    _tokenise, _lineage_cycle_check,
)
from ..core.search import (
    _embed_single, _cosine_sim, _extract_keywords,
    _is_explicit_memory_intent, _is_procedure,
)
from ..core.scope import normalize_scope_filters, scope_from_context
from .extraction import extract_refined_memory_candidates, REFINED_MEMORY_KINDS, normalize_memory_kind
from .supersedes_resolver import resolve_semantic_supersedes

logger = logging.getLogger(__name__)

# Thread-safe locks for file-based operations
_reflect_log_lock = threading.Lock()
_pending_skills_lock = threading.Lock()
_current_session_memory_ids = threading.local()

__all__ = [
    "_FULL_REFLECT_SYSTEM",
    "_MICRO_REFLECT_SYSTEM",
    "_append_reflect_log",
    "_approve_skill",
    "_auto_rebalance_zones",
    "_build_audit_entry",
    "_build_context_block",
    "_build_reflect_schema",
    "_compute_novelty_score",
    "_estimate_tokens",
    "_extract_facts_from_turn",
    "_find_conflicting_memory",
    "_format_inventory",
    "_format_messages_for_reflection",
    "_format_pending_skills_for_display",
    "_generate_session_summary",
    "_generate_skill_name",
    "_get_mem_store",
    "_get_skill_store",
    "_is_noise_text",
    "_load_pending_skill_candidates",
    "_parse_reflect_output",
    "_recent_reflect_outcomes",
    "_reflection_mode",
    "_reject_skill",
    "_repair_truncated_json",
    "_reset_current_session_memory_ids",
    "_run_embedding_micro_reflection",
    "_run_embedding_reflection",
    "_run_full_reflection",
    "_run_micro_reflection",
    "_save_pending_skill_candidates",
    "_strip_code_fence",
    "_text_similarity",
    "_tfidf_max_similarity",
    "_update_pending_skill_status",
]

def _package_root():
    pkg_name = __package__.rsplit(".", 1)[0] if __package__ and "." in __package__ else __package__
    if not pkg_name:
        raise RuntimeError("reflection engine package root is unavailable")
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        raise RuntimeError(f"Package module {pkg_name!r} is not loaded")
    return pkg


def _get_mem_store():
    """Return the package-level MemoryStore singleton."""
    return _package_root()._get_mem_store()


def _get_skill_store():
    return _package_root()._get_skill_store()


def _estimate_tokens(text):
    return _package_root()._estimate_tokens(text)


def _auto_rebalance_zones():
    return _package_root()._auto_rebalance_zones()


def _build_context_block(query=""):
    return _package_root()._build_context_block(query)


def _reflection_mode() -> str:
    """Reflection mode from config."""
    return _package_root()._reflection_mode()


def _scope_filters_from_context(ctx=None, filters: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Optional[str]]]:
    """Resolve reflection scope from a host context object and/or explicit filters."""
    return scope_from_context(ctx, filters)


def _frontmatter_for_scope(
    *,
    source: str,
    confidence: str,
    tags: List[str],
    zone: str,
    supersedes: Optional[List[str]] = None,
    supersedes_reason: str = "",
    scope_filters: Optional[Dict[str, Any]] = None,
) -> MemoryFrontmatter:
    """Build a MemoryFrontmatter with optional tenant scope fields applied."""
    normalized = normalize_scope_filters(scope_filters) or {}
    return MemoryFrontmatter.new(
        source=source,
        confidence=confidence,
        tags=tags,
        zone=zone,
        supersedes=supersedes or [],
        supersedes_reason=supersedes_reason,
        user_id=normalized.get("user_id"),
        agent_id=normalized.get("agent_id"),
        run_id=normalized.get("run_id"),
    )


def _graph_for_store(mem_store: Any) -> Optional[Any]:
    """Resolve the graph interface for a store without coupling to _graph attr.

    Production runtime sets store._graph via registration, but tests and scripts
    may not. Fall back to the package-level graph manager so typed sidecar and
    other graph consumers still work.
    """
    graph = getattr(mem_store, "_graph", None)
    if graph is not None and hasattr(graph, "record_typed_fact"):
        return graph
    try:
        from ..runtime.graph import get_graph_manager_compat
        return get_graph_manager_compat()
    except Exception:
        try:
            from mem_reflection_hermes.runtime.graph import get_graph_manager_compat
            return get_graph_manager_compat()
        except Exception:
            return None


def _record_typed_fact_sidecar(
    mem_store: Any,
    fm: MemoryFrontmatter,
    body: str,
    *,
    relation: str = "describes",
    kind: str = "fact",
    subject: Optional[str] = None,
    object: Optional[str] = None,
    target_memory_id: Optional[str] = None,
    episode_id: Optional[str] = None,
    source: str = "reflection",
    confidence: float = 0.5,
    superseded_memory_ids: Optional[List[str]] = None,
) -> None:
    """Best-effort typed sidecar write for graph-enabled stores.

    When ``superseded_memory_ids`` is provided, the typed facts owned by those
    memories are invalidated by the newly written memory (``fm.id``). This is
    what makes the Phase D sidecar actually carry temporal truth — without it
    the invalidation column stayed NULL forever and ``include_invalidated=False``
    queries kept returning superseded facts.
    """
    graph = _graph_for_store(mem_store)
    if graph is None:
        return
    try:
        graph.record_typed_fact(
            fm.id,
            body,
            relation=relation,
            kind=kind,
            subject=subject,
            object=object,
            target_memory_id=target_memory_id or fm.id,
            episode_id=episode_id or fm.id,
            zone=getattr(fm, "zone", "general") or "general",
            source=source,
            confidence=float(confidence),
        )
        if hasattr(graph, "record_entity_mentions"):
            graph.record_entity_mentions(
                fm.id,
                body,
                episode_id=episode_id or fm.id,
                zone=getattr(fm, "zone", "general") or "general",
                source=source,
                target_memory_id=target_memory_id or fm.id,
                confidence=float(confidence),
            )
        # Phase D: invalidate the typed facts owned by memories this one
        # supersedes, so the sidecar reflects temporal truth instead of
        # accumulating stale rows alongside the replacement.
        if superseded_memory_ids:
            invalidate = getattr(graph, "invalidate_facts_for_memories", None)
            if invalidate is not None:
                # Batch-invalidate all typed facts owned by superseded memories.
                # If per-fact invalidation is needed later (invalidate one fact
                # while keeping others from the same source), also available:
                #   graph.invalidate_typed_fact(fact_id, invalidated_by=...)
                invalidate(list(superseded_memory_ids), invalidated_by=fm.id)
    except Exception as e:
        logger.warning("Typed sidecar write failed for %s: %s", fm.id, e, exc_info=True)


def _record_semantic_relation_sidecar(
    mem_store: Any,
    fm: MemoryFrontmatter,
    *,
    action: str,
    target_ids: List[str],
    reason: str = "",
    confidence: float = 0.5,
) -> None:
    """Record a Phase C semantic-relation typed edge for merge/scope_split.

    ``supersede`` already carries its lineage through ``fm.supersedes`` plus the
    sidecar invalidation in :func:`_record_typed_fact_sidecar`. ``merge`` and
    ``scope_split`` carry no supersedes edge, so without this helper their
    decisions were silently dropped (round-3 audit P1-2). This writes a single
    typed edge per target so the relation is queryable without re-running the
    resolver.
    """
    target_ids = [tid for tid in (target_ids or []) if tid]
    if action not in ("merge", "scope_split") or not target_ids:
        return
    graph = _graph_for_store(mem_store)
    if graph is None:
        return
    relation = "merges" if action == "merge" else "scope_split_with"
    kind = "semantic_merge" if action == "merge" else "semantic_scope_split"
    zone = getattr(fm, "zone", "general") or "general"
    for target_id in target_ids:
        try:
            graph.record_typed_fact(
                fm.id,
                reason or f"{action} {target_id}",
                relation=relation,
                kind=kind,
                subject=fm.id,
                object=target_id,
                target_memory_id=target_id,
                episode_id=fm.id,
                zone=zone,
                source="reflection",
                confidence=float(confidence),
            )
        except Exception as e:
            logger.warning("Semantic relation sidecar failed for %s: %s", fm.id, e, exc_info=True)
            break


def _build_audit_entry(
    candidate_id: str = "",
    decision: str = "",
    decision_reason: str = "",
    novelty_score: float = 0.0,
    conflict_id: str = "",
    supersedes_ids: Optional[List[str]] = None,
    supersedes_reason: str = "",
    assigned_zone: str = "",
    graph_migration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a structured reflection audit entry for the reflect log.

    decision values: accepted | rejected | conflicted | superseded | skipped
    """
    return {
        "candidate_id": candidate_id,
        "decision": decision,
        "decision_reason": decision_reason,
        "novelty_score": round(novelty_score, 4),
        "conflict_id": conflict_id,
        "supersedes_ids": supersedes_ids or [],
        "supersedes_reason": supersedes_reason,
        "assigned_zone": assigned_zone,
        "graph_migration": graph_migration or {},
    }


def _get_current_session_memory_ids() -> Set[str]:
    ids = getattr(_current_session_memory_ids, "ids", None)
    if not ids:
        return set()
    return set(ids)



def _remember_current_session_memory_id(memory_id: str) -> None:
    ids = _get_current_session_memory_ids()
    ids.add(memory_id)
    _current_session_memory_ids.ids = ids



def _reset_current_session_memory_ids() -> None:
    if hasattr(_current_session_memory_ids, "ids"):
        delattr(_current_session_memory_ids, "ids")


# # Block 1: Reflection log

# ---------------------------------------------------------------------------
# Reflection log
# ---------------------------------------------------------------------------

REFLECT_LOG_PATH = _plugin_data_dir() / "reflect-log.jsonl"
_MAX_REFLECT_LOG_LINES = 5000  # P2-29: auto-rotate after this many entries
_MAX_ARCHIVE_AGE_DAYS = 30  # M10: auto-delete archives older than this
# MED-7: approximate line count to avoid reading the full file on every append
_reflect_log_line_count: int = 0


def _append_reflect_log(entry: Dict[str, Any]) -> None:
    global _reflect_log_line_count
    try:
        with _reflect_log_lock:
            REFLECT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            # P2-29: auto-rotate when exceeding _MAX_REFLECT_LOG_LINES
            if REFLECT_LOG_PATH.exists():
                try:
                    if _reflect_log_line_count == 0:
                        # First call after module load: count existing lines
                        with open(REFLECT_LOG_PATH, "rb") as f:
                            for _ in f:
                                _reflect_log_line_count += 1
                    if _reflect_log_line_count >= _MAX_REFLECT_LOG_LINES:
                        archive_path = REFLECT_LOG_PATH.with_suffix(
                            f".{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.jsonl"
                        )
                        REFLECT_LOG_PATH.rename(archive_path)
                        logger.debug(
                            "Reflect log rotated to %s (%d lines archived)",
                            archive_path,
                            _reflect_log_line_count,
                        )
                        _reflect_log_line_count = 0
                        # M10: purge old archives beyond retention window
                        try:
                            cutoff = datetime.now(timezone.utc).timestamp() - _MAX_ARCHIVE_AGE_DAYS * 86400
                            for old in REFLECT_LOG_PATH.parent.glob("reflect-log.*.jsonl"):
                                if old.stat().st_mtime < cutoff:
                                    old.unlink(missing_ok=True)
                        except Exception:
                            logger.debug("Archive cleanup failed", exc_info=True)
                except Exception:
                    logger.warning("Reflect log rotation failed", exc_info=True)
            with open(REFLECT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                _reflect_log_line_count += 1
    except Exception:
        logger.warning("Reflect log append failed", exc_info=True)


def _recent_reflect_outcomes(n: int = 10) -> List[Dict[str, Any]]:
    try:
        with _reflect_log_lock:
            if not REFLECT_LOG_PATH.exists():
                return []
            lines = REFLECT_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
        out = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                logger.debug("Skipping malformed reflect log line", exc_info=True)
        return out
    except Exception:
        logger.warning("Reflect log read failed", exc_info=True)
        return []




# # Block 2: Reflection prompts

# ---------------------------------------------------------------------------
# Reflection prompts
# ---------------------------------------------------------------------------

_FULL_REFLECT_SYSTEM = """You are a reflection module for a self-evolving agent. After each completed
session you are given the full transcript plus the agent's current skill /
memory inventory. Your job is to identify three things, and only when each
case truly meets the bar:

1. SKILL CANDIDATES — reusable procedures the agent worked out, with clear
triggers and a self-contained body of instructions in markdown. Only
propose if you would genuinely want the same procedure applied next
time the same situation appears. Skip anything that was a one-shot
exploration.

2. MEMORY CANDIDATES — durable facts, conventions, preferences, or
constraints the agent discovered that should persist across sessions.
One claim per memory. Default `scope` to `user`; pick `project` only
when the fact is specific to the current repo / codebase. Set `kind`
to the most specific category that applies: "fact" (default, a bare
factual statement), "preference" (what the user likes/dislikes/wants),
"decision" (a choice or policy adopted going forward), "policy"
(convention/rule/config), "todo" (a follow-up / action item),
"correction" (a revision of a prior statement), or "intent"
(an explicit "remember this" request).

3. CONFLICTS — when a new memory candidate contradicts, duplicates, or
subsumes an existing memory, report a conflict referencing the existing
memory id and proposing resolution options. Use kind "stale" when an
existing memory is factually wrong or outdated.

CRITICAL — the agent's user sees every candidate and must decide. Spammy
proposals erode trust. Default to empty arrays. Prefer false negatives over
false positives. Confidence = "high" should be rare.

Reply with EXACTLY ONE JSON object matching this schema. No prose. No
markdown fences. No commentary.

{
  "summary": "<one sentence summarising what the session accomplished>",
  "skill_candidates": [
    {
      "name": "kebab-case-name",
      "description": "one-line description for matcher",
      "triggers": ["keyword", "phrase"],
      "body": "## Title\n\nFull markdown instructions, multi-line.",
      "rationale": "why this is reusable enough to keep",
      "confidence": "low" | "medium" | "high"
    }
  ],
  "memory_candidates": [
    {
      "fact": "one short statement; one fact per memory",
      "tags": ["rust", "convention"],
      "scope": "user" | "project",
      "kind": "fact" | "preference" | "decision" | "policy" | "todo" | "correction" | "intent",
      "confidence": "low" | "medium" | "high",
      "rationale": "why this should persist",
      "supersedes": ["mem_xxxx"],
      "supersedes_reason": "human-readable reason why this replaces the referenced memory(s)"
    }
  ],
  "conflicts": [
    {
      "with": "mem_xxxx",
      "kind": "contradiction" | "redundancy" | "scope_overlap" | "stale",
      "explain": "what the disagreement is",
      "options": ["keep_old", "keep_new", "merge", "scope_split"]
    }
  ]
}"""

_MICRO_REFLECT_SYSTEM = """You are a micro-reflection module. You just observed ONE turn of conversation (user request + assistant response). Decide if anything from this turn is worth persisting as a memory or skill, and whether any existing memory is now stale.

Rules:
- Default to empty arrays. Most turns produce nothing.
- Only propose a memory if the user stated a durable preference, convention, or fact.
- Only propose a skill if the assistant followed a multi-step procedure that would be reusable verbatim next time.
- Never propose more than 1 memory and 1 skill per micro-reflection.
- Set `kind` to the most specific category: "preference", "decision", "policy", "todo", "correction", "intent", or default "fact".
- Confidence should be "low" or "medium" — never "high" for micro-reflection.
- If the conversation reveals that an existing memory is WRONG or OUTDATED, produce a memory_candidates entry with the corrected fact and set `supersedes` to the old memory's id, plus a conflicts entry with kind "stale".

Reply with EXACTLY ONE JSON object:
{
  "summary": "<one sentence>",
  "skill_candidates": [],
  "memory_candidates": [{"fact": "<short statement>", "tags": [], "scope": "user", "kind": "fact", "confidence": "low|medium", "rationale": "<why>", "supersedes": ["mem_xxx"]}],
  "conflicts": [{"with": "mem_xxx", "kind": "stale", "explain": "<why old memory is wrong>", "options": ["keep_new", "keep_old"]}]
}"""




# # Block 3: Reflection runner
# Reflection runner
# ---------------------------------------------------------------------------

def _strip_code_fence(s: str) -> str:
    s = s.strip()
    for prefix in ("```json", "```"):
        if s.startswith(prefix):
            rest = s[len(prefix):].strip()
            if "```" in rest:
                rest = rest[:rest.rfind("```")]
            return rest.strip()
    if "{" in s:
        return s[s.find("{"):]
    return s


def _repair_truncated_json(s: str) -> Optional[str]:
    s = s.strip()
    if not s.startswith("{"):
        return None

    def _close_open_containers(prefix: str) -> Optional[str]:
        stack: List[str] = []
        in_str = False
        escape = False
        for ch in prefix:
            if escape:
                escape = False
                continue
            if ch == "\\" and in_str:
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in "[{":
                stack.append(ch)
            elif ch == "]":
                if not stack or stack[-1] != "[":
                    return None
                stack.pop()
            elif ch == "}":
                if not stack or stack[-1] != "{":
                    return None
                stack.pop()
        if in_str or escape:
            return None
        repaired = prefix.rstrip()
        while stack:
            opener = stack.pop()
            repaired += "]" if opener == "[" else "}"
        return repaired

    try:
        json.loads(s)
        return None
    except Exception:
        logger.debug("_repair_truncated_json: plain load failed")
        pass

    repaired_full = _close_open_containers(s)
    if repaired_full is not None:
        tail_char = s.rstrip()[-1]
        if tail_char not in "[{:,":
            try:
                json.loads(repaired_full)
                return repaired_full
            except Exception:
                logger.debug("_repair_truncated_json: repaired_full load failed")
                pass

    for end in range(len(s) - 1, 0, -1):
        prefix = s[:end].rstrip()
        if not prefix or prefix == "{":
            continue
        if prefix.endswith((":", ",", "[", "{")):
            continue
        repaired = _close_open_containers(prefix)
        if repaired is None:
            continue
        try:
            json.loads(repaired)
            return repaired
        except Exception:
            logger.debug("_repair_truncated_json: truncation prefix load failed")
            continue
    return None


def _parse_reflect_output(text: str) -> Optional[Dict[str, Any]]:
    json_str = _strip_code_fence(text)
    # P2-26: extract JSON object from surrounding text if present
    # beta3-fix: use json.JSONDecoder.raw_decode to handle nested structures
    # correctly instead of greedy regex r'\{.*\}' which fails on multi-JSON.
    import json as _json
    decoder = _json.JSONDecoder()
    i = 0
    while i < len(json_str):
        if json_str[i] == "{":
            try:
                obj, end = decoder.raw_decode(json_str, i)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                logger.debug("_parse_reflect_output: raw_decode failed at pos %d", i)
                pass
        i += 1
    # Fallback: try the whole string
    try:
        return _json.loads(json_str)
    except Exception as first_err:
        repaired = _repair_truncated_json(json_str)
        if repaired:
            try:
                return _json.loads(repaired)
            except Exception:
                logger.debug("_parse_reflect_output: repaired load failed")
                pass
        logger.warning("Reflection JSON parse failed: %s", first_err)
        return None




# # Block 4: LLM-powered reflection

# ---------------------------------------------------------------------------
# LLM-powered reflection (kept as fallback for hybrid mode)
# ---------------------------------------------------------------------------

def _tfidf_max_similarity(text: str, memories: List[LoadedMemory]) -> float:
    """Max BM25 similarity between text and existing memories (0-1 normalized)."""
    scored = _package_root()._bm25_search_scored(memories, text, k=min(5, len(memories) or 1))
    if scored:
        # Normalize BM25 score to 0-1 using sigmoid approximation
        raw = scored[0][1]
        return raw / (raw + 1.0)
    return 0.0


def _build_reflect_schema() -> Dict[str, Any]:
    """Build JSON schema for reflection structured output."""
    # Exclude internal-only kinds (summary/raw_chunk) from the LLM-facing enum:
    # those are produced by the embedding/compaction pipeline, not by the model.
    llm_kinds = [k for k in REFINED_MEMORY_KINDS if k not in ("summary", "raw_chunk")]
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "skill_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "triggers": {"type": "array", "items": {"type": "string"}},
                        "body": {"type": "string"},
                        "rationale": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": ["name", "description", "triggers", "body", "rationale", "confidence"],
                },
            },
            "memory_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "scope": {"type": "string", "enum": ["user", "project"]},
                        "kind": {"type": "string", "enum": llm_kinds},
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                        "rationale": {"type": "string"},
                        "supersedes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["fact", "tags", "scope", "confidence", "rationale"],
                },
            },
            "conflicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "with": {"type": "string"},
                        "kind": {"type": "string", "enum": ["contradiction", "redundancy", "scope_overlap", "stale"]},
                        "explain": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["with", "kind", "explain", "options"],
                },
            },
        },
        "required": ["summary", "skill_candidates", "memory_candidates", "conflicts"],
    }


# Max transcript chars to avoid blowing LLM context on ultra-long sessions
_MAX_REFLECT_TRANSCRIPT_CHARS = 16000


def _format_messages_for_reflection(messages: List[Dict[str, Any]]) -> str:
    """Format message list into a transcript string for reflection.
    Truncates to _MAX_REFLECT_TRANSCRIPT_CHARS to keep costs bounded on
    ultra-long sessions (P1).

    Filters out tool role messages and assistant messages that contain
    tool_calls to prevent storing tool outputs and invocation records
    in memory.
    """
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")

        # Skip tool role messages (tool outputs) entirely
        if role == "tool":
            continue

        # Skip assistant messages that are tool invocations
        if role == "assistant" and "tool_calls" in msg:
            continue

        content = msg.get("content", "")
        if isinstance(content, list):
            # Extract text from multi-modal content
            texts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(part.get("text", ""))
            content = "\n".join(texts)
        elif not isinstance(content, str):
            content = str(content)

        lines.append(f"[{role}] {content}")
    result = "\n\n".join(lines)
    if len(result) > _MAX_REFLECT_TRANSCRIPT_CHARS:
        logger.debug(
            "Reflection transcript truncated from %d to %d chars",
            len(result), _MAX_REFLECT_TRANSCRIPT_CHARS,
        )
        result = result[:_MAX_REFLECT_TRANSCRIPT_CHARS:]
    return result


def _format_inventory(scope_filters: Optional[Dict[str, Any]] = None) -> str:
    """Format current memory and skill inventory for reflection context."""
    mem_store = _get_mem_store()
    skill_store = _get_skill_store()
    lines = ["=== Current Memory Inventory ==="]
    active_mems = mem_store.list_active(filters=scope_filters)
    for m in active_mems:
        lines.append(f"- [{m.id()}] {m.body[:120]} (tags: {m.frontmatter.tags}, confidence: {m.frontmatter.confidence})")
    lines.append("")
    lines.append("=== Current Skill Inventory ===")
    for s in skill_store.list():
        lines.append(f"- {s.frontmatter.name}: {s.frontmatter.description} (triggers: {s.frontmatter.triggers})")
    return "\n".join(lines)


def _run_full_reflection(
    ctx,
    messages: List[Dict[str, Any]],
    scope_filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a full reflection. Default is raw_chunk (zero LLM cost);
    falls back to embedding/LLM if configured."""
    mode = _reflection_mode()
    scope_filters = _scope_filters_from_context(ctx, scope_filters)

    # W2: raw_chunk mode — zero LLM calls, store raw conversation chunks
    if mode == "raw_chunk":
        return _run_raw_chunk_reflection(messages, scope_filters=scope_filters)

    # embedding-based (local, zero cost)
    if mode in ("embedding", "local"):
        return _run_embedding_reflection(messages, scope_filters=scope_filters)

    # Hybrid: try embedding first, if no candidates found, try LLM
    if mode == "hybrid":
        emb_result = _run_embedding_reflection(messages, scope_filters=scope_filters)
        if emb_result.get("accepted_memories") or emb_result.get("skill_candidates"):
            return emb_result
        logger.info("Hybrid mode: embedding found no candidates, trying LLM fallback")
        # Fall through to LLM

    # LLM mode (expensive, kept for compatibility)
    if not hasattr(ctx, "llm"):
        logger.warning("No ctx.llm available for full reflection")
        return {"error": "No LLM available"}

    transcript = _format_messages_for_reflection(messages)
    user_msgs = []
    for msg in messages:
        if msg.get("role", "") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(texts)
            if isinstance(content, str):
                user_msgs.append(content)
    full_user = " ".join(user_msgs)
    inventory = _format_inventory(scope_filters)

    instructions = (
        "Analyze the following conversation transcript and current agent inventory. "
        "Identify skill candidates, memory candidates, and conflicts. "
        "Be conservative — only propose high-quality candidates."
    )

    inputs = [
        {"type": "text", "text": f"=== TRANSCRIPT ===\n\n{transcript}\n\n{inventory}"},
    ]

    try:
        result = ctx.llm.complete_structured(
            instructions=instructions,
            input=inputs,
            json_schema=_build_reflect_schema(),
            json_mode=True,
            system_prompt=_FULL_REFLECT_SYSTEM,
            purpose="full_reflection",
            max_tokens=4096,
            timeout=30,
        )
    except Exception as e:
        logger.warning("LLM reflection call failed: %s", e)
        return {"error": str(e)}

    parsed = result.parsed if result else None
    if not parsed:
        logger.warning("Reflection produced no parsed output")
        return {"error": "No parsed output"}

    # Collect audit entries for each memory candidate
    audit_entries: List[Dict[str, Any]] = []
    mem_store = _get_mem_store()
    accepted_memories = []

    for cand in parsed.get("memory_candidates", []):
        body = cand.get("fact", "")
        scope = cand.get("scope", "user")
        # P2-1: normalize the LLM-provided kind so the sidecar vocabulary
        # stays consistent (unknown/missing kinds fall back to "fact").
        candidate_kind = normalize_memory_kind(cand.get("kind"))
        cand_id = f"cand_{uuid.uuid4().hex[:12]}"
        candidate_supersedes = list(cand.get("supersedes", []) or [])
        novelty = 0.0
        try:
            novelty = _compute_novelty_score(body, mem_store.list_active(filters=scope_filters))
        except Exception:
            pass

        plan = resolve_semantic_supersedes(
            candidate_text=body,
            candidate_kind=candidate_kind,
            user_msg=full_user,
            conflict_memory=None,
            conflict_similarity=0.0,
            scope_filters=scope_filters,
            explicit_supersedes=candidate_supersedes,
        )

        conflict = mem_store.check_conflict(body, exclude_ids=list(candidate_supersedes), filters=scope_filters)
        if conflict:
            existing_id, score = conflict
            logger.info("Reflection memory candidate conflicts with %s (%.2f), skipping", existing_id, score)
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="skipped",
                decision_reason=f"conflict with existing memory {existing_id} (score {score:.2f})",
                novelty_score=novelty,
                conflict_id=existing_id,
                assigned_zone=cand.get("zone", "episode"),
            ))
            continue

        try:
            supersedes_reason = cand.get("supersedes_reason", "")
            supersedes = candidate_supersedes if plan["action"] == "supersede" else []
            if not supersedes_reason and supersedes:
                supersedes_reason = plan["reason"]
            fm = _frontmatter_for_scope(
                source="reflection",
                confidence=cand.get("confidence", "medium"),
                tags=cand.get("tags", []),
                zone="episode",
                supersedes=supersedes,
                supersedes_reason=supersedes_reason,
                scope_filters=scope_filters,
            )
            if fm.supersedes:
                _validate_supersedes_targets(mem_store, fm.supersedes)
            path = mem_store.put(scope, fm, body)
            # Phase C: a merge carries no lineage edge but the resolver still
            # named targets whose facts are now superseded in truth-value.
            merge_targets = plan["target_ids"] if plan["action"] == "merge" else []
            _record_typed_fact_sidecar(
                mem_store,
                fm,
                body,
                relation="describes",
                kind=candidate_kind,
                target_memory_id=fm.id,
                episode_id=fm.id,
                source="reflection",
                confidence=0.9 if cand.get("confidence") == "high" else 0.6,
                superseded_memory_ids=list(fm.supersedes or []) + list(merge_targets) or None,
            )
            # Record merge / scope_split semantic edges (round-3 P1-2).
            if plan["action"] in ("merge", "scope_split") and plan.get("target_ids"):
                _record_semantic_relation_sidecar(
                    mem_store, fm,
                    action=plan["action"],
                    target_ids=plan["target_ids"],
                    reason=plan["reason"],
                    confidence=plan.get("confidence", 0.6),
                )
            _remember_current_session_memory_id(fm.id)
            accepted_memories.append({"id": fm.id, "body": body, "path": str(path), "kind": cand.get("kind")})
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="accepted" if not fm.supersedes else "superseded",
                decision_reason=plan["reason"] if not fm.supersedes else f"supersedes {fm.supersedes}",
                novelty_score=novelty,
                supersedes_ids=fm.supersedes or [],
                supersedes_reason=supersedes_reason,
                assigned_zone="episode",
            ))
        except Exception as e:
            logger.warning("Failed to store memory candidate: %s", e)
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="rejected",
                decision_reason=f"storage error: {e}",
                novelty_score=novelty,
                assigned_zone="episode",
            ))

    # Log skill candidates (require manual approval)
    skill_candidates = parsed.get("skill_candidates", [])
    for sk in skill_candidates:
        audit_entries.append(_build_audit_entry(
            candidate_id=f"skill_{uuid.uuid4().hex[:12]}",
            decision="pending",
            decision_reason="skill candidates require manual approval",
            assigned_zone="skill",
        ))

    # Log the reflection outcome with audit trail
    _append_reflect_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "full_llm",
        "summary": parsed.get("summary", ""),
        "skill_candidates": len(skill_candidates),
        "memory_candidates": len(parsed.get("memory_candidates", [])),
        "conflicts": len(parsed.get("conflicts", [])),
        "audit_entries": audit_entries,
        "raw": result.text,
    })

    if skill_candidates:
        logger.info("Reflection produced %d skill candidates (manual approval required)", len(skill_candidates))
        _save_pending_skill_candidates(skill_candidates)

    logger.info(
        "Full reflection complete: %d memories accepted, %d skills pending approval, %d conflicts noted",
        len(accepted_memories), len(skill_candidates), len(parsed.get("conflicts", [])),
    )

    return {
        "summary": parsed.get("summary", ""),
        "accepted_memories": accepted_memories,
        "skill_candidates": skill_candidates,
        "conflicts": parsed.get("conflicts", []),
    }


def _run_micro_reflection(
    ctx,
    user_msg: str,
    assistant_msg: str,
    scope_filters: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Run a micro-reflection. Uses embedding-based by default; falls back to LLM only in 'llm' mode."""
    mode = _reflection_mode()
    scope_filters = _scope_filters_from_context(ctx, scope_filters)

    if mode in ("embedding", "local", "hybrid"):
        return _run_embedding_micro_reflection(user_msg, assistant_msg, scope_filters=scope_filters)

    # LLM mode (expensive)
    if not hasattr(ctx, "llm"):
        return None

    instructions = (
        "You just observed ONE turn of conversation. "
        "Decide if anything is worth persisting as a memory or skill."
    )

    inputs = [
        {"type": "text", "text": f"[user] {user_msg}\n\n[assistant] {assistant_msg}"},
    ]

    try:
        result = ctx.llm.complete_structured(
            instructions=instructions,
            input=inputs,
            json_schema=_build_reflect_schema(),
            json_mode=True,
            system_prompt=_MICRO_REFLECT_SYSTEM,
            purpose="micro_reflection",
            max_tokens=2048,
            timeout=30,
        )
    except Exception as e:
        logger.debug("Micro-reflection LLM call failed: %s", e)
        return None

    parsed = result.parsed if result else None
    if not parsed:
        return None

    # Store at most 1 memory from micro-reflection (auto-accepted for micro)
    mem_store = _get_mem_store()
    accepted = None
    audit_entries: List[Dict[str, Any]] = []

    for cand in parsed.get("memory_candidates", [])[:1]:
        body = cand.get("fact", "")
        scope = cand.get("scope", "user")
        cand_id = f"cand_{uuid.uuid4().hex[:12]}"
        novelty = 0.0
        try:
            novelty = _compute_novelty_score(body, mem_store.list_active(filters=scope_filters))
        except Exception:
            pass

        candidate_supersedes = list(cand.get("supersedes", []) or [])
        # P2-1: normalize the LLM-provided kind to the canonical vocabulary.
        candidate_kind = normalize_memory_kind(cand.get("kind"))
        plan = resolve_semantic_supersedes(
            candidate_text=body,
            candidate_kind=candidate_kind,
            user_msg=user_msg,
            conflict_memory=None,
            conflict_similarity=0.0,
            scope_filters=scope_filters,
            explicit_supersedes=candidate_supersedes,
        )
        supersedes = candidate_supersedes if plan["action"] == "supersede" else []

        conflict = mem_store.check_conflict(body, exclude_ids=list(candidate_supersedes), filters=scope_filters)
        if conflict:
            existing_id, score = conflict
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="skipped",
                decision_reason=f"conflict with {existing_id} (score {score:.2f})",
                novelty_score=novelty,
                conflict_id=existing_id,
                assigned_zone=cand.get("zone", "episode"),
            ))
            continue

        try:
            fm = _frontmatter_for_scope(
                source="micro_reflection",
                confidence=cand.get("confidence", "low"),
                tags=cand.get("tags", []),
                zone=cand.get("zone", "episode"),
                supersedes=supersedes,
                supersedes_reason=cand.get("supersedes_reason", "") if not supersedes else plan["reason"],
                scope_filters=scope_filters,
            )
            if fm.supersedes:
                _validate_supersedes_targets(mem_store, fm.supersedes)
            path = mem_store.put(scope, fm, body)
            # Phase C: merge targets carry no lineage edge but should still
            # invalidate the superseded facts and record a relation edge.
            merge_targets = plan["target_ids"] if plan["action"] == "merge" else []
            _record_typed_fact_sidecar(
                mem_store,
                fm,
                body,
                relation="describes",
                kind=candidate_kind,
                target_memory_id=fm.id,
                episode_id=fm.id,
                source="micro_reflection",
                confidence=0.9 if cand.get("confidence") == "high" else 0.6,
                superseded_memory_ids=list(fm.supersedes or []) + list(merge_targets) or None,
            )
            if plan["action"] in ("merge", "scope_split") and plan.get("target_ids"):
                _record_semantic_relation_sidecar(
                    mem_store, fm,
                    action=plan["action"],
                    target_ids=plan["target_ids"],
                    reason=plan["reason"],
                    confidence=plan.get("confidence", 0.6),
                )
            _remember_current_session_memory_id(fm.id)
            accepted = {"id": fm.id, "body": body, "path": str(path), "kind": cand.get("kind")}
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="accepted" if not fm.supersedes else "superseded",
                decision_reason=plan["reason"] if not fm.supersedes else f"supersedes {fm.supersedes}",
                novelty_score=novelty,
                supersedes_ids=fm.supersedes or [],
                supersedes_reason=cand.get("supersedes_reason", "") if not fm.supersedes else plan["reason"],
                assigned_zone="episode",
            ))
        except Exception as e:
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="rejected",
                decision_reason=f"storage error: {e}",
                novelty_score=novelty,
                assigned_zone="episode",
            ))

    if accepted or audit_entries:
        _append_reflect_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "micro_llm",
            "summary": parsed.get("summary", ""),
            "accepted_memory": accepted,
            "audit_entries": audit_entries,
        })

    return parsed




# # Block 5: Skill approval
# Pending skill candidate approval system
# ---------------------------------------------------------------------------

PENDING_SKILLS_PATH = _plugin_data_dir() / "pending-skills.json"
_MAX_PENDING_SKILLS = 200  # P2-27: max pending items before archive


def _save_pending_skill_candidates(candidates: List[Dict[str, Any]]) -> None:
    """Save skill candidates to pending approval file."""
    try:
        with _pending_skills_lock:
            PENDING_SKILLS_PATH.parent.mkdir(parents=True, exist_ok=True)
            existing = []
            if PENDING_SKILLS_PATH.exists():
                with open(PENDING_SKILLS_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            # P2-27: archive old pending skills when too many accumulated
            if len(existing) > _MAX_PENDING_SKILLS:
                archive_count = len(existing)
                archive_path = PENDING_SKILLS_PATH.with_suffix(
                    f".{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
                )
                PENDING_SKILLS_PATH.rename(archive_path)
                existing = []
                logger.info("Pending skills archived to %s (%d items)", archive_path, archive_count)
            # Add timestamp and unique id to each candidate
            for cand in candidates:
                cand["_pending_id"] = f"pending_{uuid.uuid4().hex[:12]}"
                cand["_submitted_at"] = datetime.now(timezone.utc).isoformat()
                cand["_status"] = "pending"
            existing.extend(candidates)
            with open(PENDING_SKILLS_PATH, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to save pending skill candidates: %s", e)


def _load_pending_skill_candidates() -> List[Dict[str, Any]]:
    """Load all pending skill candidates."""
    try:
        if not PENDING_SKILLS_PATH.exists():
            return []
        with open(PENDING_SKILLS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _update_pending_skill_status(pending_id: str, status: str, reason: str = "") -> bool:
    """Update the status of a pending skill candidate."""
    try:
        candidates = _load_pending_skill_candidates()
        for cand in candidates:
            if cand.get("_pending_id") == pending_id:
                cand["_status"] = status
                cand["_resolved_at"] = datetime.now(timezone.utc).isoformat()
                cand["_resolve_reason"] = reason
                with open(PENDING_SKILLS_PATH, "w", encoding="utf-8") as f:
                    json.dump(candidates, f, ensure_ascii=False, indent=2)
                return True
        return False
    except Exception as e:
        logger.warning("Failed to update pending skill status: %s", e)
        return False


def _sanitize_filename(name: str) -> str:
    """Sanitize a skill name for filesystem use. Only allow safe characters."""
    safe = re.sub(r'[^a-zA-Z0-9_\-.]', '_', name)
    safe = safe.strip('.-')
    # M13: strip any residual path separators
    safe = safe.replace("/", "_").replace("\\", "_")
    if not safe:
        safe = "unnamed_skill"
    return safe


def _approve_skill(pending_id: str) -> Optional[Dict[str, Any]]:
    """Approve a pending skill candidate and write it to the skill store."""
    candidates = _load_pending_skill_candidates()
    for cand in candidates:
        if cand.get("_pending_id") == pending_id:
            if cand.get("_status") != "pending":
                return {"error": f"Skill already {cand['_status']}"}
            try:
                # Write skill to user skills directory
                skill_name = _sanitize_filename(cand["name"])
                skill_dir = _user_skills_dir() / skill_name
                skill_dir.mkdir(parents=True, exist_ok=True)

                fm_data = {
                    "name": skill_name,
                    "description": cand.get("description", ""),
                    "triggers": cand.get("triggers", []),
                    "version": "1.0.0",
                    "license": "MIT",
                }
                body = cand.get("body", "")
                skill_md = serialize_frontmatter(fm_data, body)
                (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

                _update_pending_skill_status(pending_id, "approved", "User approved via UI")
                return {
                    "success": True,
                    "name": skill_name,
                    "path": str(skill_dir),
                }
            except Exception as e:
                logger.warning("Failed to approve skill %s: %s", pending_id, e)
                return {"error": str(e)}
    return {"error": "Pending skill not found"}


def _reject_skill(pending_id: str, reason: str = "") -> bool:
    """Reject a pending skill candidate."""
    return _update_pending_skill_status(pending_id, "rejected", reason or "User rejected via UI")


def _format_pending_skills_for_display() -> str:
    """Format pending skills for TUI/gateway display."""
    candidates = [c for c in _load_pending_skill_candidates() if c.get("_status") == "pending"]
    if not candidates:
        return "No pending skill candidates."

    lines = [f"🔧 Pending Skill Candidates ({len(candidates)}):", ""]
    for i, cand in enumerate(candidates, 1):
        lines.append(f"{i}. {cand['name']}")
        lines.append(f"   Description: {cand.get('description', 'N/A')}")
        lines.append(f"   Triggers: {', '.join(cand.get('triggers', []))}")
        lines.append(f"   Confidence: {cand.get('confidence', 'medium')}")
        lines.append(f"   Rationale: {cand.get('rationale', 'N/A')}")
        lines.append(f"   Pending ID: {cand['_pending_id']}")
        lines.append("")
    lines.append("Use /approve-skill <pending_id> or /reject-skill <pending_id> to act on these.")
    return "\n".join(lines)



# ---------------------------------------------------------------------------
# Post-register reflection functions (extracted from __init__.py)
# ---------------------------------------------------------------------------

def _compute_novelty_score(new_text: str, existing_memories: List[LoadedMemory],
                           exclude_ids: Optional[Set[str]] = None) -> float:
    """Compute how novel a text is compared to existing memories (0-1, higher = more novel).

    Uses pre-computed memory embeddings from the store's embed_index when available
    for O(1) per-memory lookup instead of O(n) re-encoding.
    """
    if not existing_memories:
        return 1.0
    exclude_ids = exclude_ids or set()
    new_emb = _embed_single(new_text)
    if new_emb is None:
        filtered = [m for m in existing_memories if m.id() not in exclude_ids]
        return 1.0 - _tfidf_max_similarity(new_text, filtered)

    # Try to use store's embed_index for fast vector lookup
    store = _get_mem_store()
    max_sim = 0.0
    if store._embed_index is not None:
        vectors = store._embed_index.get("vectors", {})
        for m in existing_memories:
            if m.id() in exclude_ids:
                continue
            m_emb = vectors.get(m.id())
            if m_emb is not None:
                sim = _cosine_sim(new_emb, m_emb)
                max_sim = max(max_sim, sim)
            else:
                # Fallback: encode on demand
                m_emb = _embed_single(m.body)
                if m_emb is not None:
                    sim = _cosine_sim(new_emb, m_emb)
                    max_sim = max(max_sim, sim)
    else:
        # No embed index: encode each memory on demand
        for m in existing_memories:
            if m.id() in exclude_ids:
                continue
            m_emb = _embed_single(m.body)
            if m_emb is not None:
                sim = _cosine_sim(new_emb, m_emb)
                max_sim = max(max_sim, sim)

    # Scale: 0.9 similarity = 0.1 novelty, 0.0 similarity = 1.0 novelty
    novelty = max(0.0, 1.0 - max_sim)
    return novelty


def _find_conflicting_memory(new_text: str, existing: List[LoadedMemory], threshold: float = 0.75,
                             exclude_ids: Optional[Set[str]] = None) -> Optional[Tuple[LoadedMemory, float]]:
    """Find a semantically similar but potentially conflicting memory.

    Uses pre-computed memory embeddings from the store's embed_index when available.
    """
    exclude_ids = exclude_ids or set()
    new_emb = _embed_single(new_text)
    if new_emb is None:
        return None

    store = _get_mem_store()
    best: Optional[Tuple[LoadedMemory, float]] = None

    if store._embed_index is not None:
        vectors = store._embed_index.get("vectors", {})
        for m in existing:
            if m.id() in exclude_ids:
                continue
            m_emb = vectors.get(m.id())
            if m_emb is None:
                m_emb = _embed_single(m.body)
            if m_emb is not None:
                sim = _cosine_sim(new_emb, m_emb)
                if sim > threshold:
                    if best is None or sim > best[1]:
                        best = (m, sim)
    else:
        for m in existing:
            if m.id() in exclude_ids:
                continue
            m_emb = _embed_single(m.body)
            if m_emb is not None:
                sim = _cosine_sim(new_emb, m_emb)
                if sim > threshold:
                    if best is None or sim > best[1]:
                        best = (m, sim)
    return best


def _extract_facts_from_turn(user_msg: str, assistant_msg: str) -> List[Dict[str, Any]]:
    """Extract refined fact candidates using the shared refinement helper."""
    return extract_refined_memory_candidates(user_msg, assistant_msg)


# P2-2 (round-3): these helpers previously lived as local copies in this
# module AND in extraction.py/engine.py, with subtly different behaviour (the
# local ``_is_memorable_content`` ignored CJK weight, so Chinese short
# fragments were scored inconsistently between micro-reflection and the
# compaction fallback). They are now re-exported from extraction.py, the
# single CJK-aware source of truth, so every path agrees.
from .extraction import (  # noqa: E402,F401
    _is_noise_text,
    _is_memorable_content,
    _text_similarity,
)


def _infer_zone_from_scope(scope: str) -> str:
    """Map memory scope to default zone."""
    return "work" if scope == "project" else "general"


def _validate_supersedes_targets(mem_store: Any, supersedes: List[str]) -> None:
    """Validate every supersedes target before storage (H27)."""
    for sid in supersedes or []:
        if mem_store.get(sid) is None:
            raise ValueError(f"supersedes target not found: {sid}")
        cycle = _lineage_cycle_check(mem_store, sid)
        if cycle is not None:
            raise ValueError(f"supersedes would create a cycle: {' -> '.join(cycle)}")


def _run_embedding_reflection(
    messages: List[Dict[str, Any]],
    scope_filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a full reflection using local embeddings + rule engine (zero LLM cost).

    This replaces the expensive LLM-based reflection with:
    1. Semantic novelty detection via embeddings
    2. Heuristic fact extraction from conversation
    3. Conflict detection via embedding similarity
    4. Conservative candidate generation
    """
    scope_filters = _scope_filters_from_context(None, scope_filters)
    mem_store = _get_mem_store()
    skill_store = _get_skill_store()
    active_memories = mem_store.list_active(filters=scope_filters)
    current_session_ids = _get_current_session_memory_ids()
    all_skills = skill_store.list()

    # Build transcript
    transcript = _format_messages_for_reflection(messages)
    if not transcript.strip():
        return {"summary": "Empty transcript", "accepted_memories": [], "skill_candidates": [], "conflicts": []}

    # Compute overall novelty of this session vs existing memories
    session_novelty = _compute_novelty_score(transcript, active_memories, exclude_ids=current_session_ids)
    logger.debug("Session novelty score: %.3f", session_novelty)

    # Extract potential facts from each turn
    memory_candidates = []
    conflicts = []

    # Process the full transcript as one unit for efficiency
    # (per-turn processing is done in micro-reflection)
    user_msgs = []
    assistant_msgs = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = " ".join(texts)
        if role == "user":
            user_msgs.append(content)
        elif role == "assistant":
            assistant_msgs.append(content)

    full_user = " ".join(user_msgs)
    full_assistant = " ".join(assistant_msgs)

    # Extract facts
    facts = _extract_facts_from_turn(full_user, full_assistant)
    audit_entries: List[Dict[str, Any]] = []

    for fact in facts:
        text = fact["text"]
        cand_id = f"cand_{uuid.uuid4().hex[:12]}"
        # Check novelty
        novelty = _compute_novelty_score(text, active_memories, exclude_ids=current_session_ids)
        if novelty < 0.3:
            logger.debug("Fact too similar to existing memory (novelty %.3f), skipping: %s", novelty, text[:60])
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="skipped",
                decision_reason=f"novelty too low ({novelty:.3f} < 0.3)",
                novelty_score=novelty,
                assigned_zone=_infer_zone_from_scope("user"),
            ))
            continue

        # Check for conflicts
        conflict_mem = _find_conflicting_memory(text, active_memories, exclude_ids=current_session_ids)
        tags = _extract_keywords(text, top_k=3)
        candidate_kind = fact.get("kind", "fact")
        supersedes: List[str] = []
        plan: Optional[Dict[str, Any]] = None

        if conflict_mem:
            mem, sim = conflict_mem
            plan = resolve_semantic_supersedes(
                candidate_text=text,
                candidate_kind=candidate_kind,
                user_msg=full_user,
                conflict_memory=mem,
                conflict_similarity=sim,
                scope_filters=scope_filters,
            )
            if plan["action"] == "skip":
                logger.debug("Similar to existing memory %s (%.3f), skipping", mem.id(), sim)
                audit_entries.append(_build_audit_entry(
                    candidate_id=cand_id,
                    decision="skipped",
                    decision_reason=plan["reason"],
                    novelty_score=novelty,
                    conflict_id=mem.id(),
                    assigned_zone=_infer_zone_from_scope("user"),
                ))
                continue
            if plan["action"] == "supersede":
                supersedes = plan["target_ids"]
                conflicts.append({
                    "with": mem.id(),
                    "kind": "stale",
                    "explain": plan["reason"],
                    "options": ["keep_new", "keep_old"],
                })
            # merge / scope_split carry no supersedes edge; capture the targets
            # so the storage loop can record a semantic-relation sidecar edge.
        memory_candidates.append({
            "fact": text,
            "tags": tags,
            "scope": "user",
            "confidence": fact["confidence"],
            "rationale": fact["rationale"],
            "supersedes": supersedes,
            "kind": candidate_kind,
            "semantic_action": (plan or {}).get("action") if (plan or {}).get("action") in ("merge", "scope_split") else None,
            "semantic_target_ids": (plan or {}).get("target_ids") if (plan or {}).get("action") in ("merge", "scope_split") else [],
            "semantic_reason": (plan or {}).get("reason", "") if (plan or {}).get("action") in ("merge", "scope_split") else "",
        })
        audit_entries.append(_build_audit_entry(
            candidate_id=cand_id,
            decision="superseded" if supersedes else "accepted",
            decision_reason=plan["reason"] if plan else "novelty sufficient, no conflict",
            novelty_score=novelty,
            conflict_id=mem.id() if conflict_mem else "",
            supersedes_ids=supersedes,
            supersedes_reason=plan["reason"] if supersedes else "",
            assigned_zone=_infer_zone_from_scope("user"),
        ))

    summary_text = ""
    summary_tags: List[str] = []

    # Also check if the overall session contains novel concepts not captured by explicit facts
    if session_novelty > 0.5 and len(memory_candidates) == 0:
        # Generate a summary memory from the session
        summary_text = _generate_session_summary(transcript)
        if summary_text and len(summary_text) > 20:
            summary_tags = _extract_keywords(summary_text, top_k=3)
            memory_candidates.append({
                "fact": summary_text,
                "tags": summary_tags,
                "scope": "user",
                "confidence": "low",
                "rationale": "Session contained novel concepts not matching existing memories",
                "supersedes": [],
                "kind": "summary",
            })
            audit_entries.append(_build_audit_entry(
                candidate_id=f"cand_{uuid.uuid4().hex[:12]}",
                decision="pending_storage",
                decision_reason="session novelty high but no explicit facts extracted",
                novelty_score=session_novelty,
                assigned_zone=_infer_zone_from_scope("user"),
            ))

    # Skill detection: look for reusable procedures
    skill_candidates = []
    if _is_procedure(full_assistant) and len(full_assistant) > 200:
        # Check if similar skill already exists
        novel_skill = True
        emb_assistant = _embed_single(full_assistant)
        if emb_assistant is not None:
            for sk in all_skills:
                sk_emb = _embed_single(sk.body)
                if sk_emb is not None:
                    sim = _cosine_sim(emb_assistant, sk_emb)
                    if sim > 0.85:
                        novel_skill = False
                        break
        if novel_skill:
            name = _generate_skill_name(full_assistant)
            skill_candidates.append({
                "name": name,
                "description": f"Procedure extracted from session: {summary_text[:80] if summary_text else 'multi-step workflow'}",
                "triggers": summary_tags[:3] if summary_tags else ["procedure"],
                "body": f"## {name}\n\n{full_assistant[:800]}",
                "rationale": "Assistant provided a multi-step procedure that may be reusable",
                "confidence": "low",
            })
            audit_entries.append(_build_audit_entry(
                candidate_id=f"skill_{uuid.uuid4().hex[:12]}",
                decision="pending",
                decision_reason="skill candidate detected, manual approval required",
                assigned_zone="skill",
            ))

    # Store memory candidates
    accepted_memories = []
    for cand in memory_candidates:
        cand_id = f"cand_{uuid.uuid4().hex[:12]}"
        body = cand["fact"]
        scope = cand.get("scope", "user")
        zone = _infer_zone_from_scope(scope)
        try:
            fm = _frontmatter_for_scope(
                source="reflection",
                confidence=cand.get("confidence", "medium"),
                tags=cand.get("tags", []),
                zone=zone,
                supersedes=cand.get("supersedes", []),
                scope_filters=scope_filters,
            )
            _validate_supersedes_targets(mem_store, fm.supersedes)
            exclude_ids = list(current_session_ids | set(fm.supersedes or []))
            # Final conflict check
            conflict = mem_store.check_conflict(body, exclude_ids=exclude_ids, filters=scope_filters)
            if conflict:
                existing_id, score = conflict
                logger.info("Embedding reflection: memory conflicts with %s (%.2f), skipping", existing_id, score)
                audit_entries.append(_build_audit_entry(
                    candidate_id=cand_id,
                    decision="skipped",
                    decision_reason=f"late conflict with {existing_id} (score {score:.2f})",
                    conflict_id=existing_id,
                    supersedes_ids=fm.supersedes or [],
                    assigned_zone=zone,
                ))
                continue
            path = mem_store.put(scope, fm, body)
            accepted_memories.append({"id": fm.id, "body": body, "path": str(path), "kind": cand.get("kind")})
            _remember_current_session_memory_id(fm.id)
            # Phase C: record merge / scope_split semantic edges so the
            # resolver's decision is not silently dropped (round-3 P1-2).
            semantic_action = cand.get("semantic_action")
            semantic_targets = cand.get("semantic_target_ids") or []
            if semantic_action in ("merge", "scope_split") and semantic_targets:
                _record_semantic_relation_sidecar(
                    mem_store, fm,
                    action=semantic_action,
                    target_ids=semantic_targets,
                    reason=cand.get("semantic_reason", ""),
                    confidence=0.6,
                )
                # A merge is a semantic update: the merge target's own facts
                # are now superseded in truth-value by this memory.
                if semantic_action == "merge":
                    _record_typed_fact_sidecar(
                        mem_store, fm, body,
                        relation="describes", kind=cand.get("kind", "fact"),
                        target_memory_id=fm.id, episode_id=fm.id,
                        source="reflection", confidence=0.6,
                        superseded_memory_ids=semantic_targets,
                    )
            # Update the matching pending_storage audit entry if present
            updated = False
            for ae in audit_entries:
                if ae.get("decision") == "pending_storage":
                    ae["decision"] = "accepted" if not fm.supersedes else "superseded"
                    ae["decision_reason"] = "stored successfully" if not fm.supersedes else f"stored and superseded {fm.supersedes}"
                    ae["supersedes_ids"] = fm.supersedes or []
                    updated = True
                    break
            if not updated:
                audit_entries.append(_build_audit_entry(
                    candidate_id=cand_id,
                    decision="accepted" if not fm.supersedes else "superseded",
                    decision_reason="stored successfully",
                    supersedes_ids=fm.supersedes or [],
                    assigned_zone=zone,
                ))
        except Exception as e:
            logger.warning("Failed to store embedding reflection memory: %s", e)
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="rejected",
                decision_reason=f"storage error: {e}",
                assigned_zone="episode",
            ))

    # Save skill candidates for approval
    if skill_candidates:
        logger.info("Embedding reflection produced %d skill candidates (manual approval required)", len(skill_candidates))
        _save_pending_skill_candidates(skill_candidates)

    # Build summary
    summary = f"Session novelty: {session_novelty:.2f}. Extracted {len(facts)} facts, accepted {len(accepted_memories)} memories, {len(skill_candidates)} skills pending, {len(conflicts)} conflicts."

    _append_reflect_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "embedding",
        "summary": summary,
        "skill_candidates": len(skill_candidates),
        "memory_candidates": len(memory_candidates),
        "accepted_memories": len(accepted_memories),
        "conflicts": len(conflicts),
        "novelty": session_novelty,
        "audit_entries": audit_entries,
    })

    logger.info(
        "Embedding reflection complete: %d memories accepted, %d skills pending, %d conflicts",
        len(accepted_memories), len(skill_candidates), len(conflicts),
    )

    return {
        "summary": summary,
        "accepted_memories": accepted_memories,
        "skill_candidates": skill_candidates,
        "conflicts": conflicts,
    }


def _run_raw_chunk_reflection(
    messages: List[Dict[str, Any]],
    scope_filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Save raw conversation chunks as episode memories. Zero LLM calls.

    Academic basis: [Retrieval Bottleneck] arXiv:2603.02473, Sec.3.1.
    Basic RAG (zero LLM calls) with hybrid retrieval reaches 81.1%,
    outperforming Mem0-style Extracted Facts (77.3%).
    """
    scope_filters = _scope_filters_from_context(None, scope_filters)
    mem_store = _get_mem_store()
    accepted = []
    audit_entries: List[Dict[str, Any]] = []

    # Group messages into 3-turn windows
    chunk_size = 3
    for i in range(0, len(messages), chunk_size):
        chunk = messages[i:i + chunk_size]
        # Format chunk as readable text
        lines = []
        for msg in chunk:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(texts)
            if content and isinstance(content, str):
                lines.append(f"[{role}] {content.strip()}")
        body = "\n".join(lines)

        if len(body) < 20:
            continue

        # Direct write without LLM analysis
        fm = _frontmatter_for_scope(
            source="raw_chunk",
            confidence="low",
            tags=["episode", "raw_chunk"],
            zone="episode",
            scope_filters=scope_filters,
        )
        try:
            path = mem_store.put("user", fm, body)
            _record_typed_fact_sidecar(
                mem_store,
                fm,
                body,
                relation="captures",
                kind="raw_chunk",
                target_memory_id=fm.id,
                episode_id=fm.id,
                source="raw_chunk",
                confidence=0.3,
            )
            accepted.append({"id": fm.id, "body_preview": body[:120], "kind": "raw_chunk"})
            _remember_current_session_memory_id(fm.id)
            audit_entries.append(_build_audit_entry(
                candidate_id=f"chunk_{fm.id}",
                decision="accepted",
                decision_reason="raw chunk stored (zero LLM)",
                assigned_zone="episode",
            ))
        except Exception as e:
            logger.debug("Raw chunk storage failed: %s", e)
            audit_entries.append(_build_audit_entry(
                candidate_id=f"chunk_{uuid.uuid4().hex[:8]}",
                decision="rejected",
                decision_reason=f"storage error: {e}",
                assigned_zone="episode",
            ))

    summary = f"Raw chunk reflection: {len(accepted)} chunks stored from {len(messages)} messages."
    _append_reflect_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "raw_chunk",
        "summary": summary,
        "accepted_memories": len(accepted),
        "audit_entries": audit_entries,
    })

    return {
        "mode": "raw_chunk",
        "summary": summary,
        "accepted_memories": accepted,
        "chunks_created": len(accepted),
    }


def _run_embedding_micro_reflection(
    user_msg: str,
    assistant_msg: str,
    scope_filters: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Run a micro-reflection using local embeddings (zero LLM cost).

    Much faster than LLM-based micro-reflection (~50ms vs ~2000ms).
    """
    scope_filters = _scope_filters_from_context(None, scope_filters)
    mem_store = _get_mem_store()
    active_memories = mem_store.list_active(filters=scope_filters)

    combined = f"{user_msg} {assistant_msg}"
    cand_id = f"cand_{uuid.uuid4().hex[:12]}"
    has_explicit_intent = _is_explicit_memory_intent(user_msg)

    # Extract facts first - if user has explicit intent, always process
    facts = _extract_facts_from_turn(user_msg, assistant_msg)
    # Quick novelty check - but skip if user explicitly wants to remember
    novelty = _compute_novelty_score(combined, active_memories)
    if not has_explicit_intent and novelty < 0.25:
        logger.debug("Micro-reflection: turn too similar to existing memories (%.3f), skipping", novelty)
        _append_reflect_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "embedding_micro",
            "summary": "Micro-reflection skipped: turn too similar",
            "audit_entries": [_build_audit_entry(
                candidate_id=cand_id,
                decision="skipped",
                decision_reason=f"novelty too low ({novelty:.3f} < 0.25) without explicit intent",
                novelty_score=novelty,
                assigned_zone="episode",
            )],
        })
        return None

    if not facts:
        # If no heuristic facts were extracted, do NOT create a generic memory.
        # This prevents storing tool outputs, code blocks, and other non-memorable content.
        _append_reflect_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "embedding_micro",
            "summary": "Micro-reflection skipped: no facts detected",
            "audit_entries": [_build_audit_entry(
                candidate_id=cand_id,
                decision="skipped",
                decision_reason="no heuristic facts detected",
                novelty_score=novelty,
                assigned_zone="episode",
            )],
        })
        return None

    # Only take the highest-confidence fact
    facts.sort(key=lambda f: 0 if f["confidence"] == "high" else (1 if f["confidence"] == "medium" else 2))
    best = facts[0]

    # Check conflict
    conflict_mem = _find_conflicting_memory(best["text"], active_memories)
    tags = _extract_keywords(best["text"], top_k=3)

    supersedes = []
    plan: Optional[Dict[str, Any]] = None
    if conflict_mem:
        mem, sim = conflict_mem
        plan = resolve_semantic_supersedes(
            candidate_text=best["text"],
            candidate_kind=best.get("kind", "fact"),
            user_msg=user_msg,
            conflict_memory=mem,
            conflict_similarity=sim,
            scope_filters=scope_filters,
        )
        if plan["action"] == "skip":
            logger.debug("Micro-reflection: similar to %s (%.3f), skipping", mem.id(), sim)
            _append_reflect_log({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": "embedding_micro",
                "summary": f"Micro-reflection skipped: similar to {mem.id()}",
                "audit_entries": [_build_audit_entry(
                    candidate_id=cand_id,
                    decision="skipped",
                    decision_reason=f"similar to {mem.id()} (sim {sim:.2f}) without correction or explicit intent",
                    novelty_score=novelty,
                    conflict_id=mem.id(),
                    assigned_zone="episode",
                )],
            })
            return None
        if plan["action"] == "supersede":
            supersedes = plan["target_ids"]

    try:
        fm = _frontmatter_for_scope(
            source="micro_reflection",
            confidence=best["confidence"],
            tags=tags,
            zone="episode",
            supersedes=supersedes,
            scope_filters=scope_filters,
        )
        _validate_supersedes_targets(mem_store, fm.supersedes)
        path = mem_store.put("user", fm, best["text"])
        # Phase C: a merge is a semantic update, so the merge target's facts
        # are superseded in truth-value even though no lineage edge is written.
        semantic_action = (plan or {}).get("action")
        semantic_targets = (plan or {}).get("target_ids") or []
        merge_supersede_ids = list(fm.supersedes or [])
        if semantic_action == "merge" and semantic_targets:
            merge_supersede_ids.extend(semantic_targets)
        _record_typed_fact_sidecar(
            mem_store,
            fm,
            best["text"],
            relation="describes",
            kind=best.get("kind", "fact"),
            target_memory_id=fm.id,
            episode_id=fm.id,
            source="micro_reflection",
            confidence=0.8 if best.get("confidence") == "high" else 0.6,
            superseded_memory_ids=merge_supersede_ids or None,
        )
        # Record the merge / scope_split relation edge (round-3 P1-2).
        if semantic_action in ("merge", "scope_split") and semantic_targets:
            _record_semantic_relation_sidecar(
                mem_store, fm,
                action=semantic_action,
                target_ids=semantic_targets,
                reason=(plan or {}).get("reason", ""),
                confidence=0.6,
            )
        _remember_current_session_memory_id(fm.id)
        accepted = {"id": fm.id, "body": best["text"], "path": str(path), "kind": best.get("kind")}

        _append_reflect_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "embedding_micro",
            "summary": f"Micro-reflection accepted: {best['text'][:60]}",
            "accepted_memory": accepted,
            "novelty": novelty,
            "audit_entries": [_build_audit_entry(
                candidate_id=cand_id,
                decision="accepted" if not supersedes else "superseded",
                decision_reason="micro-reflection auto-accepted" if not supersedes else plan["reason"],
                novelty_score=novelty,
                supersedes_ids=supersedes,
                supersedes_reason=plan["reason"] if supersedes else "",
                assigned_zone="episode",
            )],
        })

        return {
            "summary": f"Detected {best['source']}: {best['text'][:60]}",
            "memory_candidates": [{"fact": best["text"], "tags": tags, "scope": "user", "confidence": best["confidence"]}],
            "skill_candidates": [],
            "conflicts": [],
        }
    except Exception as e:
        logger.debug("Micro-reflection storage failed: %s", e)
        _append_reflect_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "embedding_micro",
            "summary": "Micro-reflection storage failed",
            "audit_entries": [_build_audit_entry(
                candidate_id=cand_id,
                decision="rejected",
                decision_reason=f"storage error: {e}",
                novelty_score=novelty,
                assigned_zone="episode",
            )],
        })
        return None


def _generate_session_summary(transcript: str) -> str:
    """Generate a brief summary of the session from the transcript.

    Uses simple heuristics (first/last user messages) rather than LLM.
    """
    lines = [l for l in transcript.split("\n") if l.strip() and not l.startswith("[")]
    if not lines:
        return ""
    # Take the first substantial line
    for line in lines:
        clean = line.strip()
        if len(clean) > 20:
            return clean[:200]
    return lines[0][:200] if lines else ""


def _generate_skill_name(text: str) -> str:
    """Generate a kebab-case skill name from text heuristics."""
    keywords = _extract_keywords(text, top_k=3)
    if keywords:
        return "-".join(keywords[:3])
    # Fallback: use first few words
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if words:
        return "-".join(words[:3])
    return "extracted-procedure"


# ---------------------------------------------------------------------------
# Episode zone compaction (Phase 3 — v1.1)
# ---------------------------------------------------------------------------

_COMPACT_THRESHOLD = 20


def _split_compaction_fragments(text: str) -> List[str]:
    """Split a compaction candidate into sentence-like fragments."""
    return [part.strip() for part in re.split(r"[。！？.!?\n]+", text or "") if part.strip()]


def _compaction_fragment_score(text: str) -> float:
    """Score how representative a fragment is for a compacted summary."""
    fragment = (text or "").strip()
    if not fragment or not _is_memorable_content(fragment) or _is_noise_text(fragment):
        return -1.0

    score = 0.0
    lowered = fragment.lower()

    # Prefer concise, conclusion-like fragments over long transcript chunks.
    length = len(fragment)
    if 40 <= length <= 220:
        score += 3.0
    elif length < 40:
        score += 1.0
    elif length <= 320:
        score += 1.5
    else:
        score -= 1.5

    # Reuse the refined extraction layer as the primary signal source.
    candidates = _extract_facts_from_turn(fragment, "")
    if candidates:
        best = candidates[0]
        kind = best.get("kind", "")
        priority = int(best.get("priority", 9) or 9)
        score += max(0.0, 7.0 - float(priority))
        if best.get("text", "").strip() != fragment:
            score += 0.5
        if kind in {"intent", "decision", "preference", "policy", "correction", "todo"}:
            score += 2.0
    else:
        if any(marker in lowered for marker in ("decided", "decision", "prefer", "prefer", "use", "must", "should", "always", "never")):
            score += 2.0
        if any(marker in fragment for marker in ("决定", "约定", "采用", "喜欢", "不喜欢", "总是", "以后", "必须")):
            score += 2.0

    # Penalize commentary that describes the absence of a conclusion instead of the conclusion itself.
    if any(marker in lowered for marker in (
        "never states",
        "doesn't state",
        "didn't state",
        "not state",
        "no decision",
        "process chatter",
        "setup details",
        "scaffolding",
    )):
        score -= 3.0

    # Reward short, information-dense fragments and lightly penalize noisy scaffolding.
    keywords = _extract_keywords(fragment, top_k=3)
    score += min(len(keywords), 3) * 0.3
    if len(set(fragment[:80])) < 10:
        score -= 1.0

    return score


def _build_compaction_fallback_summary(bodies: List[str]) -> str:
    """Pick the best compacted fallback summary from a set of raw bodies."""
    fragment_pool: List[Tuple[str, float]] = []
    for body in bodies:
        if not body:
            continue
        stripped = body.strip()
        if not stripped or not _is_memorable_content(stripped) or _is_noise_text(stripped):
            continue

        candidates = _extract_facts_from_turn(stripped, "")
        fragments: List[str] = []
        if candidates:
            fragments.extend(candidate["text"].strip() for candidate in candidates[:2] if candidate.get("text"))
        fragments.extend(_split_compaction_fragments(stripped)[:3])
        if not fragments:
            fragments.append(stripped)

        for fragment in fragments:
            fragment = fragment.strip()
            if not fragment:
                continue
            fragment_pool.append((fragment, _compaction_fragment_score(fragment)))

    if not fragment_pool:
        return ""

    fragment_pool.sort(key=lambda item: (item[1], len(item[0])), reverse=True)

    selected: List[str] = []
    for fragment, score in fragment_pool:
        if score < 0:
            continue
        if any(_text_similarity(fragment, seen) > 0.8 for seen in selected):
            continue
        selected.append(fragment)
        if len(selected) >= 2:
            break

    if not selected:
        selected = [fragment_pool[0][0]]

    summary = " ".join(re.sub(r"\s+", " ", part).strip() for part in selected if part.strip())
    return summary[:500]


def _compaction_token_count(texts: List[str]) -> int:
    """Estimate total token count across a set of texts."""
    total = 0
    for text in texts:
        if not text:
            continue
        try:
            total += max(0, int(_estimate_tokens(text)))
        except Exception:
            total += len((text or "").split())
    return total


def _compaction_summary_quality(
    summary: str,
    bodies: List[str],
    fallback_summary: str = "",
) -> Dict[str, Any]:
    """Evaluate whether a compaction summary is better than the fallback."""
    cleaned = (summary or "").strip()
    fallback_clean = (fallback_summary or "").strip()
    source_tokens = _compaction_token_count(bodies)
    summary_tokens = _compaction_token_count([cleaned])
    fallback_tokens = _compaction_token_count([fallback_clean]) if fallback_clean else 0
    summary_score = _compaction_fragment_score(cleaned)
    fallback_score = _compaction_fragment_score(fallback_clean) if fallback_clean else -1.0
    compression_ratio = round(summary_tokens / source_tokens, 4) if source_tokens else 0.0
    reasons: List[str] = []

    if not cleaned:
        reasons.append("empty")
    if source_tokens and summary_tokens > max(12, int(source_tokens * 0.9)):
        reasons.append("too_long")
    if summary_score < 0:
        reasons.append("low_signal")
    if fallback_clean and summary_score + 0.25 < fallback_score:
        reasons.append("worse_than_fallback")
    if any(marker in cleaned.lower() for marker in (
        "never states",
        "doesn't state",
        "didn't state",
        "no decision",
        "process chatter",
        "setup details",
        "scaffolding",
    )):
        reasons.append("commentary_noise")

    passed = not reasons
    return {
        "passed": passed,
        "reasons": reasons,
        "source_tokens": source_tokens,
        "summary_tokens": summary_tokens,
        "fallback_tokens": fallback_tokens,
        "compression_ratio": compression_ratio,
        "summary_score": round(summary_score, 4),
        "fallback_score": round(fallback_score, 4) if fallback_clean else None,
    }


def _compact_episode_zone(mem_store, ctx=None, filters: Optional[Dict[str, Any]] = None) -> dict:
    """Compress raw episode entries into daily summaries.

    Triggers when the count of non-compacted episode entries reaches
    ``_COMPACT_THRESHOLD``.  Clusters entries by date, creates a
    summary per cluster, and marks originals as superseded.

    Parameters
    ----------
    mem_store : MemoryStore
        Plugin memory store instance.
    ctx : optional
        Hermes plugin context (needed for LLM-based summarization).

    Returns
    -------
    dict with keys ``compacted``, ``summaries``, ``skipped``.
    """
    try:
        threshold = plugin_config().get("compaction", {}).get("threshold", _COMPACT_THRESHOLD)
    except Exception:
        threshold = _COMPACT_THRESHOLD

    try:
        llm_summary_enabled = plugin_config().get("compaction", {}).get("llm_summary", True)
    except Exception:
        llm_summary_enabled = True

    scope_filters = _scope_filters_from_context(ctx, filters)

    # Get episode entries for the current scope
    all_episode = mem_store.list_by_zone("episode", filters=scope_filters)

    # Filter: only non-compacted entries
    raw_mems = [
        m for m in all_episode
        if "compacted" not in (m.frontmatter.tags or [])
    ]

    if len(raw_mems) < threshold:
        return {
            "compacted": 0,
            "skipped": f"below threshold ({len(raw_mems)} < {threshold})",
            "summaries": [],
        }

    # Cluster by day
    from collections import defaultdict
    clusters: Dict[str, List[Any]] = defaultdict(list)
    for m in raw_mems:
        created = m.frontmatter.created
        day = created[:10] if created else "unknown"
        clusters[day].append(m)

    summaries = []
    total_raw_consumed = 0
    total_source_tokens = 0
    total_summary_tokens = 0

    for day, mems in sorted(clusters.items()):
        if len(mems) < 2:
            continue  # Skip single-entry days

        bodies = [m.body.strip() for m in mems]
        fallback_summary = _build_compaction_fallback_summary(bodies)
        if not fallback_summary:
            fallback_summary = max(bodies, key=len)
        if len(fallback_summary) > 500:
            fallback_summary = fallback_summary[:497] + "..."

        # Build summary: LLM if available, otherwise the scored fallback.
        summary_mode = "fallback"
        quality = None
        if ctx is not None and llm_summary_enabled and hasattr(ctx, "llm"):
            llm_summary = _llm_summarize_cluster(day, bodies, ctx)
            if llm_summary:
                quality = _compaction_summary_quality(llm_summary, bodies, fallback_summary)
                if quality["passed"]:
                    summary = llm_summary
                    summary_mode = "llm"
                else:
                    summary = fallback_summary
                    summary_mode = "llm_fallback"
            else:
                summary = fallback_summary
        else:
            summary = fallback_summary

        source_tokens = _compaction_token_count(bodies)
        summary_tokens = _compaction_token_count([summary])
        compression_ratio = round(summary_tokens / source_tokens, 4) if source_tokens else 0.0
        total_source_tokens += source_tokens
        total_summary_tokens += summary_tokens

        fm = _frontmatter_for_scope(
            source="system",
            confidence="medium",
            tags=["compacted", "auto-summary"],
            zone="episode",
            supersedes=[m.id() for m in mems],
            supersedes_reason=f"Compacted {len(mems)} entries from {day}",
            scope_filters=scope_filters,
        )

        try:
            mem_store.put("user", fm, summary)
            _record_typed_fact_sidecar(
                mem_store,
                fm,
                summary,
                relation="summarizes",
                kind="compaction_summary",
                target_memory_id=fm.id,
                episode_id=day,
                source="compaction",
                confidence=0.6,
                superseded_memory_ids=[m.id() for m in mems],
            )
            summaries.append({
                "day": day,
                "compacted": len(mems),
                "summary": summary,
                "new_id": fm.id,
                "summary_mode": summary_mode,
                "source_tokens": source_tokens,
                "summary_tokens": summary_tokens,
                "compression_ratio": compression_ratio,
                "quality_gate": None if quality is None else quality["passed"],
                "quality_reasons": [] if quality is None else quality["reasons"],
            })
            total_raw_consumed += len(mems)
        except Exception as e:
            logger.debug("Compaction put failed for %s: %s", day, e)

    return {
        "compacted": len(summaries),
        "summaries": summaries,
        "total_raw_consumed": total_raw_consumed,
        "total_source_tokens": total_source_tokens,
        "total_summary_tokens": total_summary_tokens,
        "average_compression_ratio": round(total_summary_tokens / total_source_tokens, 4) if total_source_tokens else 0.0,
    }


def _llm_summarize_cluster(day: str, bodies: List[str], ctx) -> Optional[str]:
    """Generate a brief summary of a day's episode entries using the LLM.

    Returns ``None`` when the LLM is unavailable or fails so the caller can
    apply the scored fallback and quality gate consistently.
    """
    prompt = (
        f"Below are {len(bodies)} raw memory entries from {day}. "
        "Summarize them into 1-2 concise sentences capturing the key information "
        "in a neutral, factual tone:\n\n"
        + "\n---\n".join(f"[{i+1}] {b[:300]}" for i, b in enumerate(bodies))
    )
    try:
        if hasattr(ctx, "llm") and callable(ctx.llm):
            response = ctx.llm(prompt)
            if response and isinstance(response, str) and response.strip():
                text = response.strip()
                return text[:500]
    except Exception as e:
        logger.debug("LLM summary failed for %s: %s", day, e)

    return None
