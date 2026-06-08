"""Runtime graph compatibility surface backed by GraphIndex."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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

    def get_neighbors(
        self,
        memory_id: str,
        min_weight: float = 0.1,
        limit: int = 20,
        exclude_relations: Optional[List[str]] = None,
    ) -> List[dict]:
        exclude_relations = exclude_relations if exclude_relations is not None else ["SUPERSEDES"]
        conn = self._conn()
        rows = conn.execute(
            """SELECT source_id, target_id, relation, weight, co_occurrence, last_activated
               FROM edges
               WHERE (source_id = ? OR target_id = ?) AND weight >= ?
               ORDER BY weight DESC
               LIMIT ?""",
            (memory_id, memory_id, min_weight, limit),
        ).fetchall()
        results: List[dict] = []
        seen: set = set()
        for r in rows:
            relation = r["relation"] or "co_occurs"
            if relation in exclude_relations:
                continue
            neighbor_id = r["target_id"] if r["source_id"] == memory_id else r["source_id"]
            key = (neighbor_id, relation)
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "memory_id": neighbor_id,
                "target_id": neighbor_id,
                "source_id": memory_id,
                "weight": r["weight"],
                "relation": relation,
                "co_occurrence": r["co_occurrence"],
                "last_activated": r["last_activated"],
            })
        return results

    def get_all_nodes(self, min_weight: float = 0.0) -> List[dict]:
        conn = self._conn()
        if min_weight > 0:
            rows = conn.execute(
                "SELECT DISTINCT memory_id FROM graph_meta WHERE strength >= ?",
                (min_weight,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT DISTINCT memory_id FROM graph_meta").fetchall()
        result = []
        for r in rows:
            mid = r["memory_id"]
            meta = self.get_meta(mid)
            result.append({"memory_id": mid, "zone": meta.get("zone") if meta else None})
        return result

    def get_all_edges(self, min_weight: float = 0.0) -> List[tuple]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT source_id, target_id, weight FROM edges WHERE weight >= ?",
            (min_weight,),
        ).fetchall()
        return [(r["source_id"], r["target_id"], r["weight"]) for r in rows]

    def upsert_edge(
        self,
        source: str,
        target: str,
        relation: str = "co_occurs",
        weight_delta: float = 0.0,
    ) -> None:
        conn = self._conn()
        now = datetime.now(timezone.utc).isoformat()
        if relation == "co_occurs":
            for src, tgt in ((source, target), (target, source)):
                row = conn.execute(
                    "SELECT weight FROM edges WHERE source_id = ? AND target_id = ? AND relation = ?",
                    (src, tgt, relation),
                ).fetchone()
                if row is None:
                    weight = min(1.0, max(0.01, 0.5 + weight_delta))
                    conn.execute(
                        """INSERT INTO edges (source_id, target_id, relation, weight, co_occurrence, last_activated)
                           VALUES (?, ?, ?, ?, 1, ?)""",
                        (src, tgt, relation, weight, now),
                    )
                else:
                    weight = min(1.0, max(0.01, float(row["weight"]) + weight_delta))
                    conn.execute(
                        "UPDATE edges SET weight = ?, co_occurrence = co_occurrence + 1, last_activated = ? "
                        "WHERE source_id = ? AND target_id = ? AND relation = ?",
                        (weight, now, src, tgt, relation),
                    )
        else:
            self.set_edge_weight(source, target, relation=relation, weight=min(1.0, max(0.01, 0.5 + weight_delta)))
        conn.commit()

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

    def get_edges(
        self,
        memory_id: str,
        relation: Optional[str] = None,
        min_weight: float = 0.0,
        limit: int = 20,
    ) -> List[dict]:
        conn = self._conn()
        clauses = ["(source_id = ? OR target_id = ?)", "weight >= ?"]
        params: List[Any] = [memory_id, memory_id, min_weight]
        if relation:
            clauses.append("relation = ?")
            params.append(relation)
        sql = (
            "SELECT source_id, target_id, relation, weight, co_occurrence, last_activated "
            "FROM edges WHERE " + " AND ".join(clauses) + " ORDER BY weight DESC LIMIT ?"
        )
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
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

    def decay_edges(self, decay_rate: float = 0.01, prune_threshold: float = 0.05) -> None:
        conn = self._conn()
        rows = conn.execute(
            "SELECT source_id, target_id, relation, weight FROM edges"
        ).fetchall()
        for r in rows:
            new_weight = float(r["weight"]) * decay_rate
            if new_weight < prune_threshold:
                conn.execute(
                    "DELETE FROM edges WHERE source_id = ? AND target_id = ? AND relation = ?",
                    (r["source_id"], r["target_id"], r["relation"]),
                )
            else:
                conn.execute(
                    "UPDATE edges SET weight = ? WHERE source_id = ? AND target_id = ? AND relation = ?",
                    (new_weight, r["source_id"], r["target_id"], r["relation"]),
                )
        conn.commit()

    def propagate_activation(
        self,
        seed_ids: List[str],
        max_depth: int = 2,
        decay_factor: float = 0.5,
        min_weight: float = 0.1,
        limit: int = 10,
    ) -> List[dict]:
        activation = self.spread_activation(
            seed_ids,
            max_depth=max_depth,
            decay=decay_factor,
            min_weight=min_weight,
            limit=limit,
        )
        return [
            {"memory_id": nid, "weight": act, "relation": "co_occurs"}
            for nid, act in list(activation.items())[:limit]
        ]

    def spread_activation(
        self,
        seed_ids: List[str],
        max_depth: int = 2,
        decay: float = 0.5,
        min_weight: float = 0.1,
        limit: int = 10,
        threshold: float = 1e-4,
        max_iter: Optional[int] = None,
    ) -> Dict[str, float]:
        conn = self._conn()
        steps = max_iter if max_iter is not None else max_depth * 10
        activation: Dict[str, float] = {sid: 1.0 for sid in seed_ids}
        for _ in range(max(1, steps)):
            new_act: Dict[str, float] = {}
            for nid, act in list(activation.items()):
                if act < threshold:
                    continue
                rows = conn.execute(
                    """SELECT source_id, target_id, relation, weight
                       FROM edges
                       WHERE (source_id = ? OR target_id = ?) AND weight >= ?""",
                    (nid, nid, min_weight),
                ).fetchall()
                for r in rows:
                    relation = r["relation"] or "co_occurs"
                    if relation == "SUPERSEDES":
                        continue
                    neighbor = r["target_id"] if r["source_id"] == nid else r["source_id"]
                    if neighbor in seed_ids:
                        continue
                    propagated = act * decay * float(r["weight"])
                    new_act[neighbor] = max(new_act.get(neighbor, 0.0), propagated)
            if not new_act:
                break
            delta = sum(new_act.values())
            for nid, score in new_act.items():
                activation[nid] = max(activation.get(nid, 0.0), score)
            if delta < threshold:
                break
        results: Dict[str, float] = {}
        for nid, act in sorted(activation.items(), key=lambda x: -x[1]):
            if nid in seed_ids:
                continue
            if act < threshold:
                continue
            results[nid] = act
            if len(results) >= limit:
                break
        return results

    def stats(self) -> dict:
        stats = self._gi.stats()
        stats["healthy"] = True
        stats["node_count"] = stats.get("nodes", 0)
        stats["edge_count"] = stats.get("edges", 0)
        return stats

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
        try:
            from ..core.graph import GraphIndex
        except Exception:
            import importlib.util
            import sys
            graph_path = Path(__file__).resolve().parent.parent / "core" / "graph.py"
            spec = importlib.util.spec_from_file_location("mem_reflection_hermes.core.graph", graph_path)
            module = importlib.util.module_from_spec(spec)
            assert spec is not None and spec.loader is not None
            sys.modules.setdefault("mem_reflection_hermes.core.graph", module)
            spec.loader.exec_module(module)
            GraphIndex = module.GraphIndex
        self._gi = GraphIndex(db_path)
        self.store = _GraphStoreShim(self._gi)
        self.associator = _AssociationEngineShim(self._gi, self.store)

    def __getattr__(self, name: str):
        return getattr(self.store, name)

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

    def get_neighbors(
        self,
        memory_id: str,
        min_weight: float = 0.1,
        limit: int = 20,
        exclude_relations: Optional[List[str]] = None,
    ) -> List[dict]:
        return self.store.get_neighbors(memory_id, min_weight, limit, exclude_relations=exclude_relations)

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

    def pagerank(self, **kwargs) -> Dict[str, float]:
        """Expose GraphIndex PageRank through the legacy compat surface."""
        scores = self._gi.pagerank(**kwargs)
        for node in self.store.get_all_nodes():
            scores.setdefault(node["memory_id"], 0.0)
        return scores

    def cross_zone(self, store) -> Dict[str, Any]:
        """Expose GraphIndex cross-zone analysis through the legacy compat surface."""
        return self._gi.cross_zone(store)

    def run_decay(self) -> None:
        self._gi.step_decay()

    def close(self) -> None:
        self._gi.close()


GraphStore = GraphManagerCompat


_graph_manager_compat: Optional[GraphManagerCompat] = None
_gm_compat_lock = __import__("threading").Lock()


def get_graph_manager_compat(db_path: Optional[Path] = None) -> GraphManagerCompat:
    """Get or create the singleton compat manager (thread-safe)."""
    global _graph_manager_compat
    if _graph_manager_compat is None:
        with _gm_compat_lock:
            if _graph_manager_compat is None:
                if db_path is None:
                    from .store import plugin_data_dir
                    db_path = plugin_data_dir() / "graph.db"
                _graph_manager_compat = GraphManagerCompat(db_path)
    return _graph_manager_compat


def _health_recommendations(metrics: Dict[str, Any]) -> List[str]:
    recs = []
    if metrics.get("duplicate_clusters", 0) > 0:
        recs.append(f"Review {metrics['duplicate_clusters']} duplicate memory cluster(s).")
    if metrics.get("longest_supersedes_chain", 0) > 5:
        recs.append(f"Longest supersedes chain ({metrics['longest_supersedes_chain']}) is deep; consider consolidation.")
    if metrics.get("supersedes_cycle_count", 0) > 0:
        recs.append(f"Found {metrics['supersedes_cycle_count']} cycle(s) in supersedes chains; fix immediately.")
    if metrics.get("stale_high_rank_count", 0) > 0:
        recs.append(f"{metrics['stale_high_rank_count']} superseded memories still have high rank; consider re-ranking.")
    if metrics.get("expired_count", 0) > 0:
        recs.append(f"{metrics['expired_count']} memories have passed their valid_until date.")
    if not recs:
        recs.append("Memory store looks healthy.")
    return recs


def register_graph_features(ctx, *, get_mem_store, graph_db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Register graph tools, graph maintenance hook, health tool, and /graph command."""
    if graph_db_path is None:
        try:
            from .store import plugin_data_dir
        except ImportError:
            from store import plugin_data_dir
        graph_db_path = plugin_data_dir() / "graph.db"

    try:
        from . import runtime_hooks as _hooks_mod
    except ImportError:
        try:
            from mem_reflection_hermes import runtime_hooks as _hooks_mod
        except ImportError:
            import runtime_hooks as _hooks_mod

    _hooks_mod._gm_getter_func = get_graph_manager_compat
    _hooks_mod._gm_getter_path = graph_db_path

    gm_ref: Dict[str, Any] = {"instance": None}
    gm_lock = threading.Lock()

    def _ensure_gm():
        if gm_ref["instance"] is None:
            with gm_lock:
                if gm_ref["instance"] is None:
                    gm_ref["instance"] = get_graph_manager_compat(graph_db_path)
        return gm_ref["instance"]

    def _graph_associate_h(args: dict, **kwargs) -> str:
        gm = _ensure_gm()
        mids = args.get("memory_ids", [])[:20]
        mem_store = get_mem_store()
        valid_mids = [mid for mid in mids if mem_store.get(mid) is not None]
        if len(valid_mids) < 2:
            return json.dumps({"error": "At least 2 valid memory IDs required", "valid_ids": valid_mids})
        result = gm.associate_memories(
            valid_mids,
            args.get("context", ""),
            args.get("relation", "co_occurs"),
        )
        return json.dumps({**result, "validated_ids": valid_mids})

    ctx.register_tool(
        name="srh_associate",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_associate",
            "description": "Create graph associations between memories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_ids": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 20},
                    "relation": {"type": "string", "enum": ["co_occurs", "co_used_in_task"], "default": "co_occurs"},
                },
                "required": ["memory_ids"],
            },
        },
        handler=_graph_associate_h,
        description="Associate memories via graph edges",
        emoji="🔗",
    )

    def _graph_retrieve_h(args: dict, **kwargs) -> str:
        gm = _ensure_gm()
        mids = args.get("memory_ids", [])[:20]
        task_type = args.get("task_type", "reasoning")
        max_res = min(args.get("max_results", 10), 100)
        tier = args.get("tier", "list")
        if task_type == "reasoning" and mids and gm.store:
            try:
                zones = {
                    meta.get("zone")
                    for mid in mids
                    for meta in [gm.store.get_meta(mid)]
                    if meta and meta.get("zone")
                }
                for zone in zones:
                    inferred = {
                        "core": "factual",
                        "work": "reasoning",
                        "episode": "recent",
                        "general": "exploration",
                    }.get(zone)
                    if inferred:
                        task_type = inferred
                        break
            except Exception:
                pass
        results = gm.retrieve_related(mids, task_type, max_res, tier=tier)
        return json.dumps({"results": results, "count": len(results), "seed_ids": mids, "tier": tier, "strategy": task_type})

    ctx.register_tool(
        name="srh_graph_retrieve",
        toolset="mem_reflection_hermes",
        schema={
            "name": "srh_graph_retrieve",
            "description": "Retrieve associative memories via co-activation propagation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
                    "task_type": {"type": "string", "enum": ["factual", "reasoning", "skill", "recent", "exploration", "personalized"], "default": "reasoning"},
                    "max_results": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
                    "tier": {"type": "string", "enum": ["count", "list", "detail"], "default": "list"},
                },
                "required": ["memory_ids"],
            },
        },
        handler=_graph_retrieve_h,
        description="Retrieve graph-related memories",
        emoji="🕸️",
    )

    def _graph_stats_h(args: dict, **kwargs) -> str:
        stats = _ensure_gm().get_stats()
        stats["graph_semantics"] = "associative_coactivation"
        return json.dumps(stats)

    ctx.register_tool(
        name="srh_graph_stats",
        toolset="mem_reflection_hermes",
        schema={"name": "srh_graph_stats", "description": "Get associative graph statistics.", "parameters": {"type": "object", "properties": {}}},
        handler=_graph_stats_h,
        description="Get graph memory statistics",
        emoji="📊",
    )

    def _graph_viz_h(args: dict, **kwargs) -> str:
        gm = _ensure_gm()
        stats = gm.get_stats(tier="detail")
        if stats.get("node_count", 0) == 0:
            return json.dumps({"nodes": [], "edges": [], "stats": stats})
        try:
            with gm.store._connect() as conn:
                nodes = conn.execute(
                    "SELECT memory_id AS id, zone, importance, strength, status, access_count FROM graph_meta "
                    "WHERE strength > 0 ORDER BY importance DESC LIMIT 200"
                ).fetchall()
                edges = conn.execute(
                    "SELECT source_id AS source, target_id AS target, relation, weight FROM edges "
                    "WHERE weight >= 0.1 ORDER BY weight DESC LIMIT 500"
                ).fetchall()
                return json.dumps({
                    "nodes": [dict(r) for r in nodes],
                    "edges": [dict(r) for r in edges],
                    "stats": {**stats, "graph_semantics": "associative_coactivation"},
                })
        except Exception as exc:
            return json.dumps({"error": str(exc), "stats": stats})

    ctx.register_tool(
        name="srh_graph_viz",
        toolset="mem_reflection_hermes",
        schema={"name": "srh_graph_viz", "description": "Get graph visualization data.", "parameters": {"type": "object", "properties": {"tier": {"type": "string", "enum": ["summary", "detail"], "default": "summary"}}}},
        handler=_graph_viz_h,
        description="Graph viz data for dashboard",
        emoji="🕸️",
    )

    def _memory_health_h(args: dict, **kwargs) -> str:
        metrics = get_mem_store().health_metrics()
        return json.dumps({"health": metrics, "recommendations": _health_recommendations(metrics)})

    ctx.register_tool(
        name="srh_memory_health",
        toolset="mem_reflection_hermes",
        schema={"name": "srh_memory_health", "description": "Get memory health metrics.", "parameters": {"type": "object", "properties": {}}},
        handler=_memory_health_h,
        description="Get memory health metrics and recommendations",
        emoji="🏥",
    )

    def _post_tool_associate(**kwargs) -> None:
        try:
            tool_name = kwargs.get("tool_name", "")
            if tool_name not in ("srh_memory_write", "srh_memory_delete"):
                return None
            gm = _ensure_gm()
            args = kwargs.get("args", {})
            result = kwargs.get("result", {})
            if tool_name == "srh_memory_write":
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except json.JSONDecodeError:
                        result = {}
                if not result.get("success") or not result.get("id"):
                    return None
                memory_id = result["id"]
                gm.store.ensure_meta(memory_id, zone=args.get("zone", "general"))
                gm.store.record_access(memory_id)
                supersedes_ids = args.get("supersedes", [])
                if supersedes_ids and isinstance(supersedes_ids, list):
                    for old_id in supersedes_ids:
                        gm.store.update_importance(old_id, delta=-0.9)
                        conn = gm.store._connect()
                        conn.execute(
                            "UPDATE graph_meta SET strength=0, status='superseded' WHERE memory_id=?",
                            (old_id,),
                        )
                        for edge in gm.store.get_edges(old_id):
                            src, tgt = edge["source_id"], edge["target_id"]
                            neighbor = tgt if src == old_id else src
                            gm.store.set_edge_weight(memory_id, neighbor, relation=edge.get("relation", "co_occurs"), weight=edge.get("weight", 0.5) * 0.3)
                        conn.commit()
            elif tool_name == "srh_memory_delete":
                mem_id = args.get("id", "")
                if mem_id:
                    gm.store.update_importance(mem_id, delta=-0.9)
                    conn = gm.store._connect()
                    conn.execute(
                        "UPDATE graph_meta SET strength=0, status='deleted' WHERE memory_id=?",
                        (mem_id,),
                    )
                    conn.commit()
                    gm.store.decay_edges(decay_rate=0.9)
        except Exception as exc:
            logger.debug("graph runtime auto-associate: %s", exc)
        return None

    ctx.register_hook("post_tool_call", _post_tool_associate)

    def _slash_graph(raw_args: str) -> str:
        gm = _ensure_gm()
        parts = raw_args.strip().split()
        cmd = parts[0] if parts else "stats"
        if cmd == "stats":
            stats = gm.get_stats(tier="detail")
            return (
                f"📊 **Graph Memory Stats**\n"
                f"- Nodes: {stats['node_count']}\n"
                f"- Edges: {stats['edge_count']}\n"
                f"- Avg Weight: {stats['avg_weight']}\n"
                f"- DB: {stats['db_path']}"
            )
        if cmd == "decay":
            gm.run_decay()
            return "🧹 Decay cycle completed on all graph edges and memory strengths."
        if cmd == "associate" and len(parts) >= 3:
            result = gm.associate_memories(parts[1:])
            return f"🔗 Associated {len(parts) - 1} memories ({result['edges_created']} edges created/updated)"
        return "Usage: /graph [stats|decay|associate <id1> <id2> ...]"

    ctx.register_command(
        name="graph",
        handler=_slash_graph,
        description="Graph memory operations: stats, decay, associate",
        args_hint="[stats|decay|associate <id1> <id2> ...]",
    )

    return {"graph_db_path": graph_db_path, "get_graph_manager": get_graph_manager_compat}


# Public aliases for __init__.py register() to import
def srh_associate(args: dict, **kwargs) -> str:
    from .. import _get_mem_store, _get_graph_mgr
    gm = _get_graph_mgr()
    mids = args.get("memory_ids", [])[:20]
    mem_store = _get_mem_store()
    valid_mids = [mid for mid in mids if mem_store.get(mid) is not None]
    if len(valid_mids) < 2:
        return json.dumps({"error": "At least 2 valid memory IDs required", "valid_ids": valid_mids})
    result = gm.associate_memories(valid_mids, args.get("context", ""), args.get("relation", "co_occurs"))
    return json.dumps({**result, "validated_ids": valid_mids})


def srh_graph_retrieve(args: dict, **kwargs) -> str:
    from .. import _get_graph_mgr
    gm = _get_graph_mgr()
    seed_ids = args.get("seed_ids", [])
    max_results = int(args.get("max_results", 10))
    tier = args.get("tier", "count")
    results = gm.retrieve(seed_ids, max_results=max_results, tier=tier)
    return json.dumps({"results": results}, ensure_ascii=False)


def srh_graph_stats(args: dict, **kwargs) -> str:
    from .. import _get_graph_mgr
    gm = _get_graph_mgr()
    stats = gm.stats()
    return json.dumps(stats, ensure_ascii=False)


def srh_graph_viz(args: dict, **kwargs) -> str:
    from .. import _get_graph_mgr
    gm = _get_graph_mgr()
    fmt = args.get("format", "adjacency")
    depth = int(args.get("depth", 2))
    viz = gm.visualize(format=fmt, depth=depth)
    return json.dumps(viz, ensure_ascii=False)


def srh_memory_health(args: dict, **kwargs) -> str:
    from .. import _get_mem_store, _get_graph_mgr
    mem_store = _get_mem_store()
    gm = _get_graph_mgr()
    stats = gm.stats()
    total = stats.get("total_edges", 0)
    return json.dumps({
        "graph_edges": total,
        "memories_indexed": len(mem_store.list_active()),
        "status": "healthy" if total > 0 else "empty",
    }, ensure_ascii=False)
