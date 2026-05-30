"""ahe_graph — Graph memory enhancement for mem-reflection-hermes.

Ported from AHE OARSM memory system components:
- graph_store.py: SQLite-backed association edges with Hebbian weights
- association_engine.py: Hebbian co-occurrence learning
- decay_engine.py: Ebbinghaus forgetting curve (half-life model)
- retrieval_router.py: Adaptive multi-strategy retrieval

Integrates with the existing mem-reflection-hermes memory system,
adding graph-level associations on top of the flat file memories.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ======================================================================
# GraphStore Protocol — interface contract for graph memory consumers
# ======================================================================
# Prevents method-naming drift (copy-paste errors) across classes
# that depend on GraphStore. Any new consumer must use the correct
# method names from this interface.
# ======================================================================


class GraphStoreProtocol:
    """Interface contract for graph storage consumers.

    Subclasses MUST implement all methods listed here. Consumers
    (GraphMemoryManager, AssociationEngine, etc.) MUST only call
    methods declared on this interface.
    """

    def ensure_meta(self, memory_id: str, zone: str = "general",
                    importance: float = 0.5) -> None:
        """Ensure a memory has an entry in the meta table."""
        raise NotImplementedError

    def record_access(self, memory_id: str, context: str = "") -> None:
        """Record a memory access (for decay computation)."""
        raise NotImplementedError

    def upsert_edge(self, source: str, target: str,
                    relation: str = "co_occurs",
                    weight_delta: float = 0.0) -> None:
        """Create or update an edge between two memories."""
        raise NotImplementedError

    def propagate_activation(self, seed_ids: List[str],
                             max_depth: int = 2,
                             decay_factor: float = 0.5,
                             min_weight: float = 0.1,
                             limit: int = 10) -> List[dict]:
        """Propagate activation along graph edges from seeds."""
        raise NotImplementedError

    def decay_edges(self, decay_rate: float = 0.01) -> None:
        """Decay all association edge weights over time."""
        raise NotImplementedError

    def get_stats(self) -> dict:
        """Return graph statistics."""
        raise NotImplementedError

    def close(self) -> None:
        """Close database connection."""
        raise NotImplementedError


# ======================================================================
# Graph Store — SQLite-backed association edges
# ======================================================================


class GraphStore(GraphStoreProtocol):
    """SQLite-backed graph associations between memories.

    Each memory has an ID (its filename stem in the memory store).
    Edges connect two memory IDs with a relation type and Hebbian weight.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS graph_edges (
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        relation TEXT NOT NULL DEFAULT 'co_occurs',
        weight REAL DEFAULT 0.5,
        co_occurrence INTEGER DEFAULT 1,
        created_at TEXT,
        last_activated TEXT,
        PRIMARY KEY (source_id, target_id, relation)
    );
    CREATE TABLE IF NOT EXISTS graph_memory_meta (
        id TEXT PRIMARY KEY,
        access_count INTEGER DEFAULT 0,
        last_access_at TEXT,
        importance REAL DEFAULT 0.5,
        strength REAL DEFAULT 1.0,
        status TEXT DEFAULT 'active',
        zone TEXT DEFAULT 'general',
        embedding_hint TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_edges_source ON graph_edges(source_id);
    CREATE INDEX IF NOT EXISTS idx_edges_target ON graph_edges(target_id);
    CREATE INDEX IF NOT EXISTS idx_edges_weight ON graph_edges(weight DESC);
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._known_meta: Set[str] = set()

    def _connect(self) -> sqlite3.Connection:
        """Get or create SQLite connection with WAL mode for concurrency."""
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=3000")
                self._conn.executescript(self.SCHEMA)
            except sqlite3.Error as e:
                logger.exception("graph_store: failed to open DB at %s: %s", self.db_path, e)
                raise
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # -- Edge operations --

    def upsert_edge(self, source_id: str, target_id: str,
                    relation: str = "co_occurs",
                    weight_delta: float = 0.0) -> None:
        """Create or strengthen an edge between two memories.

        If edge exists: increment co_occurrence, adjust weight by weight_delta.
        If new: create with weight = 0.5 + weight_delta.
        """
        if not source_id or not target_id:
            logger.warning("graph_store: upsert_edge called with empty id (src=%r, tgt=%r)", source_id, target_id)
            return
        try:
            conn = self._connect()
            now = datetime.now(timezone.utc).isoformat()
            existing = conn.execute(
                "SELECT weight, co_occurrence FROM graph_edges "
                "WHERE source_id=? AND target_id=? AND relation=?",
                (source_id, target_id, relation)
            ).fetchone()

            if existing:
                new_weight = min(1.0, max(0.01, existing["weight"] + weight_delta))
                conn.execute(
                    "UPDATE graph_edges SET weight=?, co_occurrence=co_occurrence+1, "
                    "last_activated=? WHERE source_id=? AND target_id=? AND relation=?",
                    (new_weight, now, source_id, target_id, relation)
                )
            else:
                weight = min(1.0, max(0.01, 0.5 + weight_delta))
                conn.execute(
                    "INSERT OR IGNORE INTO graph_edges "
                    "(source_id, target_id, relation, weight, co_occurrence, created_at, last_activated) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (source_id, target_id, relation, weight, now, now)
                )
            conn.commit()
        except sqlite3.Error as e:
            logger.exception("graph_store: upsert_edge error %s→%s: %s", source_id, target_id, e)

    def get_edges(self, memory_id: str, relation: Optional[str] = None,
                  min_weight: float = 0.0, limit: int = 20) -> List[dict]:
        """Get edges from a memory, optionally filtered by relation and weight."""
        try:
            conn = self._connect()
            if relation:
                rows = conn.execute(
                    "SELECT * FROM graph_edges WHERE (source_id=? OR target_id=?) "
                    "AND relation=? AND weight>=? ORDER BY weight DESC LIMIT ?",
                    (memory_id, memory_id, relation, min_weight, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM graph_edges WHERE (source_id=? OR target_id=?) "
                    "AND weight>=? ORDER BY weight DESC LIMIT ?",
                    (memory_id, memory_id, min_weight, limit)
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.exception("graph_store: get_edges error for %s: %s", memory_id, e)
            return []

    def get_neighbors(self, memory_id: str, min_weight: float = 0.1,
                      limit: int = 20) -> List[dict]:
        """Get neighbor memories (directly connected)."""
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT source_id, target_id, relation, weight, co_occurrence "
                "FROM graph_edges WHERE (source_id=? OR target_id=?) AND weight>=? "
                "ORDER BY weight DESC LIMIT ?",
                (memory_id, memory_id, min_weight, limit)
            ).fetchall()
            results = []
            for r in rows:
                neighbor = r["target_id"] if r["source_id"] == memory_id else r["source_id"]
                results.append({
                    "memory_id": neighbor,
                    "relation": r["relation"],
                    "weight": r["weight"],
                    "co_occurrence": r["co_occurrence"],
                })
            return results
        except sqlite3.Error as e:
            logger.exception("graph_store: get_neighbors error for %s: %s", memory_id, e)
            return []

    def propagate_activation(self, seed_ids: List[str], max_depth: int = 2,
                             decay_factor: float = 0.5, min_weight: float = 0.1,
                             limit: int = 10) -> List[dict]:
        """Breadth-first graph traversal from seed memories.

        Returns related memories with accumulated activation scores.

        Args:
            seed_ids: starting memory IDs for traversal
            max_depth: max traversal depth (default 2, max 5)
            decay_factor: activation decay per depth level (0.0-1.0)
            min_weight: minimum edge weight to traverse (0.0-1.0)
            limit: max results to return (default 10, max 100)

        Returns:
            List of dicts with memory_id, relation, weight, activation, depth, via
        """
        if not seed_ids:
            return []
        max_depth = max(1, min(max_depth, 5))
        min_weight = max(0.0, min(min_weight, 1.0))
        limit = max(1, min(limit, 100))
        decay_factor = max(0.1, min(decay_factor, 0.9))
        visited = set(seed_ids)
        frontier = list(seed_ids)
        results = []
        current_depth = 0

        while frontier and current_depth < max_depth:
            next_frontier = []
            current_weight = 1.0 * (decay_factor ** current_depth)

            for fid in frontier:
                neighbors = self.get_neighbors(fid, min_weight=min_weight, limit=limit)
                for n in neighbors:
                    nid = n["memory_id"]
                    if nid not in visited:
                        visited.add(nid)
                        results.append({
                            "memory_id": nid,
                            "relation": n["relation"],
                            "weight": n["weight"],
                            "activation": current_weight * n["weight"],
                            "depth": current_depth + 1,
                            "via": fid,
                        })
                        next_frontier.append(nid)

            frontier = next_frontier
            current_depth += 1

        # Sort by activation descending
        results.sort(key=lambda x: x["activation"], reverse=True)
        return results[:limit]

    # -- Memory metadata --

    def ensure_meta(self, memory_id: str, zone: str = "general",
                    importance: float = 0.5) -> None:
        """Ensure a memory has an entry in the meta table (with in-memory cache)."""
        if memory_id in self._known_meta:
            return
        conn = self._connect()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO graph_memory_meta "
            "(id, access_count, last_access_at, importance, strength, status, zone) "
            "VALUES (?, 0, ?, ?, 1.0, 'active', ?)",
            (memory_id, now, importance, zone)
        )
        conn.commit()
        self._known_meta.add(memory_id)

    def record_access(self, memory_id: str, context: str = ""):
        """Record a memory access (for decay computation).

        Args:
            memory_id: target memory ID
            context: optional access context description
        """
        if not memory_id:
            return
        try:
            conn = self._connect()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE graph_memory_meta SET access_count=access_count+1, "
                "last_access_at=? WHERE id=?",
                (now, memory_id)
            )
            conn.commit()
        except sqlite3.Error as e:
            logger.exception("graph_store: record_access error: %s", e)

    def update_importance(self, memory_id: str, delta: float = 0.0) -> None:
        """Update memory importance (feedback-driven).

        Args:
            memory_id: target memory ID
            delta: importance adjustment (-1.0 to 1.0). Positive=strengthen.
        """
        if not memory_id:
            return
        try:
            conn = self._connect()
            if delta:
                conn.execute(
                    "UPDATE graph_memory_meta SET importance = "
                    "MAX(0.0, MIN(1.0, importance + ?)) WHERE id=?",
                    (delta, memory_id)
                )
            conn.commit()
        except sqlite3.Error as e:
            logger.exception("graph_store: update_importance error: %s", e)

    def get_meta(self, memory_id: str) -> Optional[dict]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM graph_memory_meta WHERE id=?", (memory_id,)
        ).fetchone()
        return dict(row) if row else None

    def decay_all(self, base_half_life: float = 7.0):
        """Run strength decay on all memory entries."""
        conn = self._connect()
        memories = conn.execute(
            "SELECT id, importance, access_count, last_access_at, strength "
            "FROM graph_memory_meta"
        ).fetchall()

        now = datetime.now(timezone.utc)
        for mem in memories:
            if mem["last_access_at"]:
                last_acc = datetime.fromisoformat(mem["last_access_at"])
                elapsed_days = (now - last_acc).total_seconds() / 86400.0
            else:
                elapsed_days = 0

            half_life = base_half_life * (1 + 0.5 * math.log(1 + (mem["access_count"] or 0)))
            new_strength = mem["importance"] * (0.5 ** (elapsed_days / half_life))

            if new_strength < 0.05:
                status = "forgotten"
            elif new_strength < 0.15:
                status = "archived"
            else:
                status = "active"

            conn.execute(
                "UPDATE graph_memory_meta SET strength=?, status=? WHERE id=?",
                (new_strength, status, mem["id"])
            )
        conn.commit()

    def decay_edges(self, decay_rate: float = 0.01) -> None:
        """Decay all association edge weights over time.

        Long-term non-co-occurrence → weights gradually decrease.
        Edge weights are clamped to a minimum of 0.01.

        Args:
            decay_rate: amount subtracted per decay cycle (default 0.01)
        """
        conn = self._connect()
        for relation in ("co_occurs", "co_used_in_task"):
            conn.execute(
                "UPDATE graph_edges SET weight = MAX(0.01, weight - ?) "
                "WHERE relation = ?",
                (decay_rate, relation)
            )
        conn.commit()

    def stats(self) -> dict:
        """Return graph statistics with connection pool health."""
        try:
            conn = self._connect()
            edge_count = conn.execute("SELECT COUNT(*) as c FROM graph_edges").fetchone()["c"]
            node_count = conn.execute("SELECT COUNT(*) as c FROM graph_memory_meta").fetchone()["c"]
            avg_weight = conn.execute("SELECT AVG(weight) as a FROM graph_edges").fetchone()["a"] or 0.0
            # Health info
            status = conn.execute("PRAGMA journal_mode").fetchone()[0]
            return {
                "node_count": node_count,
                "edge_count": edge_count,
                "avg_weight": round(avg_weight, 4),
                "db_path": str(self.db_path),
                "journal_mode": status,
                "healthy": True,
            }
        except sqlite3.Error as e:
            logger.exception("graph_store: stats error: %s", e)
            return {"node_count": 0, "edge_count": 0, "avg_weight": 0.0,
                    "db_path": str(self.db_path), "healthy": False, "error": str(e)}


# ======================================================================
# Association Engine — Hebbian co-occurrence learning
# ======================================================================


class AssociationEngine:
    """Learn associations between memories via Hebbian co-occurrence.

    Core principle (HeLa-Mem Eq.1):
        Δw_ij = η × (f_i × f_j)
    where f_i, f_j are activation of memory i and j.

    Co-occurring memories → weight increases
    Long separation → weight decays
    """

    def __init__(self, store: GraphStoreProtocol,
                 hebbian_lr: float = 0.15,
                 decay_rate: float = 0.01):
        self.store = store
        self.hebbian_lr = hebbian_lr
        self.decay_rate = decay_rate

    def on_co_occurrence(self, memory_ids: List[str],
                         context: str = "") -> int:
        """Hebbian update when memories co-occur in same context.

        Creates/strengthens bidirectional edges between co-occurring memories.
        Returns number of edges created/updated.
        """
        count = 0
        for i, mid_i in enumerate(memory_ids):
            for mid_j in memory_ids[i + 1:]:
                # Ensure both have metadata
                self.store.ensure_meta(mid_i)
                self.store.ensure_meta(mid_j)
                # Bidirectional Hebbian update
                self.store.upsert_edge(mid_i, mid_j, "co_occurs",
                                       weight_delta=self.hebbian_lr)
                self.store.upsert_edge(mid_j, mid_i, "co_occurs",
                                       weight_delta=self.hebbian_lr)
                self.store.record_access(mid_i, context)
                self.store.record_access(mid_j, context)
                count += 2
        return count

    def on_task_association(self, memory_ids: List[str],
                            task_type: str = "general") -> int:
        """Record memories used together in a task.

        Uses 'co_used_in_task' relation with lower learning rate.
        """
        count = 0
        for i, mid_i in enumerate(memory_ids):
            for mid_j in memory_ids[i + 1:]:
                self.store.ensure_meta(mid_i)
                self.store.ensure_meta(mid_j)
                self.store.upsert_edge(mid_i, mid_j, "co_used_in_task",
                                       weight_delta=0.05)
                self.store.upsert_edge(mid_j, mid_i, "co_used_in_task",
                                       weight_delta=0.05)
                count += 2
        return count

    def decay_all_edges(self):
        """Decay all association edges over time.

        Long-term non-co-occurrence → weights gradually decrease.
        Delegates to GraphStore.decay_edges() for proper encapsulation.
        """
        self.store.decay_edges(self.decay_rate)


# ======================================================================
# Retrieval Router — Adaptive multi-strategy retrieval
# ======================================================================


class RetrievalRouter:
    """Route retrieval tasks to optimal strategies based on task type.

    ROUTING_TABLE:
      - factual:       hybrid retrieval, relevance reranking
      - reasoning:     thought_path (graph activation), novelty reranking
      - skill:         skill_graph (high success-rate paths)
      - recent:        recency_first, time-based reranking
      - exploration:   diverse, coverage-based reranking
      - personalized:  hybrid, preference-based
    """

    ROUTING_TABLE = {
        "factual":       {"strategy": "hybrid",        "rerank": "relevance"},
        "reasoning":     {"strategy": "thought_path",  "rerank": "novelty"},
        "skill":         {"strategy": "skill_graph",   "rerank": "success_rate"},
        "recent":        {"strategy": "recency_first", "rerank": "time"},
        "exploration":   {"strategy": "diverse",       "rerank": "coverage"},
        "personalized":  {"strategy": "hybrid",        "rerank": "preference"},
    }

    def __init__(self, store: GraphStore):
        self.store = store

    def select_strategy(self, task_type: str) -> dict:
        """Select retrieval strategy based on task type."""
        return self.ROUTING_TABLE.get(
            task_type,
            {"strategy": "hybrid", "rerank": "relevance"}
        )

    def retrieve(self, seed_memory_ids: List[str],
                 task_type: str = "reasoning",
                 max_results: int = 10,
                 tier: str = "list") -> List[dict]:
        """Retrieve related memories via the optimal strategy for task_type.

        Progressive disclosure tiers:
          tier='count'  → only count (minimal data)
          tier='list'   → summary with memory_id, relation, weight (default)
          tier='detail' → full propagation info with activation depth
        """
        if not seed_memory_ids:
            return []

        strategy = self.select_strategy(task_type)

        if strategy["strategy"] == "thought_path":
            results = self.store.propagate_activation(
                seed_memory_ids,
                max_depth=2,
                decay_factor=0.5,
                min_weight=0.1,
                limit=max_results if tier != "count" else 1,
            )
        elif strategy["strategy"] == "recency_first":
            results = []
            for mid in seed_memory_ids:
                results.extend(self.store.get_neighbors(mid, min_weight=0.0,
                                                        limit=max_results))
            seen = set()
            deduped = []
            for r in results:
                if r["memory_id"] not in seen:
                    seen.add(r["memory_id"])
                    deduped.append(r)
            results = deduped[:max_results]
        else:
            results = self.store.propagate_activation(
                seed_memory_ids,
                max_depth=1,
                decay_factor=0.7,
                min_weight=0.2,
                limit=max_results,
            )

        # Apply tier filtering
        if tier == "count":
            return [{"memory_id": r["memory_id"]} for r in results[:max_results]]
        elif tier == "list":
            return [
                {"memory_id": r["memory_id"],
                 "relation": r.get("relation", ""),
                 "weight": round(r.get("weight", 0), 3)}
                for r in results[:max_results]
            ]
        return results[:max_results]


# ======================================================================
# Graph Memory Manager
# ======================================================================


class GraphMemoryManager:
    """Top-level coordinator for graph memory operations.

    Integrates with the mem-reflection-hermes memory system:
    - On memory write: auto-associate with co-occurring memories
    - On memory search: enhance results with graph-based neighbors
    - Periodic: decay all strengths, decay edges
    """

    def __init__(self, db_dir: Path):
        self.db_path = db_dir / "ahe_graph_memory.db"
        self.store = GraphStore(self.db_path)
        self.associator = AssociationEngine(self.store)
        self.router = RetrievalRouter(self.store)
        logger.info("ahe_graph initialized at %s", self.db_path)

    def record_access(self, memory_id: str, context: str = ""):
        """Record memory access in graph metadata."""
        self.store.ensure_meta(memory_id)
        self.store.record_access(memory_id, context)

    def associate_memories(self, memory_ids: List[str],
                           context: str = "",
                           relation: str = "co_occurs") -> dict:
        """Create associations between multiple memories."""
        # Ensure all have metadata
        for mid in memory_ids:
            self.store.ensure_meta(mid)
            self.store.record_access(mid, context)

        if relation == "co_occurs":
            count = self.associator.on_co_occurrence(memory_ids, context)
        else:
            count = self.associator.on_task_association(memory_ids, "general")

        return {
            "memory_ids": memory_ids,
            "edges_created": count,
            "relation": relation,
            "context": context[:100] if context else "",
        }

    def retrieve_related(self, memory_ids: List[str],
                         task_type: str = "reasoning",
                         max_results: int = 10,
                         tier: str = "list") -> List[dict]:
        """Retrieve graph-related memories with progressive tiers.

        Args:
            memory_ids: seed memory IDs
            task_type: retrieval strategy (factual|reasoning|skill|recent|...)
            max_results: max results to return
            tier: 'count' = minimal, 'list' = summary (default), 'detail' = full
        """
        return self.router.retrieve(memory_ids, task_type, max_results, tier=tier)

    def get_neighbors(self, memory_id: str,
                      min_weight: float = 0.1,
                      limit: int = 20) -> List[dict]:
        """Get direct neighbors of a memory."""
        return self.store.get_neighbors(memory_id, min_weight, limit)

    def get_stats(self, tier: str = "summary") -> dict:
        """Get graph statistics with progressive tiers.

        tier='summary': basic counts (default)
        tier='detail': full stats with avg weight
        """
        stats = self.store.stats()
        if tier == "summary":
            return {
                "nodes": stats["node_count"],
                "edges": stats["edge_count"],
                "avg_weight": f"{round(stats['avg_weight'], 4)} (3dp)",
            }
        return stats

    def run_decay(self):
        """Run all decay processes."""
        self.store.decay_all()
        self.associator.decay_all_edges()

    def close(self):
        self.store.close()


# ======================================================================
# Singleton
# ======================================================================

_graph_manager: Optional[GraphMemoryManager] = None
_gm_lock = threading.Lock()


def get_graph_manager(db_dir: Optional[Path] = None) -> GraphMemoryManager:
    """Get or create the singleton GraphMemoryManager (thread-safe)."""
    global _graph_manager
    if _graph_manager is None:
        with _gm_lock:
            if _graph_manager is None:
                if db_dir is None:
                    db_dir = Path.home() / ".hermes" / "plugins" / "mem-reflection-hermes"
                _graph_manager = GraphMemoryManager(db_dir)
    return _graph_manager


def reset_for_test():
    """Reset graph manager singleton (for testing)."""
    global _graph_manager
    if _graph_manager:
        _graph_manager.close()
        _graph_manager = None
