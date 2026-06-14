"""Reflection pipeline for mem-reflection-hermes.

Simplified from ~1,700 lines to ~500 lines:
- Constructor dependency injection (no late_binding)
- raw_chunk as default mode (zero LLM calls)
- JSON parsing via json.JSONDecoder.raw_decode
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from ..core.store import (
        LoadedMemory,
        MemoryFrontmatter,
        plugin_data_dir,
        _tokenise,
    )
    from ..core.search import _embed_single, _cosine_sim, _extract_keywords, _bm25_search_scored
    from .extraction import extract_refined_memory_candidates
except ImportError:
    import sys
    from pathlib import Path
    _repo = Path(__file__).resolve().parent.parent
    import importlib.util

    # Load core.store
    _store_spec = importlib.util.spec_from_file_location("_store", str(_repo / "core" / "store.py"))
    _store_mod = importlib.util.module_from_spec(_store_spec)
    sys.modules["_store"] = _store_mod
    _store_spec.loader.exec_module(_store_mod)

    LoadedMemory = _store_mod.LoadedMemory
    MemoryFrontmatter = _store_mod.MemoryFrontmatter
    plugin_data_dir = _store_mod.plugin_data_dir
    _tokenise = _store_mod._tokenise

    # Load core.search
    _search_spec = importlib.util.spec_from_file_location("_search", str(_repo / "core" / "search.py"))
    _search_mod = importlib.util.module_from_spec(_search_spec)
    sys.modules["_search"] = _search_mod
    _search_spec.loader.exec_module(_search_mod)

    _embed_single = _search_mod._embed_single
    _cosine_sim = _search_mod._cosine_sim
    _extract_keywords = _search_mod._extract_keywords
    _bm25_search_scored = _search_mod._bm25_search_scored
    _extraction_spec = importlib.util.spec_from_file_location("_extraction", str(_repo / "reflection" / "extraction.py"))
    _extraction_mod = importlib.util.module_from_spec(_extraction_spec)
    sys.modules["_extraction"] = _extraction_mod
    _extraction_spec.loader.exec_module(_extraction_mod)
    extract_refined_memory_candidates = _extraction_mod.extract_refined_memory_candidates

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Content quality gate
# ---------------------------------------------------------------------------

_TOOL_INDICATORS = [
    "```", "Exit code", "stdout", "stderr", "Tool ran without output",
    "Process completed", "Command output", "Execution result",
    "File created", "File modified", "File deleted", "Output:",
]

_CODE_PATTERNS = [
    r"^def\s+\w+\s*\(", r"^class\s+\w+", r"^import\s+\w+",
    r"^from\s+\w+\s+import", r"^function\s+\w+\s*\(", r"^const\s+\w+\s*=",
]


def _is_memorable_content(text: str) -> bool:
    """Reject non-memorable content (tool outputs, code, paths, repetition)."""
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    # CJK characters carry more semantic weight per char
    cjk_chars = sum(1 for c in text if "一" <= c <= "鿿")
    effective_len = len(text) + cjk_chars
    if effective_len < 15:
        return False
    lower = text.lower()
    for ind in _TOOL_INDICATORS:
        if ind.lower() in lower:
            return False
    # File paths — require a drive letter prefix (Windows) or leading slash (Unix)
    # plus an extension or trailing separator to avoid false positives on normal text.
    if re.search(r"(?:[a-zA-Z]:\\[\w\\.-]{3,}|(?:^|\s)/[\w/\-._]{3,})(?:\.[\w]{2,4}|[\\/])", text):
        return False
    # Code patterns
    for pat in _CODE_PATTERNS:
        if re.match(pat, text, re.MULTILINE):
            return False
    # Repetitive
    if len(set(text[:50])) < 5:
        return False
    return True


# ---------------------------------------------------------------------------
# Heuristic fact extraction
# ---------------------------------------------------------------------------

def _is_explicit_memory_intent(text: str) -> bool:
    markers = [
        "记住", "记下来", "记住这个", "请记住",
        "remember this", "save this", "note this",
        "记下来", "记一下", "记好了",
    ]
    return any(m in text.lower() for m in markers)


def _is_correction(text: str) -> bool:
    markers = [
        "纠正一下", "更正", "修正", "说错了", "不对",
        "correct me", "actually", "i was wrong", "correction",
    ]
    return any(m in text.lower() for m in markers)


def _extract_facts_from_turn(user_msg: str, assistant_msg: str) -> List[Dict[str, Any]]:
    """Extract refined fact candidates using the shared refinement helper."""
    return extract_refined_memory_candidates(user_msg, assistant_msg)


def _is_noise_text(text: str) -> bool:
    """Reject system notes and tool artifacts, not genuine user content."""
    stripped = text.strip()
    if stripped.startswith("[System note:") or stripped.startswith("[System]"):
        return True
    if stripped.startswith("[tool]") or stripped.startswith("{"):
        return True
    if "Review the conversation above and update the skill library" in stripped:
        return True
    if "gateway shutdown" in stripped.lower() or "interrupted by a gateway" in stripped.lower():
        return True
    if stripped.startswith('{"') or stripped.startswith('['):
        return True
    return False


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def _format_messages_for_reflection(messages: List[Dict[str, Any]]) -> str:
    """Format messages, filtering out tool content."""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        if role == "tool":
            continue
        if role == "assistant" and "tool_calls" in msg:
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = "\n".join(texts)
        elif not isinstance(content, str):
            content = str(content)
        lines.append(f"[{role}] {content}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# JSON schema for structured reflection output
# ---------------------------------------------------------------------------

def _build_reflect_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "memories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "zone": {"type": "string"},
                        "supersedes": {"type": "array", "items": {"type": "string"}},
                        "supersedes_reason": {"type": "string"},
                    },
                    "required": ["text", "confidence"],
                },
            },
            "skills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "triggers": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "description"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["memories", "skills", "summary"],
    }


# ---------------------------------------------------------------------------
# Reflection log
# ---------------------------------------------------------------------------

_log_lock = threading.Lock()


def _log_path() -> Path:
    return plugin_data_dir() / "reflect-log.jsonl"


def _append_reflect_log(entry: Dict[str, Any], log_path: Optional[Path] = None) -> None:
    with _log_lock:
        p = log_path or _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_reflect_log(n: int = 10, log_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = log_path or _log_path()
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(entries) >= n:
            break
    return list(reversed(entries))


# ---------------------------------------------------------------------------
# ReflectionEngine
# ---------------------------------------------------------------------------

class ReflectionEngine:
    """Simplified reflection with dependency injection."""

    def __init__(self, store, search, graph, log_path: Optional[Path] = None):
        self.store = store
        self.search = search
        self.graph = graph
        self._log_path = log_path
        self._mode = os.environ.get("MEM_REFLECTION_MODE", "raw_chunk")

    # -- micro reflection ----------------------------------------------------

    def micro(self, ctx, user_msg: str, assistant_msg: str) -> Optional[Dict[str, Any]]:
        """Per-turn micro-reflection. Returns candidate or None."""
        if self._mode == "raw_chunk":
            return self._micro_raw_chunk(user_msg, assistant_msg)
        return self._micro_heuristic(user_msg, assistant_msg)

    def _micro_raw_chunk(self, user_msg: str, assistant_msg: str) -> Optional[Dict[str, Any]]:
        """Store the turn as a raw episode memory."""
        combined = f"{user_msg.strip()}\n\n{assistant_msg.strip()}"
        if len(combined) < 20 or not _is_memorable_content(combined) or _is_noise_text(combined):
            return None
        fm = MemoryFrontmatter.new(
            source="raw_chunk", confidence="low",
            tags=["episode", "raw_chunk"], zone="episode",
        )
        self.store.put("user", fm, combined)
        return {"id": fm.id, "type": "raw_chunk", "preview": combined[:120]}

    def _micro_heuristic(self, user_msg: str, assistant_msg: str) -> Optional[Dict[str, Any]]:
        """Heuristic fact extraction with embedding novelty check."""
        facts = _extract_facts_from_turn(user_msg, assistant_msg)
        if not facts:
            return None
        facts.sort(key=lambda f: 0 if f["confidence"] == "high" else (1 if f["confidence"] == "medium" else 2))
        best = facts[0]
        fm = MemoryFrontmatter.new(
            source="micro_reflection", confidence=best["confidence"],
            tags=_extract_keywords(best["text"], top_k=3), zone="general",
        )
        self.store.put("user", fm, best["text"])
        return {"id": fm.id, "type": "fact", "text": best["text"]}

    # -- full reflection -----------------------------------------------------

    def full(self, ctx, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Session-end full reflection."""
        if self._mode == "raw_chunk":
            return self._full_raw_chunk(messages)
        return self._full_llm(ctx, messages)

    def _full_raw_chunk(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Store all memorable turns as raw chunks."""
        accepted = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "tool":
                continue
            if role == "assistant" and "tool_calls" in msg:
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(texts)
            if not content or len(content.strip()) < 20:
                continue
            if not _is_memorable_content(content) or _is_noise_text(content):
                continue
            fm = MemoryFrontmatter.new(
                source="raw_chunk", confidence="low",
                tags=["episode", "raw_chunk"], zone="episode",
            )
            self.store.put("user", fm, content.strip())
            accepted.append({"id": fm.id, "preview": content[:120]})

        summary = f"Raw chunk: {len(accepted)} chunks stored"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "raw_chunk",
            "summary": summary,
            "accepted_count": len(accepted),
        }
        _append_reflect_log(entry, self._log_path)
        return {"summary": summary, "accepted": accepted}

    def _full_llm(self, ctx, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """LLM-based full reflection (expensive, optional)."""
        transcript = _format_messages_for_reflection(messages)
        if not transcript.strip():
            return {"summary": "Empty transcript", "accepted": []}

        # Try structured JSON output
        instructions = (
            "Review this conversation transcript and extract facts worth remembering. "
            "Return JSON with memories (text, confidence, tags, zone) and skills (name, description, triggers)."
        )
        try:
            if ctx and hasattr(ctx, "llm"):
                result = ctx.llm.complete_structured(
                    instructions=instructions,
                    input=[{"type": "text", "text": transcript}],
                    json_schema=_build_reflect_schema(),
                    max_tokens=2048,
                )
                parsed = result.parsed if result else None
            else:
                parsed = None
        except Exception as e:
            logger.debug("Full reflection LLM call failed: %s", e)
            parsed = None

        if not parsed:
            # Fallback to raw_chunk
            return self._full_raw_chunk(messages)

        accepted = []
        for mem in parsed.get("memories", []):
            text = mem.get("text", "").strip()
            if not text or not _is_memorable_content(text):
                continue
            fm = MemoryFrontmatter.new(
                source="reflection",
                confidence=mem.get("confidence", "medium"),
                tags=mem.get("tags", []),
                zone=mem.get("zone", "general"),
            )
            self.store.put("user", fm, text)
            accepted.append({"id": fm.id, "text": text})

        summary = parsed.get("summary", f"Full reflection: {len(accepted)} memories stored")
        _append_reflect_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "full_llm",
            "summary": summary,
            "accepted_count": len(accepted),
        }, self._log_path)
        return {"summary": summary, "accepted": accepted}

    # -- audit ---------------------------------------------------------------

    def audit(self, candidate: Dict[str, Any], decision: str, reason: str) -> Dict[str, Any]:
        return {
            "candidate_id": candidate.get("id", ""),
            "decision": decision,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def log(self, entry: Dict[str, Any]) -> None:
        _append_reflect_log(entry, self._log_path)

    def recent(self, n: int = 10) -> List[Dict[str, Any]]:
        return _read_reflect_log(n, self._log_path)


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
    """Public audit helper kept on the new reflection surface."""
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


def _recent_reflect_outcomes(n: int = 10) -> List[Dict[str, Any]]:
    """Return the most recent reflection log entries."""
    return _read_reflect_log(n)


def _delegate_runtime_reflection(name: str):
    from . import runtime as runtime_reflection

    return getattr(runtime_reflection, name)


def _get_mem_store():
    from mem_reflection_hermes import _get_mem_store as _root_get_mem_store

    return _root_get_mem_store()


def _get_skill_store():
    from mem_reflection_hermes import _get_skill_store as _root_get_skill_store

    return _root_get_skill_store()


def _reflection_mode() -> str:
    from mem_reflection_hermes import _reflection_mode as _root_reflection_mode

    return _root_reflection_mode()


def _build_context_block(query: str = ""):
    from mem_reflection_hermes import _build_context_block as _root_build_context_block

    return _root_build_context_block(query)


def _auto_rebalance_zones():
    from mem_reflection_hermes import _auto_rebalance_zones as _root_auto_rebalance_zones

    return _root_auto_rebalance_zones()


def _parse_reflect_output(text: str) -> Optional[Dict[str, Any]]:
    return _delegate_runtime_reflection("_parse_reflect_output")(text)


def _repair_truncated_json(s: str) -> Optional[str]:
    return _delegate_runtime_reflection("_repair_truncated_json")(s)


def _load_pending_skill_candidates():
    return _delegate_runtime_reflection("_load_pending_skill_candidates")()


def _save_pending_skill_candidates(candidates):
    return _delegate_runtime_reflection("_save_pending_skill_candidates")(candidates)


def _format_pending_skills_for_display():
    return _delegate_runtime_reflection("_format_pending_skills_for_display")()


def _approve_skill(pending_id: str):
    return _delegate_runtime_reflection("_approve_skill")(pending_id)


def _reject_skill(pending_id: str, reason: str = ""):
    return _delegate_runtime_reflection("_reject_skill")(pending_id, reason)


def _reset_current_session_memory_ids() -> None:
    return _delegate_runtime_reflection("_reset_current_session_memory_ids")()


def _run_full_reflection(ctx, messages: List[Dict[str, Any]], scope_filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _delegate_runtime_reflection("_run_full_reflection")(ctx, messages, scope_filters=scope_filters)


def _run_micro_reflection(
    ctx,
    user_msg: str,
    assistant_msg: str,
    scope_filters: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    return _delegate_runtime_reflection("_run_micro_reflection")(ctx, user_msg, assistant_msg, scope_filters=scope_filters)


def _run_embedding_reflection(messages: List[Dict[str, Any]], scope_filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _delegate_runtime_reflection("_run_embedding_reflection")(messages, scope_filters=scope_filters)


def _run_embedding_micro_reflection(
    user_msg: str,
    assistant_msg: str,
    scope_filters: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    return _delegate_runtime_reflection("_run_embedding_micro_reflection")(user_msg, assistant_msg, scope_filters=scope_filters)
