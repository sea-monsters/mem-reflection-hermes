"""Memory lineage helpers for mem-reflection-hermes.

Functions for walking supersedes chains and detecting cycles.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Set

if TYPE_CHECKING:
    from .models import LoadedMemory
    from .store import MemoryStore


def _lineage_latest(store: "MemoryStore", mem_id: str) -> Optional[str]:
    current = mem_id
    visited: Set[str] = set()
    while current not in visited:
        visited.add(current)
        successor = None
        for m in store.list():
            if current in (m.frontmatter.supersedes or []):
                successor = m.id()
                break
        if successor is None:
            break
        current = successor
    return current if current != mem_id else None


def _lineage_root(store: "MemoryStore", mem_id: str) -> str:
    current = mem_id
    visited: Set[str] = set()
    while current not in visited:
        visited.add(current)
        m = store.get(current)
        if m is None or not m.frontmatter.supersedes:
            break
        current = m.frontmatter.supersedes[0]
    return current


def _lineage_depth(store: "MemoryStore", mem_id: str, visited: Optional[Set[str]] = None) -> int:
    if visited is None:
        visited = set()
    if mem_id in visited:
        return 0
    visited.add(mem_id)
    m = store.get(mem_id)
    if m is None or not m.frontmatter.supersedes:
        return 0
    parent = m.frontmatter.supersedes[0]
    return 1 + _lineage_depth(store, parent, visited)


def _lineage_cycle_check(store: "MemoryStore", mem_id: str) -> Optional[List[str]]:
    path: List[str] = []
    current = mem_id
    while current is not None:
        if current in path:
            cycle_start = path.index(current)
            return path[cycle_start:] + [current]
        path.append(current)
        m = store.get(current)
        if m is None or not m.frontmatter.supersedes:
            break
        current = m.frontmatter.supersedes[0]
    return None


def _calc_supersedes_depth(
    store: "MemoryStore",
    mem_id: str,
    visited: Optional[Set[str]] = None,
    max_depth: int = 10,
    depth: int = 0,
) -> int:
    if visited is None:
        visited = set()
    if mem_id in visited or depth >= max_depth:
        return depth
    visited.add(mem_id)
    conn = store._get_conn()
    row = conn.execute(
        "SELECT old_id FROM supersedes WHERE new_id = ? LIMIT 1", (mem_id,)
    ).fetchone()
    if row is None:
        return depth
    return _calc_supersedes_depth(store, row["old_id"], visited, max_depth, depth + 1)
