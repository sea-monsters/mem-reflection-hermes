"""Graph memory layer for mem-reflection-hermes.

Replaces 4-layer abstraction (GraphStoreProtocol → Store → Engine → Router → Manager)
with single GraphIndex class backed by independent SQLite (graph.db).

HeLa-Mem dual-path: Hebbian edges participate in retrieval via spreading activation.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'co_occurs',
    weight REAL NOT NULL DEFAULT 0.5,
    co_occurrence INTEGER NOT NULL DEFAULT 1,
    last_activated TEXT,
    PRIMARY KEY (source_id, target_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_weight ON edges(weight DESC);

CREATE TABLE IF NOT EXISTS graph_meta (
    memory_id TEXT PRIMARY KEY,
    zone TEXT NOT NULL DEFAULT 'general',
    access_count INTEGER NOT NULL DEFAULT 0,
    last_access TEXT,
    importance REAL NOT NULL DEFAULT 0.5,
    strength REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'active'
);
"""


class GraphIndex:
    """SQLite-backed Hebbian associative memory graph."""

    # HeLa-Mem per-step decay constant (λ=0.995 per spreading activation step)
    _PER_STEP_DECAY = 0.995

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._local = threading.local()
        self._lock = threading.RLock()
        self._step_counter = 0  # total spreading activation steps
        self._last_decay_step = 0
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Thread-local SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.Error:
                try:
                    conn.close()
                except Exception as e:
                    logger.debug("Failed to close stale SQLite connection: %s", e)
                    pass
                self._local.conn = None
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(graph_meta)").fetchall()
        }
        if "zone" not in columns:
            conn.execute("ALTER TABLE graph_meta ADD COLUMN zone TEXT NOT NULL DEFAULT 'general'")
        conn.commit()

    # -- meta ----------------------------------------------------------------

    def ensure_meta(self, memory_id: str, zone: str = "general") -> None:
        """Ensure a meta row exists for *memory_id*."""
        normalized_zone = (zone or "general").lower().strip() or "general"
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO graph_meta (memory_id, zone, status)
                   VALUES (?, ?, ?)
                   ON CONFLICT(memory_id) DO UPDATE SET zone = excluded.zone""",
                (memory_id, normalized_zone, "active"),
            )
            conn.commit()

    # -- edges ---------------------------------------------------------------

    def associate(self, memory_ids: List[str], context: str = "") -> int:
        """Create/update Hebbian edges between all pairs in *memory_ids*.

        Returns number of edges updated.
        """
        if len(memory_ids) < 2:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        updated = 0
        with self._lock:
            conn = self._get_conn()
            for i, src in enumerate(memory_ids):
                for tgt in memory_ids[i + 1 :]:
                    if src == tgt:
                        continue
                    # Ensure meta rows exist
                    conn.execute(
                        "INSERT OR IGNORE INTO graph_meta (memory_id) VALUES (?)",
                        (src,),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO graph_meta (memory_id) VALUES (?)",
                        (tgt,),
                    )
                    # Upsert edge (symmetric — store both directions)
                    for a, b in ((src, tgt), (tgt, src)):
                        row = conn.execute(
                            "SELECT weight, co_occurrence FROM edges WHERE source_id = ? AND target_id = ? AND relation = 'co_occurs'",
                            (a, b),
                        ).fetchone()
                        if row:
                            new_weight = min(1.0, row["weight"] + 0.05)
                            new_co = row["co_occurrence"] + 1
                            conn.execute(
                                "UPDATE edges SET weight = ?, co_occurrence = ?, last_activated = ? WHERE source_id = ? AND target_id = ? AND relation = 'co_occurs'",
                                (new_weight, new_co, now, a, b),
                            )
                        else:
                            conn.execute(
                                "INSERT INTO edges (source_id, target_id, relation, weight, co_occurrence, last_activated) VALUES (?, ?, 'co_occurs', 0.5, 1, ?)",
                                (a, b, now),
                            )
                        updated += 1
            conn.commit()
        return updated

    def neighbors(
        self, memory_id: str, min_weight: float = 0.1, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return neighbors of *memory_id* ordered by weight desc."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT target_id, weight, co_occurrence, last_activated
               FROM edges
               WHERE source_id = ? AND weight >= ?
               ORDER BY weight DESC
               LIMIT ?""",
            (memory_id, min_weight, limit),
        ).fetchall()
        return [
            {
                "memory_id": r["target_id"],
                "weight": r["weight"],
                "co_occurrence": r["co_occurrence"],
                "last_activated": r["last_activated"],
            }
            for r in rows
        ]

    def _neighbors_raw(self, memory_id: str) -> List[Dict[str, Any]]:
        """Unfiltered neighbor list for spreading activation."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT target_id, weight FROM edges WHERE source_id = ?",
            (memory_id,),
        ).fetchall()
        return [{"target_id": r["target_id"], "weight": r["weight"]} for r in rows]

    # -- spreading activation (HeLa-Mem §3.4) --------------------------------

    def spread(
        self, seed_ids: List[str], decay: float = 0.7, max_iter: int = 50, max_nodes: int = 1000
    ) -> Dict[str, float]:
        """Fixed-point activation spreading from seed nodes.

        Returns {node_id: activation_score} for all reached nodes.
        Also increments the internal step counter for per-step decay.
        """
        activation: Dict[str, float] = {sid: 1.0 for sid in seed_ids}
        for _ in range(max_iter):
            if len(activation) > max_nodes:
                break
            new_act: Dict[str, float] = {}
            for nid, act in activation.items():
                if act < 0.01:
                    continue
                for neighbor in self._neighbors_raw(nid):
                    propagated = act * decay * neighbor["weight"]
                    tid = neighbor["target_id"]
                    new_act[tid] = max(new_act.get(tid, 0.0), propagated)
            activation.update(new_act)
            delta = sum(
                abs(new_act.get(nid, 0.0) - activation.get(nid, 0.0))
                for nid in set(new_act) | set(activation)
            )
            if delta < 1e-4:
                break
        self._step_counter += 1
        return activation

    # -- decay ---------------------------------------------------------------

    def decay(self) -> None:
        """Apply Ebbinghaus decay to all edges (30-day half-life).

        Calendar-time decay; kept for backward compatibility.
        For HeLa-Mem aligned per-step decay, use step_decay().
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT source_id, target_id, relation, last_activated, weight FROM edges"
            ).fetchall()
            for r in rows:
                last = r["last_activated"]
                if not last:
                    continue
                try:
                    last_dt = datetime.fromisoformat(last)
                    days = max(0, (now - last_dt).total_seconds() / 86400.0)
                    factor = 0.5 ** (days / 30.0)
                    new_weight = r["weight"] * factor
                    if new_weight < 0.05:
                        conn.execute(
                            "DELETE FROM edges WHERE source_id = ? AND target_id = ? AND relation = ?",
                            (r["source_id"], r["target_id"], r["relation"]),
                        )
                    else:
                        conn.execute(
                            "UPDATE edges SET weight = ? WHERE source_id = ? AND target_id = ? AND relation = ?",
                            (new_weight, r["source_id"], r["target_id"], r["relation"]),
                        )
                except Exception as e:
                    logger.warning(
                        "Edge decay failed for %s->%s: %s",
                        r["source_id"], r["target_id"], e,
                    )
                    continue
            conn.commit()

    def step_decay(self) -> Dict[str, Any]:
        """Apply HeLa-Mem per-step decay (λ=0.995 per spreading activation step).

        Decay is proportional to the number of spreading activation calls
        since the edge was last activated.  Edges with weight < 0.05 are pruned.

        Returns summary dict with steps_applied and edges_pruned.
        """
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT source_id, target_id, relation, weight, co_occurrence FROM edges"
            ).fetchall()
            steps_since = max(1, self._step_counter - self._last_decay_step)
            factor = self._PER_STEP_DECAY ** steps_since
            pruned = 0
            updated = 0
            for r in rows:
                new_weight = r["weight"] * factor
                if new_weight < 0.05:
                    conn.execute(
                        "DELETE FROM edges WHERE source_id = ? AND target_id = ? AND relation = ?",
                        (r["source_id"], r["target_id"], r["relation"]),
                    )
                    pruned += 1
                else:
                    conn.execute(
                        "UPDATE edges SET weight = ? WHERE source_id = ? AND target_id = ? AND relation = ?",
                        (new_weight, r["source_id"], r["target_id"], r["relation"]),
                    )
                    updated += 1
            conn.commit()
            self._last_decay_step = self._step_counter
            return {
                "steps_since_last_decay": steps_since,
                "decay_factor": round(factor, 6),
                "edges_pruned": pruned,
                "edges_updated": updated,
            }

    # -- pagerank ------------------------------------------------------------

    def pagerank(self, damping: float = 0.85, max_iter: int = 50, tol: float = 1e-6) -> Dict[str, float]:
        """Compute PageRank centrality with reverse adjacency (O(n·d))."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT source_id, target_id, weight FROM edges"
        ).fetchall()
        if not rows:
            return {}

        # Build forward adjacency: node -> [(out_neighbor, weight)]
        adj: Dict[str, List[Tuple[str, float]]] = {}
        all_nodes: Set[str] = set()
        for r in rows:
            src, tgt, w = r["source_id"], r["target_id"], r["weight"]
            all_nodes.add(src)
            all_nodes.add(tgt)
            adj.setdefault(src, []).append((tgt, w))

        n = len(all_nodes)
        if n == 0:
            return {}

        # Pre-build reverse adjacency: target -> [(source, weight, total_out)]
        # for O(n·d) inner loop instead of O(n²·d).
        rev_adj: Dict[str, List[Tuple[str, float, float]]] = {}
        for src, out_edges in adj.items():
            total_out = sum(w for _, w in out_edges)
            if total_out == 0:
                continue
            for tgt, w in out_edges:
                rev_adj.setdefault(tgt, []).append((src, w, total_out))

        # Initialize
        rank: Dict[str, float] = {node: 1.0 / n for node in all_nodes}

        for _ in range(max_iter):
            new_rank: Dict[str, float] = {}
            for node in all_nodes:
                contrib = 0.0
                for src, w, total_out in rev_adj.get(node, []):
                    contrib += damping * rank[src] * (w / total_out)
                new_rank[node] = (1.0 - damping) / n + contrib
            # Check convergence
            delta = sum(abs(new_rank.get(nid, 0) - rank.get(nid, 0)) for nid in all_nodes)
            rank = new_rank
            if delta < tol:
                break
        return rank

    # -- stats ---------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        node_count = conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT source_id FROM edges UNION SELECT DISTINCT target_id FROM edges)"
        ).fetchone()[0]
        avg_weight = conn.execute("SELECT AVG(weight) FROM edges").fetchone()[0] or 0.0
        return {
            "nodes": node_count,
            "edges": edge_count,
            "avg_weight": round(avg_weight, 4),
        }

    # -- Hebbian Distillation (HeLa-Mem §3.2) --------------------------------

    def distill(
        self,
        store,
        hub_threshold: float = 0.15,
        min_neighbors: int = 3,
        max_neighbors: int = 10,
    ) -> List[Dict[str, Any]]:
        """Distill densely connected memory hubs into semantic summaries.

        HeLa-Mem's Reflective Consolidation: identify PageRank hubs,
        cluster high-weight neighbors, generate a distilled semantic
        memory per hub, store in zone='semantic'.

        Returns list of distilled summaries with hub_id and member_ids.
        """
        try:
            from .store import MemoryFrontmatter, _tokenise
        except ImportError:
            # Fallback for direct loading (e.g., via importlib.util.spec_from_file_location)
            import importlib.util as _iu
            _repo = Path(__file__).resolve().parent.parent
            _pkg = "mem_reflection_hermes.core.store"
            if _pkg in sys.modules:
                _store_mod = sys.modules[_pkg]
            elif "mem_reflection_hermes" in sys.modules:
                _store_mod = sys.modules["mem_reflection_hermes.core.store"]
            elif "_store" in sys.modules:
                _store_mod = sys.modules["_store"]
            elif "_store_fallback" in sys.modules:
                _store_mod = sys.modules["_store_fallback"]
            else:
                _store_spec = _iu.spec_from_file_location(_pkg, str(_repo / "core" / "store.py"))
                _store_mod = _iu.module_from_spec(_store_spec)
                _store_mod.__package__ = "mem_reflection_hermes.core"
                sys.modules[_pkg] = _store_mod
                _store_spec.loader.exec_module(_store_mod)
            MemoryFrontmatter = _store_mod.MemoryFrontmatter
            _tokenise = _store_mod._tokenise

        pr = self.pagerank()
        if not pr:
            return []

        # Identify hubs (PageRank > threshold, top 20)
        hubs = sorted(pr.items(), key=lambda x: x[1], reverse=True)
        hubs = [(hid, score) for hid, score in hubs if score >= hub_threshold]
        hubs = hubs[:20]

        distilled: List[Dict[str, Any]] = []
        seen_members: Set[str] = set()

        for hub_id, hub_score in hubs:
            # Get high-weight neighbors (excluding already-distilled members)
            nbrs = self.neighbors(hub_id, min_weight=0.2, limit=max_neighbors)
            member_ids = [hub_id] + [n["memory_id"] for n in nbrs]
            member_ids = [m for m in member_ids if m not in seen_members]
            if len(member_ids) < min_neighbors:
                continue

            # Load member bodies from store
            bodies: List[str] = []
            tags: Set[str] = set()
            for mid in member_ids:
                mem = store.get(mid)
                if mem is None:
                    continue
                bodies.append(mem.body.strip())
                if mem.frontmatter.tags:
                    tags.update(mem.frontmatter.tags)
            if len(bodies) < min_neighbors:
                continue

            # Heuristic summary: shared keywords + member count
            all_tokens: List[str] = []
            for b in bodies:
                all_tokens.extend(_tokenise(b))
            from collections import Counter
            freq = Counter(all_tokens)
            top_keywords = [t for t, c in freq.most_common(5) if c >= 2]

            summary = (
                f"Semantic cluster of {len(bodies)} memories. "
                f"Hub: {hub_id[:8]}... (PageRank {hub_score:.3f}). "
                f"Keywords: {', '.join(top_keywords) if top_keywords else 'N/A'}. "
                f"Members: {', '.join(m[:8] + '...' for m in member_ids[:5])}"
                f"{' and ' + str(len(member_ids) - 5) + ' more' if len(member_ids) > 5 else ''}."
            )

            # Write semantic memory
            fm = MemoryFrontmatter.new(
                source="hebbian_distillation",
                confidence="medium",
                tags=list(tags)[:10] + ["semantic", "distilled"],
                zone="semantic",
            )
            try:
                store.put("user", fm, summary)
                distilled.append({
                    "hub_id": hub_id,
                    "member_ids": member_ids,
                    "semantic_id": fm.id,
                    "summary": summary,
                    "page_rank": round(hub_score, 4),
                })
                seen_members.update(member_ids)
            except Exception as e:
                logger.warning("Distillation write failed for hub %s: %s", hub_id, e)

        return distilled

    # -- cross-zone analysis -------------------------------------------------

    def cross_zone(self, store) -> Dict[str, Any]:
        """Analyze cross-zone connections."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT source_id, target_id, weight FROM edges WHERE weight >= 0.1"
        ).fetchall()

        # Build zone map from store
        zone_map: Dict[str, str] = {}
        for m in store.list_active():
            zone_map[m.id()] = m.frontmatter.zone

        zone_matrix: Dict[str, Dict[str, float]] = {}
        bridge_memories: List[Dict[str, Any]] = []
        seen_pairs: set = set()
        for r in rows:
            src_zone = zone_map.get(r["source_id"], "unknown")
            tgt_zone = zone_map.get(r["target_id"], "unknown")
            if src_zone == tgt_zone:
                continue
            # Deduplicate symmetric edges (a→b and b→a are one bridge)
            pair = tuple(sorted((r["source_id"], r["target_id"])))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            w = r["weight"]
            zone_matrix.setdefault(src_zone, {}).setdefault(tgt_zone, 0.0)
            zone_matrix[src_zone][tgt_zone] += w
            if w > 0.3:
                bridge_memories.append({
                    "source": r["source_id"],
                    "target": r["target_id"],
                    "weight": w,
                })

        return {
            "zone_matrix": zone_matrix,
            "bridge_count": len(bridge_memories),
            "bridges": bridge_memories[:20],
        }

    def remove_memory(self, memory_id: str) -> None:
        """Remove all edges and meta associated with *memory_id* (thread-safe)."""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM edges WHERE source_id = ? OR target_id = ?",
                (memory_id, memory_id),
            )
            conn.execute(
                "DELETE FROM graph_meta WHERE memory_id = ?",
                (memory_id,),
            )
            conn.commit()

    def clean_orphan_edges(self, valid_ids: Set[str]) -> int:
        """Delete edges + meta where memory no longer exists in *valid_ids*.

        Returns total count of rows deleted (edges + graph_meta).
        Fail-open: on SQL error, logs warning and returns 0.
        """
        deleted = 0
        try:
            with self._lock:
                conn = self._get_conn()
                if not valid_ids:
                    # Empty set means all rows are orphaned
                    deleted += conn.execute("DELETE FROM edges").rowcount
                    deleted += conn.execute("DELETE FROM graph_meta").rowcount
                else:
                    placeholders = ",".join("?" * len(valid_ids))
                    ids_list = list(valid_ids)
                    deleted += conn.execute(
                        f"DELETE FROM edges WHERE source_id NOT IN ({placeholders}) OR target_id NOT IN ({placeholders})",
                        ids_list + ids_list,
                    ).rowcount
                    deleted += conn.execute(
                        f"DELETE FROM graph_meta WHERE memory_id NOT IN ({placeholders})",
                        ids_list,
                    ).rowcount
                conn.commit()
        except Exception as e:
            logger.warning("Graph orphan edge cleanup failed: %s", e)
        return deleted

    def close(self) -> None:
        """Checkpoint WAL and close connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._local.conn.close()
            self._local.conn = None
