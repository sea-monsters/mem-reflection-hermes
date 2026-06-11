"""_helpers.py — Shared test utility functions.

Importable from any test file via: from _helpers import make_memory, ...
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional

# Ensure project root is importable
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Use store.py dataclasses directly (leaf module, no relative imports).
# core.py's MemoryFrontmatter lacks to_dict() needed by store.py's write_memory_atomic.
from core.store import LoadedMemory, MemoryFrontmatter, MemoryEffectiveness


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
    confidence: str = "medium",
) -> LoadedMemory:
    """Build a LoadedMemory with a specific ID."""
    return make_memory(
        body=body, zone=zone, age_days=age_days,
        tags=tags, supersedes=supersedes, mem_id=mem_id,
        confidence=confidence,
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


# ---------------------------------------------------------------------------
# Lightweight mock classes for curator / pipeline tests
# ---------------------------------------------------------------------------

class MockFrontmatter:
    """Minimal frontmatter stand-in for tests that need Memory-like objects."""

    def __init__(
        self,
        mem_id: str,
        body: str = "",
        zone: str = "general",
        created: str = "",
        confidence: str = "medium",
        pinned: bool = False,
        tags: list | None = None,
        supersedes: list | None = None,
        valid_until: str = "",
    ):
        self.id_val = mem_id
        self.zone = zone
        self.created = created or datetime.now(timezone.utc).isoformat()
        self.confidence = confidence
        self.pinned = pinned
        self.tags = tags or []
        self.supersedes = supersedes or []
        self.valid_until = valid_until
        self.supersedes_reason = ""
        self._body = body

    def id(self) -> str:
        return self.id_val


class MockMemory:
    """Minimal memory stand-in for curator / pipeline tests."""

    def __init__(
        self,
        mid: str,
        body: str,
        zone: str = "general",
        created: str = "",
        confidence: str = "medium",
        pinned: bool = False,
        tags: list | None = None,
        supersedes: list | None = None,
        valid_until: str = "",
    ):
        self.id_val = mid
        self.body = body
        self.scope = "user"
        self.frontmatter = MockFrontmatter(
            mid, body, zone, created, confidence, pinned, tags, supersedes, valid_until
        )

    def id(self) -> str:
        return self.id_val


class MockStore:
    """Minimal store stand-in for curator / pipeline tests.

    Supports get/put/delete/list/list_active/update/is_superseded/latest_for.
    """

    def __init__(self):
        self.memories: dict[str, MockMemory] = {}
        self.deleted: list[str] = []
        self.eff_data: dict[str, dict[str, Any]] = {}
        self._cold_store: list[dict[str, Any]] = []
        self._plugin_config_override: dict[str, Any] = {}

    def list_active(self) -> list[MockMemory]:
        return list(self.memories.values())

    def get(self, mid: str):
        return self.memories.get(mid)

    def put(self, scope: str, fm, body: str):
        self.memories[fm.id] = MockMemory(
            fm.id, body,
            zone=getattr(fm, "zone", "general"),
            tags=getattr(fm, "tags", []),
        )

    def list(self, *, zone=None, active_only: bool = False, sort: str = "rank", limit=None,
             filters=None):
        mems = list(self.memories.values())
        if zone:
            mems = [m for m in mems if m.frontmatter.zone == zone]
        if filters:
            for key, val in filters.items():
                mems = [m for m in mems if getattr(m.frontmatter, key, None) == val]
        if limit is not None:
            mems = mems[:limit]
        return mems

    def delete(self, scope: str, mid: str) -> bool:
        if mid in self.memories:
            del self.memories[mid]
            self.deleted.append(mid)
            return True
        return False

    def list_active_effectiveness(self) -> dict[str, dict[str, Any]]:
        return self.eff_data

    def update(self, mem_id, body=None, zone=None, confidence=None,
               tags=None, pinned=None, supersedes=None):
        mem = self.memories.get(mem_id)
        if mem is None:
            return None
        if body is not None:
            mem.body = body
        if zone is not None:
            mem.frontmatter.zone = zone
        if confidence is not None:
            mem.frontmatter.confidence = confidence
        if tags is not None:
            mem.frontmatter.tags = tags
        if pinned is not None:
            mem.frontmatter.pinned = pinned
        if supersedes is not None:
            mem.frontmatter.supersedes = supersedes
        return mem

    def is_superseded(self, mem_id: str) -> bool:
        for mem in self.memories.values():
            if mem_id in (mem.frontmatter.supersedes or []):
                return True
        return False

    def latest_for(self, mem_id: str):
        visited: set = set()
        current = mem_id
        while current not in visited:
            visited.add(current)
            next_id = None
            for mem in self.memories.values():
                if current in (mem.frontmatter.supersedes or []):
                    next_id = mem.id()
                    break
            if next_id is None:
                break
            current = next_id
        return self.memories.get(current)


def _age_to_iso(age_days: float) -> str:
    """Convert age in days to an ISO-8601 timestamp (now minus age_days)."""
    dt = datetime.now(timezone.utc) - timedelta(days=age_days)
    return dt.isoformat()
