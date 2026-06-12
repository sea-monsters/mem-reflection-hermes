"""Thin method bodies extracted from MemoryStore to keep core/store.py lean."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from .entities import entity_enabled, extract_entities
from .tokenization import _tokenise

if TYPE_CHECKING:
    from .models import LoadedMemory, MemoryEffectiveness
    from .store import MemoryStore


def entity_links_for_memory(store: "MemoryStore", memory_id: str) -> List[Dict[str, Any]]:
    conn = store._get_conn()
    rows = conn.execute(
        """SELECT e.text, e.normalized, e.type, l.weight, l.source
           FROM entity_links l
           JOIN entities e ON e.id = l.entity_id
           WHERE l.memory_id = ?
           ORDER BY l.weight DESC, e.text ASC""",
        (memory_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def compute_entity_boosts(
    store: "MemoryStore",
    query: str,
    candidate_ids: Optional[Set[str]] = None,
) -> Tuple[Dict[str, float], Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    if not entity_enabled():
        return {}, {}, []
    extracted = extract_entities(query)
    if not extracted:
        return {}, {}, []
    normalized_terms = [e["normalized"] for e in extracted]
    conn = store._get_conn()
    placeholders = ", ".join("?" for _ in normalized_terms)
    rows = conn.execute(
        f"""SELECT l.memory_id, l.weight, l.source, e.text, e.normalized, e.type
            FROM entity_links l
            JOIN entities e ON e.id = l.entity_id
            WHERE e.normalized IN ({placeholders})""",
        normalized_terms,
    ).fetchall()
    boosts: Dict[str, float] = {}
    hits: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        mid = row["memory_id"]
        if candidate_ids is not None and mid not in candidate_ids:
            continue
        hit = {"text": row["text"], "normalized": row["normalized"],
               "type": row["type"], "weight": float(row["weight"]), "source": row["source"]}
        hits.setdefault(mid, []).append(hit)
        boosts[mid] = boosts.get(mid, 0.0) + float(row["weight"])
    return boosts, hits, extracted


def latest_for(store: "MemoryStore", mem_id: str) -> Optional["LoadedMemory"]:
    conn = store._get_conn()
    current = mem_id
    visited: Set[str] = set()
    while current and current not in visited:
        visited.add(current)
        row = conn.execute(
            """SELECT s.new_id
               FROM supersedes s
               JOIN memories m ON m.id = s.new_id
               WHERE s.old_id = ?
               ORDER BY m.created DESC, m.version DESC, m.rank DESC, s.new_id DESC
               LIMIT 1""",
            (current,),
        ).fetchone()
        if row is None:
            break
        current = row["new_id"]
    if current == mem_id and store.is_superseded(mem_id):
        return None
    return store.get(current)


def lineage_chain(store: "MemoryStore", mem_id: str, max_depth: int = 10) -> List["LoadedMemory"]:
    conn = store._get_conn()
    backward: List[str] = [mem_id]
    cur = mem_id
    bv: Set[str] = set()
    while cur not in bv and len(backward) < max_depth:
        bv.add(cur)
        row = conn.execute("SELECT old_id FROM supersedes WHERE new_id = ?", (cur,)).fetchone()
        if row is None:
            break
        cur = row["old_id"]
        backward.append(cur)
    backward.reverse()
    chain: List["LoadedMemory"] = []
    fc = backward[0]
    fv: Set[str] = set()
    depth = 0
    while fc not in fv and depth < max_depth:
        fv.add(fc)
        m = store.get(fc)
        if m is not None:
            chain.append(m)
        row = conn.execute(
            """SELECT s.new_id
               FROM supersedes s
               JOIN memories m ON m.id = s.new_id
               WHERE s.old_id = ?
               ORDER BY m.created DESC, m.version DESC, m.rank DESC, s.new_id DESC
               LIMIT 1""",
            (fc,),
        ).fetchone()
        if row is None:
            break
        fc = row["new_id"]
        depth += 1
    return chain


def effectiveness(
    store: "MemoryStore",
    memory_id: Optional[str] = None,
) -> Dict[str, "MemoryEffectiveness"]:
    from .models import MemoryEffectiveness
    conn = store._get_conn()
    if memory_id is not None:
        rows = conn.execute("SELECT event, at FROM stats WHERE memory_id = ?", (memory_id,)).fetchall()
        eff = MemoryEffectiveness()
        for r in rows:
            ev, at = r["event"], r["at"]
            if ev == "loaded":
                eff.loaded += 1
            elif ev == "referenced":
                eff.referenced += 1
            elif ev == "accessed":
                eff.accessed += 1
            if at and (eff.last_event_at is None or at > eff.last_event_at):
                eff.last_event_at = at
        return {memory_id: eff}
    rows = conn.execute("SELECT memory_id, event, at FROM stats").fetchall()
    result: Dict[str, MemoryEffectiveness] = {}
    for r in rows:
        mid, ev, at = r["memory_id"], r["event"], r["at"]
        e = result.setdefault(mid, MemoryEffectiveness())
        if ev == "loaded":
            e.loaded += 1
        elif ev == "referenced":
            e.referenced += 1
        elif ev == "accessed":
            e.accessed += 1
        if at and (e.last_event_at is None or at > e.last_event_at):
            e.last_event_at = at
    return result


def record_stat(store: "MemoryStore", memory_id: str, event: str) -> None:
    with store._lock:
        conn = store._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT INTO stats (memory_id, event, at) VALUES (?, ?, ?)",
                     (memory_id, event, now))
        conn.commit()
