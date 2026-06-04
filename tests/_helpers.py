"""_helpers.py — Shared test utility functions.

Importable from any test file via: from _helpers import make_memory, ...
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

# Ensure project root is importable
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Use store.py dataclasses directly (leaf module, no relative imports).
# core.py's MemoryFrontmatter lacks to_dict() needed by store.py's write_memory_atomic.
from store import LoadedMemory, MemoryFrontmatter, MemoryEffectiveness


def make_memory(
    body: str,
    zone: str = "general",
    age_days: float = 0.0,
    tags: Optional[List[str]] = None,
    supersedes: Optional[List[str]] = None,
    source: str = "test",
    confidence: str = "medium",
    mem_id: Optional[str] = None,
) -> LoadedMemory:
    """Build a LoadedMemory with deterministic created timestamp."""
    fm = MemoryFrontmatter(
        id=mem_id or f"test-{id(body) % 100000:05d}",
        created=_age_to_iso(age_days),
        source=source,
        confidence=confidence,
        tags=list(tags or []),
        supersedes=list(supersedes or []),
        zone=zone,
    )
    return LoadedMemory(
        frontmatter=fm,
        body=body,
        scope="user",
        source_path=Path(f"/tmp/test_mem_{fm.id}.md"),
    )


def make_memory_with_id(
    mem_id: str,
    body: str,
    zone: str = "general",
    age_days: float = 0.0,
    tags: Optional[List[str]] = None,
    supersedes: Optional[List[str]] = None,
) -> LoadedMemory:
    """Build a LoadedMemory with a specific ID."""
    return make_memory(
        body=body, zone=zone, age_days=age_days,
        tags=tags, supersedes=supersedes, mem_id=mem_id,
    )


def effectiveness_for(
    memory_id: str,
    loaded: int = 1,
    referenced: int = 0,
    accessed: int = 0,
    last_event_days_ago: float = 0.0,
) -> MemoryEffectiveness:
    """Build a MemoryEffectiveness with deterministic last_event_at."""
    last_at = None
    if last_event_days_ago >= 0 and (loaded > 0 or referenced > 0 or accessed > 0):
        last_at = _age_to_iso(last_event_days_ago)
    return MemoryEffectiveness(
        loaded=loaded,
        referenced=referenced,
        accessed=accessed,
        last_event_at=last_at,
    )


def _age_to_iso(age_days: float) -> str:
    """Convert age in days to an ISO-8601 timestamp (now minus age_days)."""
    dt = datetime.now(timezone.utc) - timedelta(days=age_days)
    return dt.isoformat()
