"""graph/compat.py — Backward-compatible wrapper around new GraphIndex.

Exposes the legacy ahe_graph GraphMemoryManager / GraphStore shape so
__init__.py, hooks/lifecycle.py, and dashboard/plugin_api.py keep working
while the underlying storage moves to the new graph.db schema.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _as_dict(row):
    """Convert sqlite3.Row or any mapping to a plain dict."""
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


class _GraphStoreShim:
    """Shim exposing legacy GraphStore methods on top of GraphIndex."""

    def __init__(self, graph_index):
        self._gi = graph_index

    def _conn(self):
        return self._gi._get_conn()

    def get_neighbors(self, memory_id: str, min_weight: float = 0.1, limit: int = 20) -> List[dict]:
        return [
            {
                "memory_id": n["memory_id"],
                "target_id": n["memory_id"],
                "weight": n["weight"],
                "relation": "co_occurs",
                "co_occurrence": n.get("co_occurrence", 1),
                "last_activated": n.get("last_activated"),
            }
            for n in self._gi.neighbors(memory_id, min_weight=min_weight, limit=limit)
        ]

    def get_all_nodes(self, min_weight: float = 0.0) -> List[dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT DISTINCT memory_id FROM graph_meta"
        ).fetchall()
        return [{"memory_id": r["memory_id"]} for r in rows]

    def get_all_edges(self, min_weight: float = 0.0) -> List[tuple]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT source_id, target_id, weight FROM edges WHERE weight >= ?",
            (min_weight,),
        ).fetchall()
        return [(r["source_id"], r["target_id"], r["weight"]) for r in rows]

    def ensure_meta(self, memory_id: str, zone: str = "general", importance: float = 0.5) -> None:
        self._gi.ensure_meta(memory_id, zone=zone)
        conn = self._conn()
        conn.execute(
            "UPDATE graph_meta SET importance = ? WHERE memory_id = ?",
            (importance, memory_id),
        )
        conn.commit()

    def get_meta(self, memory_id: str) -> Optional[dict]:
        conn = self._conn()
        row = conn.execute(
            "SELECT memory_id, zone, access_count, last_access, importance, strength, status "
            "FROM graph_meta WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        return _as_dict(row)

    def record_access(self, memory_id: str, context: str = "") -> None:
        self.ensure_meta(memory_id)
        conn = self._conn()
        conn.execute(
            "UPDATE graph_meta SET access_count = access_count + 1, last_access = ? "
            "WHERE memory_id = ?",
            (datetime.now(timezone.utc).isoformat(), memory_id),
        )
        conn.commit()

    def update_importance(self, memory_id: str, delta: float) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE graph_meta SET importance = max(0.0, min(1.0, importance + ?)) "
            "WHERE memory_id = ?",
            (delta, memory_id),
        )
        conn.commit()

    def get_edges(self, memory_id: str) -> List[dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT source_id, target_id, relation, weight, co_occurrence, last_activated "
            "FROM edges WHERE source_id = ? OR target_id = ?",
            (memory_id, memory_id),
        ).fetchall()
        return [_as_dict(r) for r in rows]

    def set_edge_weight(self, source: str, target: str, relation: str = "co_occurs", weight: float = 0.5) -> None:
        conn = self._conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO edges (source_id, target_id, relation, weight, co_occurrence, last_activated)
               VALUES (?, ?, ?, ?, 1, ?)
               ON CONFLICT(source_id, target_id, relation) DO UPDATE SET
                 weight = excluded.weight,
                 last_activated = excluded.last_activated""",
            (source, target, relation, weight, now),
        )
        # Symmetric edge
        conn.execute(
            """INSERT INTO edges (source_id, target_id, relation, weight, co_occurrence, last_activated)
               VALUES (?, ?, ?, ?, 1, ?)
               ON CONFLICT(source_id, target_id, relation) DO UPDATE SET
                 weight = excluded.weight,
                 last_activated = excluded.last_activated""",
            (target, source, relation, weight, now),
        )
        conn.commit()

    def decay_edges(self, decay_rate: float = 0.01) -> None:
        # New schema uses step_decay; approximate legacy behavior
        self._gi.step_decay()

    def propagate_activation(
        self,
        seed_ids: List[str],
        max_depth: int = 2,
        decay_factor: float = 0.5,
        min_weight: float = 0.1,
        limit: int = 10,
    ) -> List[dict]:
        activation = self._gi.spread(seed_ids, decay=decay_factor, max_iter=max_depth * 10)
        # Convert to legacy result shape
        results = []
        seen = set(seed_ids)
        for nid, act in sorted(activation.items(), key=lambda x: -x[1]):
            if nid in seen:
                continue
            if act < min_weight:
                continue
            results.append({"memory_id": nid, "weight": act, "relation": "co_occurs"})
            seen.add(nid)
            if len(results) >= limit:
                break
        return results

    def stats(self) -> dict:
        return self._gi.stats()

    def _connect(self):
        return self._conn()


class _AssociationEngineShim:
    """Shim exposing legacy AssociationEngine methods."""

    def __init__(self, graph_index, store_shim):
        self._gi = graph_index
        self._store = store_shim

    def on_co_occurrence(self, memory_ids: List[str], context: str = "") -> int:
        return self._gi.associate(memory_ids, context=context)

    def on_memory_coactivation(self, memory_ids: List[str]) -> int:
        return self._gi.associate(memory_ids)

    def on_task_association(self, memory_ids: List[str], task_zone: str = "general") -> int:
        return self._gi.associate(memory_ids, context=task_zone)

    def decay_all_edges(self) -> None:
        self._gi.step_decay()


class GraphManagerCompat:
    """Backward-compatible GraphMemoryManager backed by new GraphIndex."""

    def __init__(self, db_path: Path):
        # Import lazily to avoid circular imports at module load time
        from ..graph import GraphIndex
        self._gi = GraphIndex(db_path)
        self.store = _GraphStoreShim(self._gi)
        self.associator = _AssociationEngineShim(self._gi, self.store)

    def record_access(self, memory_id: str, context: str = "") -> None:
        self.store.record_access(memory_id, context)

    def associate_memories(self, memory_ids: List[str], context: str = "",
                           relation: str = "co_occurs") -> dict:
        count = self._gi.associate(memory_ids, context=context)
        return {
            "memory_ids": memory_ids,
            "edges_created": count,
            "relation": relation,
            "context": context[:100] if context else "",
        }

    def retrieve_related(self, memory_ids: List[str], task_type: str = "reasoning",
                         max_results: int = 10, tier: str = "list") -> List[dict]:
        results = self.store.propagate_activation(
            memory_ids,
            max_depth=2,
            decay_factor=0.5,
            min_weight=0.1,
            limit=max_results,
        )
        if tier == "count":
            return [{"memory_id": r["memory_id"]} for r in results[:max_results]]
        if tier == "list":
            return [
                {"memory_id": r["memory_id"], "relation": r.get("relation", ""),
                 "weight": round(r.get("weight", 0), 3)}
                for r in results[:max_results]
            ]
        return results[:max_results]

    def propagate_activation(self, seed_ids: List[str], **kwargs) -> List[dict]:
        return self.store.propagate_activation(seed_ids, **kwargs)

    def get_neighbors(self, memory_id: str, min_weight: float = 0.1, limit: int = 20) -> List[dict]:
        return self.store.get_neighbors(memory_id, min_weight, limit)

    def get_stats(self, tier: str = "summary") -> dict:
        stats = self.store.stats()
        if tier == "summary":
            return {
                "nodes": stats.get("nodes", 0),
                "edges": stats.get("edges", 0),
                "avg_weight": stats.get("avg_weight", 0.0),
            }
        return {
            "node_count": stats.get("nodes", 0),
            "edge_count": stats.get("edges", 0),
            "avg_weight": stats.get("avg_weight", 0.0),
            "db_path": str(self._gi._db_path),
        }

    def run_decay(self) -> None:
        self._gi.step_decay()

    def close(self) -> None:
        self._gi.close()


_graph_manager_compat: Optional[GraphManagerCompat] = None
_gm_compat_lock = __import__("threading").Lock()


def get_graph_manager_compat(db_path: Optional[Path] = None) -> GraphManagerCompat:
    """Get or create the singleton compat manager (thread-safe)."""
    global _graph_manager_compat
    if _graph_manager_compat is None:
        with _gm_compat_lock:
            if _graph_manager_compat is None:
                if db_path is None:
                    from ..store import plugin_data_dir
                    db_path = plugin_data_dir() / "graph.db"
                _graph_manager_compat = GraphManagerCompat(db_path)
    return _graph_manager_compat
