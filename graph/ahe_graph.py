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
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple

logger = logging.getLogger(__name__)


# ======================================================================
# GraphStore Protocol — interface contract for graph memory consumers
# ======================================================================
# Prevents method-naming drift (copy-paste errors) across classes
# that depend on GraphStore. Any new consumer must use the correct
# method names from this interface.
# ======================================================================


class GraphStoreProtocol(Protocol):
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
        """Propagate activation along graph edges from seeds.

        Complexity (P2-17): BFS O(b^d) where b=max_neighbors(20) and
        d=max_depth(2-3). At depth=3: ~8000 nodes visited — ~50-100ms SQLite
        latency. Acceptable for typical usage.
        """
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
        self._local = threading.local()  # Per-thread connections (beta3: thread-safety fix)
        self._all_conns: Set[sqlite3.Connection] = set()
        self._all_conns_lock = threading.Lock()
        self._known_meta: Set[str] = set()
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        """Get or create per-thread SQLite connection with WAL mode."""
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is None:
            try:
                conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=3000")
                conn.executescript(self.SCHEMA)
                self._local.conn = conn
                with self._all_conns_lock:
                    self._all_conns.add(conn)
            except sqlite3.Error as e:
                logger.exception("graph_store: failed to open DB at %s: %s", self.db_path, e)
                raise
        return conn

    def close(self):
        """Close all connections (called from any thread).

        Checkpoints WAL and cleans up journal files so Windows temp directory
        cleanup does not fail on locked -wal / -shm handles.
        """
        # Release per-thread conn reference first
        self._local.conn = None
        self._local.__dict__.clear()
        with self._all_conns_lock:
            conns = list(self._all_conns)
            self._all_conns.clear()
        for conn in conns:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as e:
                logger.debug("WAL checkpoint failed during close: %s", e)
            try:
                conn.execute("PRAGMA journal_mode=DELETE")
            except Exception as e:
                logger.debug("Journal mode switch failed during close: %s", e)
            try:
                conn.close()
            except Exception as e:
                logger.debug("Connection close failed during close: %s", e)

    # -- Edge operations --

    def set_edge_weight(self, source_id: str, target_id: str,
                        relation: str = "co_occurs",
                        weight: float = 0.5) -> None:
        """Set absolute edge weight (unlike upsert_edge which uses delta)."""
        if not source_id or not target_id:
            return
        with self._lock:
            try:
                conn = self._connect()
                now = datetime.now(timezone.utc).isoformat()
                existing = conn.execute(
                    "SELECT weight FROM graph_edges "
                    "WHERE source_id=? AND target_id=? AND relation=?",
                    (source_id, target_id, relation)
                ).fetchone()
                w = min(1.0, max(0.01, weight))
                if existing:
                    conn.execute(
                        "UPDATE graph_edges SET weight=?, last_activated=? "
                        "WHERE source_id=? AND target_id=? AND relation=?",
                        (w, now, source_id, target_id, relation)
                    )
                else:
                    conn.execute(
                        "INSERT INTO graph_edges (source_id, target_id, relation, weight, co_occurrence, created_at, last_activated) "
                        "VALUES (?, ?, ?, ?, 1, ?, ?)",
                        (source_id, target_id, relation, w, now, now)
                    )
                conn.commit()
                self._invalidate_adj_cache()
            except sqlite3.Error as e:
                logger.exception("graph_store: set_edge_weight error: %s", e)

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
        with self._lock:
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
                self._invalidate_adj_cache()
            except sqlite3.Error as e:
                logger.exception("graph_store: upsert_edge error %s→%s: %s", source_id, target_id, e)

    def get_edges(self, memory_id: str, relation: Optional[str] = None,
                  min_weight: float = 0.0, limit: int = 20) -> List[dict]:
        """Get edges from a memory, optionally filtered by relation and weight."""
        with self._lock:
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
                      limit: int = 20,
                      exclude_relations: Optional[List[str]] = None) -> List[dict]:
        """Get neighbor memories (directly connected).

        W2.5: SUPERSEDES edges are excluded by default — they belong to the
        lineage layer, not the associative graph.
        """
        if exclude_relations is None:
            exclude_relations = ["SUPERSEDES"]
        with self._lock:
            try:
                conn = self._connect()
                if exclude_relations:
                    placeholders = ",".join("?" * len(exclude_relations))
                    rows = conn.execute(
                        f"SELECT source_id, target_id, relation, weight, co_occurrence "
                        f"FROM graph_edges WHERE (source_id=? OR target_id=?) AND weight>=? "
                        f"AND relation NOT IN ({placeholders}) "
                        f"ORDER BY weight DESC LIMIT ?",
                        (memory_id, memory_id, min_weight, *exclude_relations, limit)
                    ).fetchall()
                else:
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
                        "target_id": neighbor,
                        "relation": r["relation"],
                        "weight": r["weight"],
                        "co_occurrence": r["co_occurrence"],
                    })
                return results
            except sqlite3.Error as e:
                logger.exception("graph_store: get_neighbors error for %s: %s", memory_id, e)
                return []

    # W3.1: cached sparse adjacency for iterative spreading activation
    _cached_adj: Optional[Dict[str, List[Tuple[str, float]]]] = None
    _cached_adj_mtime: int = 0
    _cached_adj_min_weight: float = 0.0

    def _build_adjacency(self, min_weight: float = 0.0) -> Dict[str, List[Tuple[str, float]]]:
        """Build normalized sparse adjacency table from graph_edges.

        Returns dict: node_id -> [(neighbor_id, normalized_weight), ...]
        Weights are normalized by out-degree (row-stochastic).
        """
        # H18: mtime check, DB query, and cache update all inside self._lock
        with self._lock:
            try:
                mtime = self.db_path.stat().st_mtime_ns
            except Exception:
                mtime = 0
            if (self._cached_adj is not None
                    and self._cached_adj_mtime == mtime
                    and self._cached_adj_min_weight == min_weight):
                return self._cached_adj

            conn = self._connect()
            rows = conn.execute(
                "SELECT source_id, target_id, weight FROM graph_edges WHERE weight >= ?",
                (min_weight,)
            ).fetchall()

            # Build raw adjacency + compute out-degree sums
            raw: Dict[str, List[Tuple[str, float]]] = {}
            out_sum: Dict[str, float] = {}
            for src, tgt, w in rows:
                raw.setdefault(src, []).append((tgt, w))
                out_sum[src] = out_sum.get(src, 0.0) + w

            # Normalize by out-degree (row-stochastic)
            adj: Dict[str, List[Tuple[str, float]]] = {}
            for src, edges in raw.items():
                total = out_sum.get(src, 1.0)
                if total > 0:
                    adj[src] = [(tgt, w / total) for tgt, w in edges]

            self._cached_adj = adj
            self._cached_adj_mtime = mtime
            self._cached_adj_min_weight = min_weight
            return adj

    def _invalidate_adj_cache(self) -> None:
        """Call after any edge mutation to force adjacency rebuild."""
        self._cached_adj = None
        self._cached_adj_mtime = 0
        self._cached_adj_min_weight = 0.0

    def spread_activation(self, seed_ids: List[str],
                          decay: float = 0.7,
                          max_iter: int = 50,
                          threshold: float = 1e-4,
                          min_weight: float = 0.0) -> Dict[str, float]:
        """Iterative fixed-point spreading activation (HeLa-Mem Sec.3.4).

        Computes steady-state activation vector via power iteration:
            A_{t+1} = decay * W^T * A_t + seeds
        where W is the row-stochastic adjacency matrix.

        Args:
            seed_ids: initial activated memory IDs (seed vector = 1.0 each)
            decay: damping factor per iteration (default 0.7)
            max_iter: max iterations before forced stop
            threshold: L1 convergence threshold
            min_weight: minimum edge weight to include in adjacency

        Returns:
            Dict mapping memory_id -> activation score (excluding seeds)
        """
        if not seed_ids:
            return {}

        adj = self._build_adjacency(min_weight=min_weight)
        if not adj:
            return {}

        # Collect all node IDs from adjacency
        all_nodes: Set[str] = set(adj.keys())
        for edges in adj.values():
            for tgt, _ in edges:
                all_nodes.add(tgt)

        # Initialize activation: seeds = 1.0, others = 0.0
        act: Dict[str, float] = {nid: 0.0 for nid in all_nodes}
        for sid in seed_ids:
            if sid not in act:
                logger.warning("spread_activation: seed '%s' not in adjacency (isolated or nonexistent)", sid)
            if sid in act:
                act[sid] = 1.0

        # Power iteration
        for _ in range(max_iter):
            new_act: Dict[str, float] = {nid: 0.0 for nid in all_nodes}
            # Propagate along edges: new_act[tgt] += decay * act[src] * W[src,tgt]
            for src, edges in adj.items():
                src_act = act.get(src, 0.0)
                if src_act == 0.0:
                    continue
                for tgt, w in edges:
                    new_act[tgt] = new_act.get(tgt, 0.0) + decay * src_act * w
            # Re-inject seeds
            for sid in seed_ids:
                if sid in new_act:
                    new_act[sid] += 1.0

            # Convergence check (L1 norm)
            diff = sum(abs(new_act.get(nid, 0.0) - act.get(nid, 0.0)) for nid in all_nodes)
            act = new_act
            if diff < threshold:
                break

        # Exclude seeds from result
        for sid in seed_ids:
            act.pop(sid, None)
        return act

    def propagate_activation(self, seed_ids: List[str], max_depth: int = 2,
                             decay_factor: float = 0.5, min_weight: float = 0.1,
                             limit: int = 10) -> List[dict]:
        """Breadth-first graph traversal from seed memories.

        DEPRECATED: use spread_activation() for steady-state activation.
        Kept for backward compatibility.

        Note: the `limit` parameter caps neighbors per BFS node, which may
        prune important paths in densely connected graphs. Use a higher limit
        (e.g., 50) for thorough traversal.
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
        with self._lock:
            conn = self._connect()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO graph_memory_meta "
                "(id, access_count, last_access_at, importance, strength, status, zone) "
                "VALUES (?, 0, ?, ?, 1.0, 'active', ?)",
                (memory_id, now, importance, zone)
            )
            conn.execute(
                "UPDATE graph_memory_meta SET zone=? WHERE id=? AND zone != ?",
                (zone, memory_id, zone)
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
        with self._lock:
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
        with self._lock:
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
        # W3.2: reads use WAL multi-read, no lock needed
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM graph_memory_meta WHERE id=?", (memory_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_nodes(self) -> List[dict]:
        """Get all memory nodes in the graph."""
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id as memory_id, zone, importance, strength, status "
                "FROM graph_memory_meta WHERE status != 'archived'"
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.exception("graph_store: get_all_nodes error: %s", e)
            return []

    def get_all_edges(self, min_weight: float = 0.0) -> List[Tuple[str, str, float]]:
        """Get all edges, optionally filtered by minimum weight (H15).

        Returns list of (source_id, target_id, weight) tuples.
        Used by PageRank and other graph-level operations to avoid N+1 queries.
        """
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT source_id, target_id, weight FROM graph_edges WHERE weight >= ?",
                (min_weight,)
            ).fetchall()
            return [(r["source_id"], r["target_id"], r["weight"]) for r in rows]
        except sqlite3.Error as e:
            logger.exception("graph_store: get_all_edges error: %s", e)
            return []

    def add_supersedes_edge(self, old_memory_id: str, new_memory_id: str) -> None:
        """Record a SUPERSEDES relationship: new_memory supersedes old_memory.

        W2.5 DEPRECATED: SUPERSEDES edges now belong to the lineage layer
        (MemoryFrontmatter.supersedes). This method is a no-op kept for
        backward compatibility.
        """
        logger.debug("add_supersedes_edge is deprecated (no-op): %s -> %s", old_memory_id, new_memory_id)

    def remove_supersedes_edge(self, old_memory_id: str, new_memory_id: str) -> None:
        """Remove a SUPERSEDES edge."""
        with self._lock:
            try:
                conn = self._connect()
                conn.execute(
                    "DELETE FROM graph_edges WHERE source_id=? AND target_id=? AND relation='SUPERSEDES'",
                    (old_memory_id, new_memory_id)
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.exception("graph_store: remove_supersedes_edge error: %s", e)

    def decay_all(self, base_half_life: float = 7.0):
        """Run strength decay on all memory entries.

        Strength thresholds (Ebbinghaus-inspired):
          < 0.05 → "forgotten" (effectively decayed beyond usefulness)
          < 0.15 → "archived" (faded but retained for lineage)
          >= 0.15 → "active" (recently reinforced or high importance)
        """
        batch_size = 100
        offset = 0
        now = datetime.now(timezone.utc)

        while True:
            with self._lock:
                conn = self._connect()
                memories = conn.execute(
                    "SELECT id, importance, access_count, last_access_at, strength "
                    "FROM graph_memory_meta LIMIT ? OFFSET ?",
                    (batch_size, offset),
                ).fetchall()

            if not memories:
                break

            updates = []
            for mem in memories:
                try:
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
                    updates.append((new_strength, status, mem["id"]))
                except Exception:
                    logger.warning("graph_store: decay_all skipped malformed row id=%s", mem.get("id"), exc_info=True)

            if updates:
                with self._lock:
                    conn = self._connect()
                    conn.executemany(
                        "UPDATE graph_memory_meta SET strength=?, status=? WHERE id=?",
                        updates,
                    )
                    conn.commit()

            offset += batch_size

    def decay_edges(self, decay_rate: float = 0.01, prune_threshold: float = 0.005) -> None:
        """Decay all association edge weights over time.

        Long-term non-co-occurrence → weights gradually decrease.
        Edges are pruned once they fall below prune_threshold.
        Edges below prune_threshold are deleted (P2-18: dead edge cleanup).

        Args:
            decay_rate: amount subtracted per decay cycle (default 0.01)
            prune_threshold: edges with weight below this are removed (default 0.005)
        """
        with self._lock:
            conn = self._connect()
            for relation in ("co_occurs", "co_used_in_task"):
                conn.execute(
                    "UPDATE graph_edges SET weight = MAX(0.0, weight - ?) "
                    "WHERE relation = ?",
                    (decay_rate, relation)
                )
                # P2-18: prune edges that have decayed below threshold
                conn.execute(
                    "DELETE FROM graph_edges WHERE weight < ? AND relation = ?",
                    (prune_threshold, relation)
                )
            # SUPERSEDES edges are structural, not Hebbian — never decay
            conn.commit()
            self._invalidate_adj_cache()

    def stats(self) -> dict:
        """Return graph statistics with connection pool health."""
        # W3.2: reads use WAL multi-read, no lock needed
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

    def on_memory_coactivation(self, memory_ids: List[str],
                               context: str = "") -> int:
        """Compatibility alias for dashboard callers."""
        return self.on_co_occurrence(memory_ids, context)

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

    def __init__(self, store: GraphStoreProtocol):
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
        db_dir.mkdir(parents=True, exist_ok=True)
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

    def propagate_activation(self, seed_ids: List[str], **kwargs) -> List[dict]:
        """Compatibility wrapper exposing store activation at manager level."""
        return self.store.propagate_activation(seed_ids, **kwargs)

    def spread_activation(self, seed_ids: List[str], **kwargs) -> Dict[str, float]:
        """Iterative fixed-point spreading activation (W3.1).

        Returns steady-state activation scores for non-seed nodes.
        """
        return self.store.spread_activation(seed_ids, **kwargs)

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
