"""Shared helpers for memory curation.

Centralizes duplicated patterns from the legacy monolithic curator.py:
- protected memory checks (pinned / keep / permanent tags)
- cold store entry construction
- archive-and-delete transaction with unified error handling
- effectiveness loading with safe fallback
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..bridge import _refine_body

logger = logging.getLogger(__name__)

_CURATOR_CFG_KEY = "curator"

_DEFAULT_CFG: Dict[str, Any] = {
    "enabled": True,
    "trigger": "session_end",
    "ttl": {"expired_action": "archive"},
    "stale": {"days": 90, "effectiveness_threshold": 0.1},
    "episode": {"ttl_days": 30},
    "similarity": {
        "enabled": True,
        "bm25_threshold": 0.6,
        "embedding_threshold": 0.85,
        "llm_merge": False,
    },
    "cold_storage": {"enabled": True, "max_archive_size_mb": 10},
}


@dataclass
class CuratorContext:
    """Input context shared by all actions in a pipeline run."""
    mem_store: Any
    errors: List[str] = field(default_factory=list)


@dataclass
class CuratorResult:
    """Output from a single curator action."""
    action_name: str
    archived: int = 0
    compacted: int = 0
    merged: int = 0
    similar_pairs: int = 0
    orphan_edges: int = 0
    errors: List[str] = field(default_factory=list)


def _curator_config(mem_store) -> Dict[str, Any]:
    """Read curator config from plugin config, merging with defaults."""
    try:
        from ...core.store import plugin_config
        cfg = plugin_config().get(_CURATOR_CFG_KEY, {})
    except Exception:
        cfg = {}
    merged = dict(_DEFAULT_CFG)
    merged.update(cfg)
    for key in ("ttl", "stale", "episode", "similarity", "cold_storage"):
        if key in cfg and isinstance(cfg[key], dict):
            merged[key] = dict(merged.get(key, {}))
            merged[key].update(cfg[key])
    return merged


def _curator_enabled(mem_store) -> bool:
    """Is the curator active? Checks config + trigger condition."""
    cfg = _curator_config(mem_store)
    if not cfg.get("enabled", True):
        return False
    trigger = cfg.get("trigger", "session_end")
    if trigger != "session_end":
        logger.warning(
            "Curator trigger '%s' is not supported. Only 'session_end' is currently implemented.",
            trigger,
        )
    return True


def is_protected(fm) -> bool:
    """Return True if memory is pinned or tagged keep/permanent."""
    if fm.pinned:
        return True
    return bool(fm.tags and any(t in ("keep", "permanent") for t in fm.tags))


def _load_effectiveness(mem_store, memory_id: str) -> Optional[Dict[str, Any]]:
    """Load effectiveness stats for a single memory."""
    try:
        eff_dict = mem_store.list_active_effectiveness()
        if isinstance(eff_dict, dict):
            return eff_dict.get(memory_id)
    except Exception:
        pass
    return None


def load_last_access(mem_store, mid: str) -> float:
    """Load last_accessed timestamp; return 0 on any failure."""
    try:
        eff = _load_effectiveness(mem_store, mid)
        return eff.get("last_accessed", 0) if eff else 0
    except Exception:
        return 0


def build_cold_entry(mem, context_tag: str, **extra) -> Dict[str, Any]:
    """Construct a standard cold store entry dict."""
    fm = mem.frontmatter
    return {
        "id": mem.id(),
        "body": _refine_body(mem.body),
        "zone": fm.zone,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "tags": list(fm.tags or []) + ["archived", "cold", context_tag],
        "original_frontmatter": {
            "created": fm.created,
            "confidence": fm.confidence,
            "pinned": fm.pinned,
            "supersedes": list(fm.supersedes or []),
            "supersedes_reason": getattr(fm, "supersedes_reason", ""),
        },
        **extra,
    }


def archive_and_delete(
    mem_store, mem, entry: Dict[str, Any], context: str
) -> Tuple[bool, Optional[str]]:
    """Append to cold store and delete from active store.

    Returns (success, error_message). On failure the cold store append
    is the priority; a failed active-store delete is logged but the
    entry remains in cold storage for audit.
    """
    if not _append_to_cold_store(mem_store, entry):
        return False, "cold store write failed"
    try:
        mem_store.delete(mem.scope, mem.id())
        return True, None
    except Exception as e:
        logger.warning(
            "Failed to delete %s after archiving (%s): %s. Cold entry preserved.",
            mem.id(), context, e,
        )
        return False, str(e)


# Re-export for cold_store helpers consumed by this module
from .cold_store import _append_to_cold_store  # noqa: E402,F401
