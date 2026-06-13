"""Semantic supersedes resolution helpers.

The resolver keeps replacement decisions separate from raw conflict detection
so reflection paths can distinguish:
- true corrections/replacements
- merge-worthy updates
- same-scope coexistence
- cross-scope split cases
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from ..core.scope import memory_matches_scope, normalize_scope_filters
except ImportError:
    from core.scope import memory_matches_scope, normalize_scope_filters

_CORRECTION_MARKERS = [
    "纠正一下", "更正", "修正", "说错了", "不对",
    "correct me", "actually", "i was wrong", "correction",
]

_REPLACEMENT_MARKERS = [
    "replace", "replacement", "update", "instead", "switch to",
    "from now on", "going forward", "now use", "change to",
    "改成", "改为", "以后改用", "以后统一", "从现在开始", "今后", "以后都",
]


def _memory_id(memory: Any) -> str:
    if memory is None:
        return ""
    mem_id = getattr(memory, "id", None)
    if callable(mem_id):
        return str(mem_id())
    if mem_id is not None:
        return str(mem_id)
    frontmatter = getattr(memory, "frontmatter", memory)
    frontmatter_id = getattr(frontmatter, "id", "")
    return str(frontmatter_id) if frontmatter_id is not None else ""


def _is_correction(text: str) -> bool:
    lower = (text or "").lower()
    return any(marker in lower for marker in _CORRECTION_MARKERS)


def _is_replacement_intent(text: str) -> bool:
    lower = (text or "").lower()
    return any(marker in lower for marker in _REPLACEMENT_MARKERS)


def resolve_semantic_supersedes(
    *,
    candidate_text: str,
    candidate_kind: str,
    user_msg: str,
    conflict_memory: Any = None,
    conflict_similarity: float = 0.0,
    scope_filters: Optional[Dict[str, Any]] = None,
    explicit_supersedes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Classify how a candidate should relate to an existing memory.

    Returns a small action contract:
    - ``store``: keep the new memory, no replacement edge
    - ``merge``: keep the new memory, but treat it as a semantic update cue
    - ``supersede``: keep the new memory and link it to target_ids
    - ``scope_split``: do not replace across scope boundaries
    - ``skip``: drop the candidate entirely
    """
    normalized_scope = normalize_scope_filters(scope_filters) if scope_filters is not None else None
    target_id = _memory_id(conflict_memory)
    candidate_kind = (candidate_kind or "fact").strip().lower()

    if conflict_memory is not None and normalized_scope is not None:
        if not memory_matches_scope(conflict_memory, normalized_scope):
            return {
                "action": "scope_split",
                "target_ids": [],
                "reason": f"conflict target {target_id} is outside the active scope",
                "confidence": 1.0,
            }

    if explicit_supersedes:
        if _is_correction(user_msg) or _is_replacement_intent(user_msg):
            return {
                "action": "supersede",
                "target_ids": list(dict.fromkeys(explicit_supersedes)),
                "reason": "explicit supersedes target accepted by replacement intent",
                "confidence": 0.95,
            }
        if candidate_kind in {"decision", "policy", "preference"}:
            return {
                "action": "merge",
                "target_ids": list(dict.fromkeys(explicit_supersedes)),
                "reason": "explicit supersedes target treated as semantic merge cue",
                "confidence": 0.75,
            }
        return {
            "action": "store",
            "target_ids": [],
            "reason": "explicit supersedes ignored because the message does not indicate replacement",
            "confidence": 0.5,
        }

    if conflict_memory is None:
        return {
            "action": "store",
            "target_ids": [],
            "reason": "no conflicting memory in the active scope",
            "confidence": 0.2,
        }

    if _is_correction(user_msg) or candidate_kind == "correction":
        if conflict_similarity >= 0.7:
            return {
                "action": "supersede",
                "target_ids": [target_id],
                "reason": f"correction semantics with similarity {conflict_similarity:.2f}",
                "confidence": conflict_similarity,
            }

    if candidate_kind in {"decision", "policy", "preference"} and conflict_similarity >= 0.8:
        return {
            "action": "merge",
            "target_ids": [target_id],
            "reason": f"{candidate_kind} semantics with similarity {conflict_similarity:.2f}",
            "confidence": conflict_similarity,
        }

    if candidate_kind == "intent":
        return {
            "action": "store",
            "target_ids": [],
            "reason": "explicit memory intent should be stored without replacement unless the user is correcting",
            "confidence": 0.7,
        }

    if _is_replacement_intent(user_msg) and conflict_similarity >= 0.8:
        return {
            "action": "supersede",
            "target_ids": [target_id],
            "reason": f"replacement intent with similarity {conflict_similarity:.2f}",
            "confidence": conflict_similarity,
        }

    if conflict_similarity >= 0.85:
        return {
            "action": "skip",
            "target_ids": [],
            "reason": f"similar to {target_id} without a replacement signal",
            "confidence": conflict_similarity,
        }

    return {
        "action": "store",
        "target_ids": [],
        "reason": "no strong replacement signal",
        "confidence": max(0.2, conflict_similarity),
    }

