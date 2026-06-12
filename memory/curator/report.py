"""Report generation for curator pipeline runs."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Resolve the shared late-binding helper safely for both package and standalone
# module loading.
try:
    from mem_reflection_hermes.runtime._lb import _lb as _lb_fn
except ImportError:
    _lb_fn = None
if _lb_fn is None:
    try:
        from runtime._lb import _lb as _lb_fn
    except ImportError:
        _lb_fn = None

# Intra-curator imports: use normal relative imports in package mode; provide
# minimal standalone fallbacks so the module can still be loaded directly.
try:
    from .helpers import CuratorContext, CuratorResult
except ImportError:
    from dataclasses import dataclass, field
    from typing import Any as _AnyT

    @dataclass
    class CuratorContext:  # type: ignore[no-redef]
        mem_store: _AnyT
        errors: List[str] = field(default_factory=list)

    @dataclass
    class CuratorResult:  # type: ignore[no-redef]
        action_name: str
        archived: int = 0
        compacted: int = 0
        merged: int = 0
        similar_pairs: int = 0
        orphan_edges: int = 0
        errors: List[str] = field(default_factory=list)


def generate_report(
    detected_stale: int,
    archived_stale: int,
    archived_superseded: int,
    similar_pairs: int,
    errors: List[str],
    merged_count: int = 0,
    compacted_count: int = 0,
    orphan_count: int = 0,
) -> str:
    """Generate a text summary of curator actions for the reflection log."""
    parts: List[str] = []
    if detected_stale:
        parts.append(f"stale: {detected_stale} detected, {archived_stale} archived")
    elif archived_stale:
        parts.append(f"stale: {archived_stale} archived")
    if archived_superseded:
        parts.append(f"superseded: {archived_superseded} archived")
    if compacted_count:
        parts.append(f"compacted: {compacted_count} archived")
    if similar_pairs:
        parts.append(f"similar: {similar_pairs} candidate pair(s) found")
    if merged_count:
        parts.append(f"merged: {merged_count} archived")
    if orphan_count:
        parts.append(f"orphan edges: {orphan_count} cleaned")
    if errors:
        parts.append(f"errors: {len(errors)}")
    if not parts:
        return "No curator actions"
    return f"curator: {', '.join(parts)}"


def _persist_report(
    mem_store,
    results: Dict[str, Any],
    report_text: str,
    cold_store_path_fn,
) -> None:
    """Persist curator report to JSON file and reflection log."""
    try:
        cold_path = cold_store_path_fn(mem_store)
        report_path = cold_path.parent / "curator-report.json"
        report_path.write_text(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "report": report_text,
                    "stale": results.get("stale", 0),
                    "archived": results.get("archived", 0),
                    "superseded": results.get("superseded", 0),
                    "compacted": results.get("compacted", 0),
                    "similar": results.get("similar", 0),
                    "merged": results.get("merged", 0),
                    "orphan_edges": results.get("orphan_edges", 0),
                    "total_archived": results.get("total_archived", 0),
                    "errors": results.get("errors", []),
                },
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Failed to write curator report file: %s", e)

    reflect_rt = (
        _lb_fn("mem_reflection_hermes.reflection.runtime") if _lb_fn is not None else None
    )
    if reflect_rt is None:
        reflect_rt = _lb_fn("reflection.runtime") if _lb_fn is not None else None

    if reflect_rt is None or not hasattr(reflect_rt, "_append_reflect_log"):
        logger.debug("Reflection runtime unavailable; skipping curator log append")
        return

    try:
        reflect_rt._append_reflect_log(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": "curator",
                "summary": report_text,
                "stale": results.get("stale", 0),
                "archived": results.get("archived", 0),
                "superseded": results.get("superseded", 0),
                "compacted": results.get("compacted", 0),
                "similar": results.get("similar", 0),
                "merged": results.get("merged", 0),
                "orphan_edges": results.get("orphan_edges", 0),
                "errors": results.get("errors", []),
            }
        )
    except Exception as e:
        logger.warning("Failed to append curator report to reflection log: %s", e)
