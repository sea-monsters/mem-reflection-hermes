"""Shared helpers for memory scope filtering.

Scope fields are a cross-cutting isolation boundary. Keep normalization,
matching, SQL clauses, schemas, and cache keys in one small module so callers
do not each invent subtly different semantics.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

SCOPE_FIELDS = ("user_id", "agent_id", "run_id")


def normalize_scope_value(value: Any) -> Optional[str]:
    """Normalize scope values before persistence, filtering, or cache keys."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def normalize_scope_filters(filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Optional[str]]]:
    """Validate and normalize a scope filter dict.

    ``None`` means no scope filtering. A provided empty dict stays empty so
    callers such as batch delete can reject it as missing user intent.
    """
    if filters is None:
        return None
    unknown = set(filters.keys()) - set(SCOPE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown filter keys: {unknown}")
    return {key: normalize_scope_value(filters[key]) for key in SCOPE_FIELDS if key in filters}


def scope_from_values(
    *,
    user_id: Any = None,
    agent_id: Any = None,
    run_id: Any = None,
) -> Dict[str, Optional[str]]:
    """Build normalized filters from discrete scope field values."""
    raw = {"user_id": user_id, "agent_id": agent_id, "run_id": run_id}
    return {key: normalize_scope_value(value) for key, value in raw.items() if value is not None}


def scope_from_context(ctx: Any = None, filters: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Optional[str]]]:
    """Build normalized scope filters from a context object or explicit filters."""
    if filters is not None:
        normalized = normalize_scope_filters(filters)
        return normalized or None
    if ctx is None:
        return None
    explicit = getattr(ctx, "scope_filters", None)
    if explicit is not None:
        normalized = normalize_scope_filters(explicit)
        return normalized or None
    normalized = scope_from_values(
        user_id=getattr(ctx, "user_id", None),
        agent_id=getattr(ctx, "agent_id", None),
        run_id=getattr(ctx, "run_id", None),
    )
    return normalized or None


def scope_cache_key(filters: Optional[Dict[str, Any]]) -> Tuple[Tuple[str, str, Optional[str]], ...]:
    """Return a cache key that distinguishes absent, NULL, and string values."""
    normalized = normalize_scope_filters(filters) or {}
    return tuple((key, "NULL" if value is None else "VALUE", value) for key, value in sorted(normalized.items()))


def build_scope_clauses(filters: Dict[str, Any]) -> Tuple[List[str], List[Any]]:
    """Build SQL WHERE clauses and params from normalized scope filters."""
    normalized = normalize_scope_filters(filters) or {}
    clauses: List[str] = []
    params: List[Any] = []
    for key in SCOPE_FIELDS:
        if key not in normalized:
            continue
        value = normalized[key]
        if value is None:
            clauses.append(f"{key} IS NULL")
        else:
            clauses.append(f"{key} = ?")
            params.append(value)
    return clauses, params


def memory_matches_scope(memory: Any, filters: Optional[Dict[str, Any]]) -> bool:
    """Return True when a loaded memory's frontmatter matches scope filters."""
    normalized = normalize_scope_filters(filters)
    if not normalized:
        return True
    frontmatter = getattr(memory, "frontmatter", memory)
    for key, expected in normalized.items():
        actual = normalize_scope_value(getattr(frontmatter, key, None))
        if actual != expected:
            return False
    return True


def filter_memories_by_scope(memories: Iterable[Any], filters: Optional[Dict[str, Any]]) -> List[Any]:
    """Filter an iterable of loaded memories by scope."""
    return [memory for memory in memories if memory_matches_scope(memory, filters)]


SCOPE_FILTER_SCHEMA = {
    "type": "object",
    "properties": {
        "user_id": {"type": ["string", "null"]},
        "agent_id": {"type": ["string", "null"]},
        "run_id": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}
# Note: SCOPE_FILTER_SCHEMA is safe to spread into a nested "filters" property.
# Do NOT spread it at the top level of a tool schema because additionalProperties: False
# would conflict with tool-specific fields.
