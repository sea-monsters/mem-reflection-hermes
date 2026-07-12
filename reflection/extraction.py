"""Shared refined reflection extraction helpers.

This module keeps raw transcript capture separate from refined memory
extraction so runtime and engine paths do not drift apart.
"""
from __future__ import annotations

import re
from pathlib import Path
import sys
from typing import Any, Dict, List

# Canonical typed-memory vocabulary. The reflection LLM prompt/schema and the
# heuristic extraction layer both draw from this set so the `kind` column in
# the typed fact sidecar stays consistent across embedding and LLM modes
# (round-3 audit P2-1: previously the LLM path never produced a kind, so the
# sidecar's kind distribution silently degenerated to "fact").
REFINED_MEMORY_KINDS = (
    "fact",
    "preference",
    "decision",
    "policy",
    "todo",
    "correction",
    "intent",
    "procedure",
    "summary",
    "raw_chunk",
)
_REFINED_KIND_SET = frozenset(REFINED_MEMORY_KINDS)


def normalize_memory_kind(kind: Any) -> str:
    """Normalize an external (LLM/provided) kind to the canonical vocabulary.

    Returns ``"fact"`` for anything missing or outside the known set, so an
    unexpected model output cannot poison the typed sidecar's ``kind`` column
    (round-3 audit P2-1).
    """
    if not kind:
        return "fact"
    lowered = str(kind).strip().lower()
    return lowered if lowered in _REFINED_KIND_SET else "fact"

try:
    from ..core.search import (
        _extract_keywords,
    )
    from ..core.store import _tokenise
except ImportError:
    _repo = Path(__file__).resolve().parent.parent
    import importlib.util

    _search_spec = importlib.util.spec_from_file_location("_search", str(_repo / "core" / "search.py"))
    _search_mod = importlib.util.module_from_spec(_search_spec)
    sys.modules["_search"] = _search_mod
    _search_spec.loader.exec_module(_search_mod)
    _extract_keywords = _search_mod._extract_keywords

    _store_spec = importlib.util.spec_from_file_location("_store", str(_repo / "core" / "store.py"))
    _store_mod = importlib.util.module_from_spec(_store_spec)
    sys.modules["_store"] = _store_mod
    _store_spec.loader.exec_module(_store_mod)
    _tokenise = _store_mod._tokenise


def _is_memorable_content(text: str) -> bool:
    if not text or not isinstance(text, str):
        return False

    text = text.strip()
    cjk_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    effective_len = len(text) + cjk_chars
    if effective_len < 15:
        return False

    text_lower = text.lower()
    tool_indicators = [
        "```", "exit code", "stdout", "stderr", "tool ran without output",
        "process completed", "command output", "execution result",
        "file created", "file modified", "file deleted",
    ]
    for indicator in tool_indicators:
        if indicator in text_lower:
            return False

    if re.search(r"[a-zA-Z]:\\[\w\\.-]+|/[\w/\-._]+", text):
        if re.search(r"[\\/]([\w-]+\.[\w]{2,4}|[\w-]+[\\/])", text):
            return False

    code_patterns = [
        r"^def\s+\w+\s*\(", r"^class\s+\w+", r"^import\s+\w+",
        r"^from\s+\w+\s+import", r"^\s*(public|private|protected)\s+(void|int|String)",
        r"^function\s+\w+\s*\(", r"^const\s+\w+\s*=",
    ]
    for pat in code_patterns:
        if re.match(pat, text, re.MULTILINE):
            return False

    if len(set(text[:50])) < 5:
        return False

    if text_lower.startswith(("[system]", "[error]", "[warning]", "[info]", "[debug]")):
        return False

    return True


def _is_noise_text(text: str) -> bool:
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


def _text_similarity(a: str, b: str) -> float:
    ta = set(_tokenise(a))
    tb = set(_tokenise(b))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / max(len(ta), len(tb))


def _is_explicit_memory_intent(text: str) -> bool:
    markers = [
        "记住", "记下来", "记住这个", "请记住",
        "remember this", "save this", "note this",
        "记一下", "记好了", "remember", "note that",
    ]
    lower = (text or "").lower()
    return any(marker in lower for marker in markers)


def _is_correction(text: str) -> bool:
    markers = [
        "纠正一下", "更正", "修正", "说错了", "不对",
        "correct me", "actually", "i was wrong", "correction",
    ]
    lower = (text or "").lower()
    return any(marker in lower for marker in markers)


def _is_procedure(text: str) -> bool:
    markers = [
        "how to", "steps", "workflow", "process", "procedure",
        "configure", "setup", "安装", "配置", "步骤", "流程",
        "way to", "method of", "always use", "never use", "make sure",
    ]
    lower = (text or "").lower()
    return any(marker in lower for marker in markers)


def _sentence_fragments(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"[。！？.!?\n]+", text or "") if part.strip()]


def _candidate(
    *,
    text: str,
    confidence: str,
    rationale: str,
    source: str,
    kind: str,
    priority: int,
) -> Dict[str, Any]:
    tags = _extract_keywords(text, top_k=3)
    return {
        "text": text,
        "confidence": confidence,
        "rationale": rationale,
        "source": source,
        "kind": kind,
        "priority": priority,
        "tags": tags,
    }


def extract_refined_memory_candidates(user_msg: str, assistant_msg: str) -> List[Dict[str, Any]]:
    """Extract and rank refined memory candidates from a turn."""
    facts: List[Dict[str, Any]] = []
    combined = f"{user_msg} {assistant_msg}"

    # Explicit memory intent
    if _is_explicit_memory_intent(user_msg):
        for s in _sentence_fragments(user_msg):
            if _is_explicit_memory_intent(s) and len(s) > 10 and _is_memorable_content(s):
                facts.append(_candidate(
                    text=s,
                    confidence="high",
                    rationale="User explicitly requested to remember",
                    source="explicit_intent",
                    kind="intent",
                    priority=0,
                ))

    # Corrections
    if _is_correction(user_msg):
        for s in _sentence_fragments(user_msg):
            if _is_correction(s) and len(s) > 10 and _is_memorable_content(s):
                facts.append(_candidate(
                    text=s,
                    confidence="medium",
                    rationale="User corrected a previous statement",
                    source="correction",
                    kind="correction",
                    priority=1,
                ))

    # Decisions / conventions
    decision_markers = ["决定", "统一", "约定", "定为", "采用", "从现在开始", "今后", "以后都"]
    for s in _sentence_fragments(combined):
        if len(s) > 10 and _is_memorable_content(s) and any(marker in s for marker in decision_markers):
            facts.append(_candidate(
                text=s,
                confidence="medium",
                rationale="Detected decision or policy statement",
                source="decision",
                kind="decision",
                priority=2,
            ))

    # TODO / action items
    for s in _sentence_fragments(combined):
        lowered = s.lower()
        if len(s) > 10 and _is_memorable_content(s) and (
            "todo" in lowered or "待办" in s or "后续" in s or "需要补" in s
        ):
            facts.append(_candidate(
                text=s,
                confidence="medium",
                rationale="Detected task or follow-up item",
                source="todo",
                kind="todo",
                priority=3,
            ))

    # Preferences
    not_sentence_end = r"[^\n。！？.!?]"
    pref_patterns = [
        (rf"(?:我|i)\s*(?:喜欢|prefer|like|want|想|要)\s*({not_sentence_end}{{5,80}})", "preference"),
        (rf"(?:我|i)\s*(?:不喜欢|hate|dislike|不想)\s*({not_sentence_end}{{5,80}})", "preference"),
        (rf"(?:我|i)\s*(?:总是|always|usually|never)\s*({not_sentence_end}{{5,80}})", "preference"),
        (rf"(?:用|use)\s*({not_sentence_end}{{3,40}})\s*(?:因为|because)", "preference"),
    ]
    for pat, source in pref_patterns:
        for m in re.finditer(pat, combined, re.IGNORECASE):
            text = m.group(0).strip()
            if len(text) > 10 and _is_memorable_content(text):
                facts.append(_candidate(
                    text=text,
                    confidence="medium",
                    rationale="Detected preference statement",
                    source=source,
                    kind="preference",
                    priority=4,
                ))

    # Policies / conventions
    policy_patterns = [
        r"(?:配置|config|setting|设置)\s*[：:]\s*(.{5,80})",
        r"(?:默认|default)\s*[：:]\s*(.{5,80})",
        r"(?:约定|convention)\s*[：:]\s*(.{5,80})",
        r"(?:规则|rule)\s*[：:]\s*(.{5,80})",
    ]
    for pat in policy_patterns:
        for m in re.finditer(pat, combined, re.IGNORECASE):
            text = m.group(0).strip()
            if len(text) > 10 and _is_memorable_content(text):
                facts.append(_candidate(
                    text=text,
                    confidence="medium",
                    rationale="Detected configuration or convention",
                    source="policy",
                    kind="policy",
                    priority=5,
                ))

    # Procedure / skill seeds
    if _is_procedure(assistant_msg) and len(assistant_msg) > 200:
        summary = assistant_msg.strip()
        if len(summary) > 800:
            summary = summary[:800]
        facts.append(_candidate(
            text=summary,
            confidence="low",
            rationale="Assistant provided a reusable procedure",
            source="procedure",
            kind="procedure",
            priority=6,
        ))

    # Deduplicate by text similarity
    deduped: List[Dict[str, Any]] = []
    seen_texts: List[str] = []
    for fact in facts:
        if _is_noise_text(fact["text"]):
            continue
        if any(_text_similarity(fact["text"], seen) > 0.8 for seen in seen_texts):
            continue
        seen_texts.append(fact["text"])
        deduped.append(fact)

    deduped.sort(key=lambda item: (item.get("priority", 99), -len(item.get("text", ""))))
    return deduped
