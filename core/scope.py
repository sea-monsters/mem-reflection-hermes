"""Shared helpers for memory scope filtering.

Scope fields are a cross-cutting isolation boundary. Keep normalization,
matching, SQL clauses, schemas, and cache keys in one small module so callers
do not each invent subtly different semantics.

Round-3 (P2-3) explicit-intent model
------------------------------------
Three distinct intents collapse onto the same dict shapes and caused the
``None`` / ``{}`` / ``{field: None}`` ambiguity flagged in the round-2 audit:

* ``UNSCOPED``    — return everything (no filter at all).
* ``TENANT``      — filter to specific ``user_id`` / ``agent_id`` / ``run_id``.
* ``GLOBAL_ONLY`` — return only rows where the scope columns are ``NULL``
  (global/shared memories), explicitly excluding tenant rows.

``None`` continues to mean UNSCOPED and a dict of concrete values means TENANT,
both unchanged. ``GLOBAL_ONLY`` is reachable only through the new
:func:`global_only_scope` helper, which tags the normalized dict with a private
``_scope_intent`` marker so :func:`build_scope_clauses` can emit ``IS NULL``
clauses for all three columns. Empty dicts (``{}``) keep their existing
de-facto "no filter" behaviour to avoid breaking the many ``if filters:``
guards across the codebase.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCOPE_FIELDS = ("user_id", "agent_id", "run_id")

# Private marker key carried inside a normalized filter dict to record an
# explicit scope intent that the field values alone cannot express (today:
# GLOBAL_ONLY). Prefixed so it never collides with a real scope column.
_SCOPE_INTENT_KEY = "_scope_intent"


class ScopeIntent(str, Enum):
    """Explicit scope-filtering intent.

    ``str`` mixin so the value serializes cleanly into JSON / audit logs.
    """

    UNSCOPED = "unscoped"        # return everything
    TENANT = "tenant"            # filter by concrete scope field values
    GLOBAL_ONLY = "global_only"  # only rows where scope columns are NULL


def _is_global_only(filters: Any) -> bool:
    """True when *filters* carries the explicit GLOBAL_ONLY intent marker."""
    return isinstance(filters, dict) and filters.get(_SCOPE_INTENT_KEY) == ScopeIntent.GLOBAL_ONLY.value


def global_only_scope() -> Dict[str, Any]:
    """Build a normalized filter dict that selects only global (NULL-scope) rows.

    This is the canonical way to express the GLOBAL_ONLY intent; an empty dict
    ``{}`` is intentionally NOT overloaded to mean global-only because too many
    call sites gate on ``if filters:`` and would then silently drop the filter.
    """
    return {_SCOPE_INTENT_KEY: ScopeIntent.GLOBAL_ONLY.value}


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

    The explicit GLOBAL_ONLY intent marker (see :func:`global_only_scope`) is
    preserved so it can flow through to :func:`build_scope_clauses`.
    """
    if filters is None:
        return None
    unknown = set(filters.keys()) - set(SCOPE_FIELDS) - {_SCOPE_INTENT_KEY}
    if unknown:
        raise ValueError(f"Unknown filter keys: {unknown}")
    normalized = {key: normalize_scope_value(filters[key]) for key in SCOPE_FIELDS if key in filters}
    # Preserve the explicit GLOBAL_ONLY intent marker if present.
    if filters.get(_SCOPE_INTENT_KEY) == ScopeIntent.GLOBAL_ONLY.value:
        normalized[_SCOPE_INTENT_KEY] = ScopeIntent.GLOBAL_ONLY.value
    return normalized


def scope_from_values(
    *,
    user_id: Any = None,
    agent_id: Any = None,
    run_id: Any = None,
) -> Dict[str, Optional[str]]:
    """Build normalized filters from discrete scope field values.

    Always returns a concrete TENANT dict (possibly empty). Callers that need
    the GLOBAL_ONLY intent must use :func:`global_only_scope` instead.
    """
    raw = {"user_id": user_id, "agent_id": agent_id, "run_id": run_id}
    return {key: normalize_scope_value(value) for key, value in raw.items() if value is not None}


def scope_from_context(ctx: Any = None, filters: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Optional[str]]]:
    """Build normalized scope filters from a context object or explicit filters."""
    if filters is not None:
        normalized = normalize_scope_filters(filters)
        # An explicit GLOBAL_ONLY marker must survive even when it produces an
        # otherwise-empty normalized dict (which would otherwise be falsy).
        if normalized and _SCOPE_INTENT_KEY in normalized:
            return normalized
        return normalized or None
    if ctx is None:
        return None
    explicit = getattr(ctx, "scope_filters", None)
    if explicit is not None:
        normalized = normalize_scope_filters(explicit)
        if normalized and _SCOPE_INTENT_KEY in normalized:
            return normalized
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
    parts: List[Tuple[str, str, Optional[str]]] = []
    # Include the intent marker so GLOBAL_ONLY caches separately from UNSCOPED.
    if _is_global_only(normalized):
        parts.append((_SCOPE_INTENT_KEY, "VALUE", ScopeIntent.GLOBAL_ONLY.value))
    for key, value in sorted({k: v for k, v in normalized.items() if k in SCOPE_FIELDS}.items()):
        parts.append((key, "NULL" if value is None else "VALUE", value))
    return tuple(parts)


def build_scope_clauses(filters: Dict[str, Any]) -> Tuple[List[str], List[Any]]:
    """Build SQL WHERE clauses and params from normalized scope filters.

    Intent handling:
    * GLOBAL_ONLY (explicit marker) → ``user_id IS NULL AND agent_id IS NULL
      AND run_id IS NULL`` for all three columns.
    * TENANT (concrete values) → equality / ``IS NULL`` clauses per provided
      field.
    * Empty / UNSCOPED → no clauses (callers gate this with ``if filters:``).
    """
    normalized = normalize_scope_clauses_input(filters)

    # Explicit global-only: emit IS NULL for every scope column.
    if _is_global_only(normalized):
        return [f"{col} IS NULL" for col in SCOPE_FIELDS], []

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


def normalize_scope_clauses_input(filters: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize input for clause building without dropping the GLOBAL_ONLY marker.

    A thin wrapper over :func:`normalize_scope_filters` that keeps ``{}`` as
    empty (existing behaviour) rather than collapsing it to ``None``.
    """
    if filters is None:
        return {}
    return normalize_scope_filters(filters) or {}


def memory_matches_scope(memory: Any, filters: Optional[Dict[str, Any]]) -> bool:
    """Return True when a loaded memory's frontmatter matches scope filters."""
    normalized = normalize_scope_filters(filters)
    if not normalized:
        return True
    frontmatter = getattr(memory, "frontmatter", memory)
    # GLOBAL_ONLY: the memory matches only if every scope field is NULL.
    if _is_global_only(normalized):
        return all(normalize_scope_value(getattr(frontmatter, key, None)) is None for key in SCOPE_FIELDS)
    for key, expected in normalized.items():
        if key == _SCOPE_INTENT_KEY:
            continue
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
