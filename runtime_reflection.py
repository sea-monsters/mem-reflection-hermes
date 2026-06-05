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

from .store import (
    LoadedMemory, MemoryFrontmatter,
    hermes_home as _hermes_home, plugin_data_dir as _plugin_data_dir,
    user_skills_dir as _user_skills_dir,
    micro_reflection_enabled, profile_mode_enabled, plugin_config,
    parse_frontmatter, serialize_frontmatter,
    _tokenise, _lineage_cycle_check,
)
try:
    from .search import (
        _embed_single, _cosine_sim, _extract_keywords,
        _is_explicit_memory_intent, _is_correction, _is_procedure,
        _classify_intent,
    )
except ImportError:
    import importlib.util as _i_util
    from pathlib import Path as _Path
    _search_path = _Path(__file__).resolve().parent / "search.py"
    _spec = _i_util.spec_from_file_location(
        "mem_reflection_hermes.search", str(_search_path))
    _search_mod = _i_util.module_from_spec(_spec)
    _spec.loader.exec_module(_search_mod)
    _embed_single = _search_mod._embed_single
    _cosine_sim = _search_mod._cosine_sim
    _extract_keywords = _search_mod._extract_keywords
    _is_explicit_memory_intent = _search_mod._is_explicit_memory_intent
    _is_correction = _search_mod._is_correction
    _is_procedure = _search_mod._is_procedure
    _classify_intent = _search_mod._classify_intent

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
when the fact is specific to the current repo / codebase.

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
- Confidence should be "low" or "medium" — never "high" for micro-reflection.
- If the conversation reveals that an existing memory is WRONG or OUTDATED, produce a memory_candidates entry with the corrected fact and set `supersedes` to the old memory's id, plus a conflicts entry with kind "stale".

Reply with EXACTLY ONE JSON object:
{
  "summary": "<one sentence>",
  "skill_candidates": [],
  "memory_candidates": [{"fact": "<short statement>", "tags": [], "scope": "user", "confidence": "low|medium", "rationale": "<why>", "supersedes": ["mem_xxx"]}],
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
        pass

    repaired_full = _close_open_containers(s)
    if repaired_full is not None:
        tail_char = s.rstrip()[-1]
        if tail_char not in "[{:,":
            try:
                json.loads(repaired_full)
                return repaired_full
            except Exception:
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


def _format_inventory() -> str:
    """Format current memory and skill inventory for reflection context."""
    mem_store = _get_mem_store()
    skill_store = _get_skill_store()
    lines = ["=== Current Memory Inventory ==="]
    for m in mem_store.list_active():
        lines.append(f"- [{m.id()}] {m.body[:120]} (tags: {m.frontmatter.tags}, confidence: {m.frontmatter.confidence})")
    lines.append("")
    lines.append("=== Current Skill Inventory ===")
    for s in skill_store.list():
        lines.append(f"- {s.frontmatter.name}: {s.frontmatter.description} (triggers: {s.frontmatter.triggers})")
    return "\n".join(lines)


def _run_full_reflection(ctx, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run a full reflection. Default is raw_chunk (zero LLM cost);
    falls back to embedding/LLM if configured."""
    mode = _reflection_mode()

    # W2: raw_chunk mode — zero LLM calls, store raw conversation chunks
    if mode == "raw_chunk":
        return _run_raw_chunk_reflection(messages)

    # embedding-based (local, zero cost)
    if mode in ("embedding", "local"):
        return _run_embedding_reflection(messages)

    # Hybrid: try embedding first, if no candidates found, try LLM
    if mode == "hybrid":
        emb_result = _run_embedding_reflection(messages)
        if emb_result.get("accepted_memories") or emb_result.get("skill_candidates"):
            return emb_result
        logger.info("Hybrid mode: embedding found no candidates, trying LLM fallback")
        # Fall through to LLM

    # LLM mode (expensive, kept for compatibility)
    if not hasattr(ctx, "llm"):
        logger.warning("No ctx.llm available for full reflection")
        return {"error": "No LLM available"}

    transcript = _format_messages_for_reflection(messages)
    inventory = _format_inventory()

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
        cand_id = f"cand_{uuid.uuid4().hex[:12]}"
        novelty = 0.0
        try:
            novelty = _compute_novelty_score(body, mem_store.list_active())
        except Exception:
            pass

        supersedes = cand.get("supersedes", [])
        conflict = mem_store.check_conflict(body, exclude_ids=list(supersedes))
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
            fm = MemoryFrontmatter.new(
                source="reflection",
                confidence=cand.get("confidence", "medium"),
                tags=cand.get("tags", []),
                zone="episode",
            )
            fm.supersedes = supersedes
            _validate_supersedes_targets(mem_store, fm.supersedes)
            supersedes_reason = cand.get("supersedes_reason", "")
            if not supersedes_reason and fm.supersedes:
                supersedes_reason = "LLM suggested replacement"
            fm.supersedes_reason = supersedes_reason
            path = mem_store.put(scope, fm, body)
            _remember_current_session_memory_id(fm.id)
            accepted_memories.append({"id": fm.id, "body": body, "path": str(path)})
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="accepted" if not fm.supersedes else "superseded",
                decision_reason="novelty sufficient, no conflict" if not fm.supersedes else f"supersedes {fm.supersedes}",
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


def _run_micro_reflection(ctx, user_msg: str, assistant_msg: str) -> Optional[Dict[str, Any]]:
    """Run a micro-reflection. Uses embedding-based by default; falls back to LLM only in 'llm' mode."""
    mode = _reflection_mode()

    if mode in ("embedding", "local", "hybrid"):
        return _run_embedding_micro_reflection(user_msg, assistant_msg)

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
            novelty = _compute_novelty_score(body, mem_store.list_active())
        except Exception:
            pass

        supersedes = cand.get("supersedes", [])
        conflict = mem_store.check_conflict(body, exclude_ids=list(supersedes))
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
            fm = MemoryFrontmatter.new(
                source="micro_reflection",
                confidence=cand.get("confidence", "low"),
                tags=cand.get("tags", []),
            )
            fm.supersedes = supersedes
            _validate_supersedes_targets(mem_store, fm.supersedes)
            path = mem_store.put(scope, fm, body)
            _remember_current_session_memory_id(fm.id)
            accepted = {"id": fm.id, "body": body, "path": str(path)}
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="accepted" if not fm.supersedes else "superseded",
                decision_reason="micro-reflection auto-accepted",
                novelty_score=novelty,
                supersedes_ids=fm.supersedes or [],
                supersedes_reason=cand.get("supersedes_reason", "LLM suggested replacement"),
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
    """Extract potential fact statements from a conversation turn using heuristics."""
    facts = []
    combined = f"{user_msg} {assistant_msg}"

    # Heuristic 1: Explicit memory intent
    if _is_explicit_memory_intent(user_msg):
        # Extract the sentence containing the intent marker
        sentences = re.split(r'[。！？.!?\n]+', user_msg)
        for s in sentences:
            if _is_explicit_memory_intent(s):
                s = s.strip()
                if len(s) > 10 and _is_memorable_content(s):
                    facts.append({
                        "text": s,
                        "confidence": "high",
                        "rationale": "User explicitly requested to remember",
                        "source": "explicit_intent",
                    })

    # Heuristic 2: Corrections
    if _is_correction(user_msg):
        sentences = re.split(r'[。！？.!?\n]+', user_msg)
        for s in sentences:
            if _is_correction(s) and len(s) > 10 and _is_memorable_content(s):
                facts.append({
                    "text": s.strip(),
                    "confidence": "medium",
                    "rationale": "User corrected a previous statement",
                    "source": "correction",
                })

    # Heuristic 3: Preference statements
    # HIGH-9: use restrictive char classes instead of `.` to avoid capturing
    # trailing punctuation / half-sentences. Stop at sentence boundaries.
    _NOT_SENTENCE_END = r"[^\n。！？.!?]"
    pref_patterns = [
        (r"(?:我|i)\s+(?:喜欢|prefer|like|want|想|要)\s+(" + _NOT_SENTENCE_END + r"{5,80})", "preference"),
        (r"(?:我|i)\s+(?:不喜欢|hate|dislike|不想)\s+(" + _NOT_SENTENCE_END + r"{5,80})", "preference"),
        (r"(?:我|i)\s+(?:总是|always|usually|never)\s+(" + _NOT_SENTENCE_END + r"{5,80})", "preference"),
        (r"(?:用|use)\s+(" + _NOT_SENTENCE_END + r"{3,40})\s+(?:因为|because)", "preference"),
    ]
    for pat, source in pref_patterns:
        for m in re.finditer(pat, combined, re.IGNORECASE):
            text = m.group(0).strip()
            if len(text) > 10 and _is_memorable_content(text):
                facts.append({
                    "text": text,
                    "confidence": "medium",
                    "rationale": "Detected preference statement",
                    "source": source,
                })

    # Heuristic 4: Convention / config statements
    conv_patterns = [
        r"(?:配置|config|setting|设置)\s*[：:]\s*(.{5,80})",
        r"(?:默认|default)\s*[：:]\s*(.{5,80})",
        r"(?:约定|convention)\s*[：:]\s*(.{5,80})",
        r"(?:规则|rule)\s*[：:]\s*(.{5,80})",
    ]
    for pat in conv_patterns:
        for m in re.finditer(pat, combined, re.IGNORECASE):
            text = m.group(0).strip()
            if len(text) > 10 and _is_memorable_content(text):
                facts.append({
                    "text": text,
                    "confidence": "medium",
                    "rationale": "Detected configuration or convention",
                    "source": "convention",
                })

    # Deduplicate by text similarity
    deduped = []
    seen_texts = []
    for f in facts:
        # Filter out system notes and tool call artifacts
        if _is_noise_text(f["text"]):
            continue
        is_dup = False
        for st in seen_texts:
            if _text_similarity(f["text"], st) > 0.8:
                is_dup = True
                break
        if not is_dup:
            seen_texts.append(f["text"])
            deduped.append(f)

    return deduped


def _is_noise_text(text: str) -> bool:
    """Check if extracted text is a system note or tool artifact, not genuine user content.

    These texts should never be saved as memories.
    """
    stripped = text.strip()

    # System-level bookkeeping notes
    if stripped.startswith("[System note:") or stripped.startswith("[System]"):
        return True

    # Tool placeholder markers
    if stripped.startswith("[tool]") or stripped.startswith("{"):
        return True

    # "Review the conversation above and update the skill library" — auto-injected task
    if "Review the conversation above and update the skill library" in stripped:
        return True

    # Gateway shutdown / restart notes
    if "gateway shutdown" in stripped.lower() or "interrupted by a gateway" in stripped.lower():
        return True

    # Pure JSON / data dumps (tool outputs leaked into message text)
    if stripped.startswith('{"') or stripped.startswith('['):
        return True

    return False


def _text_similarity(a: str, b: str) -> float:
    """Quick text similarity using token overlap."""
    ta = set(_tokenise(a))
    tb = set(_tokenise(b))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / max(len(ta), len(tb))


def _is_memorable_content(text: str) -> bool:
    """Filter out non-memorable content that shouldn't be stored as memory.

    Rejects:
    - Tool output patterns (code blocks, exit codes, stdout/stderr)
    - File paths and code patterns
    - Very short or very repetitive content
    - System-like messages

    Returns True if content is worth remembering, False otherwise.
    """
    if not text or not isinstance(text, str):
        return False

    text = text.strip()

    # Too short
    if len(text) < 15:
        return False

    # Tool output patterns
    tool_indicators = [
        "```", "Exit code", "stdout", "stderr", "Tool ran without output",
        "Process completed", "Command output", "Execution result",
        "File created", "File modified", "File deleted",
    ]
    text_lower = text.lower()
    for indicator in tool_indicators:
        if indicator.lower() in text_lower:
            return False

    # File paths (Windows and Unix)
    if re.search(r'[a-zA-Z]:\\[\w\\.-]+|/[\w/\-._]+', text):
        # Only reject if looks like a file path, not just a path-like string
        if re.search(r'[\\/]([\w-]+\.[\w]{2,4}|[\w-]+[\\/])', text):
            return False

    # Code patterns (function/class definitions, imports)
    code_patterns = [
        r'^def\s+\w+\s*\(', r'^class\s+\w+', r'^import\s+\w+',
        r'^from\s+\w+\s+import', r'^\s*(public|private|protected)\s+(void|int|String)',
        r'^function\s+\w+\s*\(', r'^const\s+\w+\s*=',
    ]
    for pat in code_patterns:
        if re.match(pat, text, re.MULTILINE):
            return False

    # Very repetitive content (e.g., "------" or "aaaaa")
    if len(set(text[:50])) < 5:
        return False

    # System-like messages
    system_prefixes = ["[system]", "[error]", "[warning]", "[info]", "[debug]"]
    for prefix in system_prefixes:
        if text_lower.startswith(prefix.lower()):
            return False

    return True


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


def _run_embedding_reflection(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run a full reflection using local embeddings + rule engine (zero LLM cost).

    This replaces the expensive LLM-based reflection with:
    1. Semantic novelty detection via embeddings
    2. Heuristic fact extraction from conversation
    3. Conflict detection via embedding similarity
    4. Conservative candidate generation
    """
    mem_store = _get_mem_store()
    skill_store = _get_skill_store()
    active_memories = mem_store.list_active()
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

        if conflict_mem:
            mem, sim = conflict_mem
            # If very similar but user is correcting, mark as stale
            if _is_correction(full_user) and sim > 0.8:
                conflicts.append({
                    "with": mem.id(),
                    "kind": "stale",
                    "explain": f"User corrected previous information. Similarity: {sim:.2f}",
                    "options": ["keep_new", "keep_old"],
                })
                memory_candidates.append({
                    "fact": text,
                    "tags": tags,
                    "scope": "user",
                    "confidence": fact["confidence"],
                    "rationale": fact["rationale"],
                    "supersedes": [mem.id()],
                })
                audit_entries.append(_build_audit_entry(
                    candidate_id=cand_id,
                    decision="superseded",
                    decision_reason=f"user corrected previous info; similarity {sim:.2f}",
                    novelty_score=novelty,
                    conflict_id=mem.id(),
                    supersedes_ids=[mem.id()],
                    supersedes_reason="user correction",
                    assigned_zone=_infer_zone_from_scope("user"),
                ))
            else:
                # Just similar, not necessarily conflicting - skip to avoid duplication
                logger.debug("Similar to existing memory %s (%.3f), skipping", mem.id(), sim)
                audit_entries.append(_build_audit_entry(
                    candidate_id=cand_id,
                    decision="skipped",
                    decision_reason=f"similar to {mem.id()} (sim {sim:.2f}) without explicit correction",
                    novelty_score=novelty,
                    conflict_id=mem.id(),
                    assigned_zone=_infer_zone_from_scope("user"),
                ))
                continue
        else:
            memory_candidates.append({
                "fact": text,
                "tags": tags,
                "scope": "user",
                "confidence": fact["confidence"],
                "rationale": fact["rationale"],
                "supersedes": [],
            })
            audit_entries.append(_build_audit_entry(
                candidate_id=cand_id,
                decision="pending_storage",
                decision_reason="novelty sufficient, no conflict",
                novelty_score=novelty,
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
            fm = MemoryFrontmatter.new(
                source="reflection",
                confidence=cand.get("confidence", "medium"),
                tags=cand.get("tags", []),
                zone=zone,
            )
            fm.supersedes = cand.get("supersedes", [])
            _validate_supersedes_targets(mem_store, fm.supersedes)
            exclude_ids = list(current_session_ids | set(fm.supersedes or []))
            # Final conflict check
            conflict = mem_store.check_conflict(body, exclude_ids=exclude_ids)
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
            accepted_memories.append({"id": fm.id, "body": body, "path": str(path)})
            _remember_current_session_memory_id(fm.id)
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


def _run_raw_chunk_reflection(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Save raw conversation chunks as episode memories. Zero LLM calls.

    Academic basis: [Retrieval Bottleneck] arXiv:2603.02473, Sec.3.1.
    Basic RAG (zero LLM calls) with hybrid retrieval reaches 81.1%,
    outperforming Mem0-style Extracted Facts (77.3%).
    """
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
        fm = MemoryFrontmatter.new(
            source="raw_chunk",
            confidence="low",
            tags=["episode", "raw_chunk"],
            zone="episode",
        )
        try:
            path = mem_store.put("user", fm, body)
            accepted.append({"id": fm.id, "body_preview": body[:120]})
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


def _run_embedding_micro_reflection(user_msg: str, assistant_msg: str) -> Optional[Dict[str, Any]]:
    """Run a micro-reflection using local embeddings (zero LLM cost).

    Much faster than LLM-based micro-reflection (~50ms vs ~2000ms).
    """
    mem_store = _get_mem_store()
    active_memories = mem_store.list_active()

    combined = f"{user_msg} {assistant_msg}"
    cand_id = f"cand_{uuid.uuid4().hex[:12]}"

    # Extract facts first - if user has explicit intent, always process
    facts = _extract_facts_from_turn(user_msg, assistant_msg)
    has_explicit_intent = _is_explicit_memory_intent(user_msg)

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
    if conflict_mem:
        mem, sim = conflict_mem
        if _is_correction(user_msg) and sim > 0.7:
            supersedes = [mem.id()]
        elif has_explicit_intent and sim > 0.85:
            # Very similar and user explicitly stated - likely an update
            supersedes = [mem.id()]
        else:
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

    try:
        fm = MemoryFrontmatter.new(
            source="micro_reflection",
            confidence=best["confidence"],
            tags=tags,
            zone="episode",
        )
        fm.supersedes = supersedes
        _validate_supersedes_targets(mem_store, fm.supersedes)
        path = mem_store.put("user", fm, best["text"])
        _remember_current_session_memory_id(fm.id)
        accepted = {"id": fm.id, "body": best["text"], "path": str(path)}

        _append_reflect_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "embedding_micro",
            "summary": f"Micro-reflection accepted: {best['text'][:60]}",
            "accepted_memory": accepted,
            "novelty": novelty,
            "audit_entries": [_build_audit_entry(
                candidate_id=cand_id,
                decision="accepted" if not supersedes else "superseded",
                decision_reason="micro-reflection auto-accepted" if not supersedes else f"supersedes {supersedes}",
                novelty_score=novelty,
                supersedes_ids=supersedes,
                supersedes_reason="user correction" if _is_correction(user_msg) else "explicit intent update",
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


def _compact_episode_zone(mem_store, ctx=None) -> dict:
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

    # Get all episode entries
    all_episode = mem_store.list_by_zone("episode")

    # Filter: only non-compacted, non-superseded entries
    raw_mems = [
        m for m in all_episode
        if "compacted" not in (m.frontmatter.tags or [])
        and not m.frontmatter.supersedes
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

    for day, mems in sorted(clusters.items()):
        if len(mems) < 2:
            continue  # Skip single-entry days

        bodies = [m.body.strip() for m in mems]

        # Build summary: LLM if available, otherwise longest
        if ctx is not None and llm_summary_enabled and hasattr(ctx, "llm"):
            summary = _llm_summarize_cluster(day, bodies, ctx)
        else:
            summary = max(bodies, key=len)
            if len(summary) > 300:
                summary = summary[:297] + "..."

        fm = MemoryFrontmatter.new(
            source="system",
            confidence="medium",
            tags=["compacted", "auto-summary"],
            zone="episode",
        )
        fm.supersedes = [m.id() for m in mems]
        fm.supersedes_reason = f"Compacted {len(mems)} entries from {day}"

        try:
            mem_store.put("user", fm, summary)
            summaries.append({
                "day": day,
                "compacted": len(mems),
                "summary": summary,
                "new_id": fm.id,
            })
            total_raw_consumed += len(mems)
        except Exception as e:
            logger.debug("Compaction put failed for %s: %s", day, e)

    return {
        "compacted": len(summaries),
        "summaries": summaries,
        "total_raw_consumed": total_raw_consumed,
    }


def _llm_summarize_cluster(day: str, bodies: List[str], ctx) -> str:
    """Generate a brief summary of a day's episode entries using the LLM.

    Falls back to the longest body if LLM call fails.
    """
    prompt = (
        f"Below are {len(bodies)} raw memory entries from {day}. "
        "Summarize them into 1-2 concise sentences capturing the key information "
        "in a neutral, factual tone:\n\n"
        + "\n---\n".join(f"[{i+1}] {b[:300]}" for i, b in enumerate(bodies))
    )
    fallback = max(bodies, key=len)
    if len(fallback) > 300:
        fallback = fallback[:297] + "..."

    try:
        if hasattr(ctx, "llm") and callable(ctx.llm):
            response = ctx.llm(prompt)
            if response and isinstance(response, str) and response.strip():
                text = response.strip()
                return text[:500]
    except Exception as e:
        logger.debug("LLM summary failed for %s: %s", day, e)

    return fallback
