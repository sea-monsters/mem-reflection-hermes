"""Intent classification and frontmatter predicate helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .models import MemoryFrontmatter


def _classify_update_intent(old_body: str, new_body: str) -> str:
    old_lower = old_body.lower()
    new_lower = new_body.lower()
    correction_markers = ["actually", "wrong", "incorrect", "not anymore", "no longer", "changed", "updated"]
    if any(m in new_lower for m in correction_markers):
        return "correction"
    if "except" in new_lower or "unless" in new_lower or "but for" in new_lower:
        return "scoped_exception"
    if any(m in new_lower for m in ["on monday", "on tuesday", "yesterday", "last week", "then"]):
        if any(m in old_lower for m in ["on monday", "on tuesday", "yesterday", "last week", "then"]):
            return "historical_episode"
    if len(new_body) > len(old_body) * 1.3 and old_lower in new_lower:
        return "elaboration"
    return "replacement"


def _is_expired(fm: "MemoryFrontmatter", now: Optional[datetime] = None) -> bool:
    if fm.valid_until is None:
        return False
    try:
        until_dt = datetime.fromisoformat(fm.valid_until)
        now_dt = now or datetime.now(timezone.utc)
        return now_dt > until_dt
    except Exception as e:
        logger.debug("Could not parse valid_until date %r: %s", fm.valid_until, e)
        return False


def _is_context_mismatch(fm: "MemoryFrontmatter", current_scope: Optional[str] = None) -> bool:
    if fm.context_scope is None or current_scope is None:
        return False
    return fm.context_scope != current_scope
