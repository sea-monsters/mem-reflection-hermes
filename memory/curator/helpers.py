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

try:
    from ...core.scope import normalize_scope_filters
except ImportError:
    try:
        from core.scope import normalize_scope_filters
    except ImportError:
        def normalize_scope_filters(filters):  # type: ignore[no-redef]
            return filters

logger = logging.getLogger(__name__)

# Resolve the shared late-binding helper safely for both package and standalone
# module loading. Bounded fallback chain is acceptable here because it only
# locates _lb() itself; downstream cross-module imports must go through _lb.
try:
    from mem_reflection_hermes.runtime._lb import _lb as _lb_fn
except ImportError:
    _lb_fn = None
if _lb_fn is None:
    try:
        from runtime._lb import _lb as _lb_fn
    except ImportError:
        _lb_fn = None

# Cross-module body refinement: prefer memory.bridge._refine_body when
# resolvable, else fall back to a minimal strip so build_cold_entry never
# raises during standalone loading.
try:
    from ..bridge import _refine_body as _refine_body_fn
except ImportError:
    _bridge_mod = _lb_fn("mem_reflection_hermes.memory.bridge") if _lb_fn is not None else None
    if _bridge_mod is not None:
        _refine_body_fn = _bridge_mod._refine_body  # type: ignore[assignment]
    else:
        def _refine_body_fn(body: str, max_chars: int = 500) -> str:  # type: ignore[misc]
            return body.strip()


_CURATOR_CFG_KEY = "curator"

_DEFAULT_CFG: Dict[str, Any] = {
    "enabled": True,
    "trigger": "session_end",
    "ttl": {"expired_action": "archive"},
    # effectiveness_threshold compares against the combined score
    # factor() * decay_factor(), whose floor is ~0.15 (factor min 0.5 x decay
    # min 0.3). A threshold of 0.2 lets genuinely low-utility memories (rarely
    # referenced AND long untouched) get archived, while active ones (decay=1.0)
    # always score >= 0.5 and are kept.
    "stale": {"days": 90, "effectiveness_threshold": 0.2},
    "episode": {"ttl_days": 30},
    "similarity": {
        "enabled": True,
        "bm25_threshold": 0.6,
        "embedding_threshold": 0.85,
        "llm_merge": False,
    },
    "cold_storage": {"enabled": True, "max_archive_size_mb": 10},
    # Stats compaction: when the event stream (memory-stats.jsonl) exceeds this
    # many lines, the curator folds it into the aggregate snapshot
    # (effectiveness-index.jsonl) and truncates the stream. Keeps read-time
    # scanning bounded to the post-compaction tail.
    "stats": {"compact_threshold_lines": 5000},
}


@dataclass
class CuratorContext:
    """Input context shared by all actions in a pipeline run."""
    mem_store: Any
    filters: Optional[Dict[str, Optional[str]]] = None
    admin_global: bool = False
    scope_label: str = "local_global"
    errors: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.filters = normalize_scope_filters(self.filters)
        if self.admin_global:
            self.scope_label = "global_admin"
        elif self.filters:
            self.scope_label = "scoped"

    def list_active(self):
        """Return active memories according to this curator run's scope policy."""
        if self.admin_global or not self.filters:
            return self.mem_store.list_active()
        return self.mem_store.list_active(filters=self.filters)


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
    cfg: Dict[str, Any] = {}
    # Test seam: allow a store to inject config without requiring full
    # package-relative import resolution.
    if mem_store is not None and hasattr(mem_store, "_plugin_config_override"):
        cfg = mem_store._plugin_config_override.get(_CURATOR_CFG_KEY, {})
    else:
        core_store = _lb_fn("mem_reflection_hermes.core.store") if _lb_fn is not None else None
        if core_store is None:
            core_store = _lb_fn("core.store") if _lb_fn is not None else None
        if core_store is not None and hasattr(core_store, "plugin_config"):
            cfg = core_store.plugin_config().get(_CURATOR_CFG_KEY, {})
        else:
            cfg = {}
    merged = dict(_DEFAULT_CFG)
    merged.update(cfg)
    for key in ("ttl", "stale", "episode", "similarity", "cold_storage", "stats"):
        if key in cfg and isinstance(cfg[key], dict):
            # Start from defaults so partial overrides do not drop sibling keys.
            merged[key] = dict(_DEFAULT_CFG.get(key, {}))
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


def _load_effectiveness(mem_store, memory_id: str):
    """Load the MemoryEffectiveness record for a single memory.

    Returns a MemoryEffectiveness dataclass (loaded/referenced/accessed/
    last_event_at with factor() and decay_factor()), or None if the store
    exposes no effectiveness data. Callers must use attribute/method access
    (eff.factor(), eff.last_event_at) -- NOT dict-style .get().
    """
    try:
        eff_dict = mem_store.effectiveness(memory_id)
    except AttributeError:
        # Store has no effectiveness() API -- callers fail open (treat as None).
        return None
    if isinstance(eff_dict, dict):
        return eff_dict.get(memory_id)
    return None


def load_last_access(mem_store, mid: str) -> float:
    """Return last-event timestamp (epoch seconds) for a memory, or 0.

    Derived from MemoryEffectiveness.last_event_at (ISO-8601 string written by
    the stats pipeline). Returns 0 when there is no recorded event.
    """
    try:
        eff = _load_effectiveness(mem_store, mid)
        if not eff or not getattr(eff, "last_event_at", None):
            return 0
        from datetime import datetime, timezone
        last = datetime.fromisoformat(eff.last_event_at.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last.timestamp()
    except Exception:
        return 0


def _count_stats_lines(stats_path) -> int:
    """Count lines in the memory-stats.jsonl event stream (cheap generator)."""
    if not stats_path.exists():
        return 0
    try:
        with open(stats_path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def compact_stats_snapshot(mem_store) -> Dict[str, Any]:
    """Fold the stats event stream into the aggregate snapshot and truncate it.

    Dual-track compaction (invoked by the curator at session end):
      1. load_effectiveness() reads snapshot baseline + event-stream tail and
         returns the complete per-memory aggregate.
      2. _write_effectiveness_snapshot() atomically rewrites the snapshot with
         one row per memory_id (using _safe_write: tmp+fsync+os.replace).
      3. The event stream is truncated (emptied) since every event is now folded
         into the snapshot.
      4. The in-process effectiveness cache is invalidated.

    Skipped (no-op) when the event stream is below the configured
    compact_threshold_lines, to avoid rewriting the snapshot every session.

    Returns a small status dict for reporting.
    """
    result = {"compacted": False, "lines_before": 0, "lines_after": 0, "error": None}
    try:
        core_store = _lb_fn("mem_reflection_hermes.core.store") if _lb_fn is not None else None
        if core_store is None:
            core_store = _lb_fn("core.store") if _lb_fn is not None else None
        if core_store is None:
            result["error"] = "core.store module unavailable"
            return result

        stats_path = core_store._stats_path()
        threshold = (
            _curator_config(mem_store)
            .get("stats", {})
            .get("compact_threshold_lines", 5000)
        )
        lines_before = _count_stats_lines(stats_path)
        result["lines_before"] = lines_before
        if lines_before < threshold:
            return result  # below threshold -> skip compaction this run

        # 1. Read the full aggregate (snapshot + tail).
        eff_map = core_store.load_effectiveness()
        # 2. folded_at = the max event timestamp actually folded, NOT wall-clock
        #    now. The read path skips events with at <= folded_at, so using the
        #    last event's at (rather than compaction time) ensures events written
        #    AFTER compaction but with an earlier timestamp are not dropped.
        folded_at = max(
            (e.last_event_at for e in eff_map.values() if e.last_event_at),
            default=None,
        )
        if folded_at is None:
            folded_at = datetime.now(timezone.utc).isoformat()
        # 3. Rewrite the snapshot atomically.
        core_store._write_effectiveness_snapshot(eff_map, folded_at)
        # 4. Truncate the event stream (all events now live in the snapshot).
        core_store._safe_write(stats_path, "")
        # 5. Invalidate the in-process cache so the next read sees fresh state.
        core_store._invalidate_effectiveness_cache()

        result["compacted"] = True
        result["lines_after"] = 0
    except Exception as e:
        logger.warning("compact_stats_snapshot failed: %s", e)
        result["error"] = str(e)
    return result


def build_cold_entry(mem, context_tag: str, **extra) -> Dict[str, Any]:
    """Construct a standard cold store entry dict."""
    fm = mem.frontmatter
    return {
        "id": mem.id(),
        "body": _refine_body_fn(mem.body),
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


# Re-export for cold_store helpers consumed by this module.
# Guard against standalone loading where relative imports have no parent package.
try:
    from .cold_store import _append_to_cold_store  # noqa: E402,F401
except ImportError:
    def _append_to_cold_store(mem_store, entry):  # type: ignore[no-redef]
        return True
