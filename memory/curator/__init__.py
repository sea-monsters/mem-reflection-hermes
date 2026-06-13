"""Curator package — composable action pipeline for memory lifecycle.

Public API:
- _run_curator(ctx, mem_store, filters=None, admin_global=False) -> dict: run the full pipeline
- Action classes: ArchiveStale, CompactChains, ArchiveSuperseded, MergeSimilar, CleanOrphanEdges
- Helpers: is_protected, build_cold_entry, archive_and_delete, load_last_access
- Cold store: _cold_store_path, _load_cold_store, _append_to_cold_store, _restore_from_cold
- Config: _curator_config, _curator_enabled
- Report: generate_report

Backward-compatible thin wrappers for legacy top-level functions are provided
so existing imports continue to work.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from ...core.scope import normalize_scope_filters, scope_from_context
except ImportError:
    try:
        from core.scope import normalize_scope_filters, scope_from_context
    except ImportError:
        def normalize_scope_filters(filters):  # type: ignore[no-redef]
            return filters
        def scope_from_context(ctx=None, filters=None):  # type: ignore[no-redef]
            if filters is not None:
                return filters
            if ctx is None:
                return None
            return getattr(ctx, "scope_filters", None)

from .actions import (
    ArchiveStale,
    ArchiveSuperseded,
    CleanOrphanEdges,
    CompactChains,
    CuratorAction,
    CuratorContext,
    CuratorResult,
    GenerateReport,
    MergeSimilar,
)
from .cold_store import (
    _append_to_cold_store,
    _cold_store_path,
    _load_cold_store,
    _prune_cold_store,
    _restore_from_cold,
)
from .helpers import (
    _curator_config,
    _curator_enabled,
    archive_and_delete,
    build_cold_entry,
    is_protected,
    load_last_access,
)
from .report import _persist_report, generate_report

logger = logging.getLogger(__name__)

__all__ = [
    # Entry point
    "_run_curator",
    # Actions
    "ArchiveStale",
    "ArchiveSuperseded",
    "CleanOrphanEdges",
    "CompactChains",
    "CuratorAction",
    "CuratorContext",
    "CuratorResult",
    "GenerateReport",
    "MergeSimilar",
    # Helpers
    "is_protected",
    "build_cold_entry",
    "archive_and_delete",
    "load_last_access",
    # Cold store
    "_cold_store_path",
    "_load_cold_store",
    "_append_to_cold_store",
    "_prune_cold_store",
    "_restore_from_cold",
    # Config
    "_curator_config",
    "_curator_enabled",
    # Report
    "generate_report",
    # Backward compat
    "scan_for_stale",
    "archive_expired",
    "archive_superseded",
    "compact_superseded_chains",
    "scan_for_similar",
    "merge_similar",
    "clean_orphan_edges",
]

# Pipeline ordering per v1.4 design:
#   1. ArchiveStale
#   2. CompactChains (before ArchiveSuperseded so intermediates are compacted first)
#   3. ArchiveSuperseded (remaining deep chains)
#   4. MergeSimilar (detection + merge)
#   5. CleanOrphanEdges
#   6. GenerateReport
_ACTION_CLASSES = [
    ArchiveStale,
    CompactChains,
    ArchiveSuperseded,
    MergeSimilar,
    CleanOrphanEdges,
]


def _ctx_scope_filters(ctx) -> Optional[Dict[str, Optional[str]]]:
    return scope_from_context(ctx)


def _run_curator(
    ctx,
    mem_store,
    filters: Optional[Dict[str, Optional[str]]] = None,
    admin_global: bool = False,
) -> Dict[str, Any]:
    """Run the full curator pipeline. Called from on_session_end.

    Fail-open: all curation failures are caught and logged.
    """
    filters = normalize_scope_filters(filters) if filters is not None else _ctx_scope_filters(ctx)
    scope_label = "global_admin" if admin_global else ("scoped" if filters else "local_global")
    result: Dict[str, Any] = {
        "curator": True,
        "scope": scope_label,
        "filters": filters or {},
        "admin_global": admin_global,
        "stale": 0,
        "archived": 0,
        "superseded": 0,
        "compacted": 0,
        "similar": 0,
        "merged": 0,
        "orphan_edges": 0,
        "total_archived": 0,
        "errors": [],
    }

    pipeline_ctx = CuratorContext(mem_store=mem_store, filters=filters, admin_global=admin_global)
    action_results: List[CuratorResult] = []

    for action_cls in _ACTION_CLASSES:
        action = action_cls()
        if not action.should_run(pipeline_ctx):
            continue
        try:
            r = action.execute(pipeline_ctx)
            action_results.append(r)
            pipeline_ctx.errors.extend(r.errors)
        except Exception as e:
            pipeline_ctx.errors.append(f"{action.name}: {e}")
            logger.warning("Curator action %s failed: %s", action.name, e)

    # Aggregate
    result["stale"] = sum(r.archived for r in action_results if r.action_name == "ArchiveStale")
    result["archived"] = result["stale"]
    result["compacted"] = sum(r.compacted for r in action_results if r.action_name == "CompactChains")
    result["superseded"] = sum(r.archived for r in action_results if r.action_name == "ArchiveSuperseded")
    result["similar"] = sum(r.similar_pairs for r in action_results if r.action_name == "MergeSimilar")
    result["merged"] = sum(r.merged for r in action_results if r.action_name == "MergeSimilar")
    result["orphan_edges"] = sum(r.orphan_edges for r in action_results if r.action_name == "CleanOrphanEdges")
    result["total_archived"] = result["archived"] + result["superseded"] + result["merged"]
    result["errors"] = list(pipeline_ctx.errors)

    # Generate and persist report
    try:
        report_action = GenerateReport()
        report_result = report_action.execute(pipeline_ctx, action_results)
        report_text = getattr(report_result, "report_text", None)
        if report_text:
            result["report"] = report_text
            _persist_report(mem_store, result, report_text, _cold_store_path)
    except Exception as e:
        pipeline_ctx.errors.append(f"report: {e}")
        logger.warning("Curator report generation failed: %s", e)
        result["report"] = generate_report(
            detected_stale=result["stale"],
            archived_stale=result["archived"],
            archived_superseded=result["superseded"],
            similar_pairs=result["similar"],
            errors=result["errors"],
            merged_count=result["merged"],
            compacted_count=result["compacted"],
            orphan_count=result["orphan_edges"],
        )

    result["errors"] = list(pipeline_ctx.errors)
    return result


# ── Backward-compatible thin wrappers ────────────────────────────────


def scan_for_stale(mem_store) -> List[str]:
    """Legacy wrapper: detect expired/stale memory IDs without archiving."""
    ids: List[str] = []
    try:
        cfg = _curator_config(mem_store)
        stale_days = cfg.get("stale", {}).get("days", 90)
        eff_threshold = cfg.get("stale", {}).get("effectiveness_threshold", 0.1)
        import time
        from datetime import datetime, timezone
        now = time.time()
        for mem in mem_store.list_active():
            if is_protected(mem.frontmatter):
                continue
            is_stale = False
            valid_until = getattr(mem.frontmatter, "valid_until", None)
            if valid_until:
                try:
                    if datetime.fromisoformat(valid_until) < datetime.now(timezone.utc):
                        is_stale = True
                except (ValueError, TypeError):
                    pass
            if not is_stale:
                last_access = load_last_access(mem_store, mem.id())
                if last_access > 0 and (now - last_access) > stale_days * 86400:
                    is_stale = True
                else:
                    from .helpers import _load_effectiveness
                    eff = _load_effectiveness(mem_store, mem.id())
                    if eff and eff.get("effectiveness", 0.5) < eff_threshold:
                        is_stale = True
            if is_stale:
                ids.append(mem.id())
    except Exception as e:
        logger.warning("Legacy scan_for_stale wrapper failed: %s", e)
    return ids


def archive_expired(mem_store, memory_ids: List[str]) -> int:
    """Legacy wrapper: archive a list of memory IDs."""
    archived = 0
    for mid in memory_ids:
        mem = mem_store.get(mid)
        if mem is None:
            continue
        entry = build_cold_entry(mem, context_tag="stale")
        success, _ = archive_and_delete(mem_store, mem, entry, "stale")
        if success:
            archived += 1
    return archived


def archive_superseded(mem_store) -> int:
    """Legacy wrapper around ArchiveSuperseded action."""
    action = ArchiveSuperseded()
    ctx = CuratorContext(mem_store=mem_store)
    result = action.execute(ctx)
    return result.archived


def compact_superseded_chains(mem_store) -> int:
    """Legacy wrapper around CompactChains action."""
    action = CompactChains()
    ctx = CuratorContext(mem_store=mem_store)
    result = action.execute(ctx)
    return result.compacted


def scan_for_similar(mem_store) -> List[tuple]:
    """Legacy wrapper around MergeSimilar._scan_for_similar."""
    action = MergeSimilar()
    return action._scan_for_similar(mem_store)


def merge_similar(mem_store) -> int:
    """Legacy wrapper around MergeSimilar action."""
    action = MergeSimilar()
    ctx = CuratorContext(mem_store=mem_store)
    result = action.execute(ctx)
    return result.merged


def clean_orphan_edges(mem_store) -> int:
    """Legacy wrapper around CleanOrphanEdges action."""
    action = CleanOrphanEdges()
    ctx = CuratorContext(mem_store=mem_store)
    result = action.execute(ctx)
    return result.orphan_edges
