"""Health, validation, and index maintenance helpers for MemoryStore.

Extracted from core/store.py to keep the main store module under 800 lines.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Set

if TYPE_CHECKING:
    from .store import MemoryStore

logger = logging.getLogger(__name__)


def health_metrics(store: "MemoryStore") -> Dict[str, Any]:
    conn = store._get_conn()
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE id NOT IN (SELECT old_id FROM supersedes)"
    ).fetchone()[0]
    pinned = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE pinned = 1 AND id NOT IN (SELECT old_id FROM supersedes)"
    ).fetchone()[0]
    zones = store.zone_counts()
    dup_clusters = 0
    try:
        from datasketch import MinHash, MinHashLSH
        active_mems = store.list_active()
        if len(active_mems) > 1:
            lsh = MinHashLSH(threshold=0.85, num_perm=128)
            minhashes: Dict[str, Any] = {}
            from .tokenization import _tokenise
            for m in active_mems:
                mh = MinHash(num_perm=128)
                for token in _tokenise(m.body):
                    mh.update(token.encode("utf-8"))
                lsh.insert(m.id(), mh)
                minhashes[m.id()] = mh
            seen_ids: Set[str] = set()
            for m in active_mems:
                if m.id() in seen_ids:
                    continue
                neighbors = lsh.query(minhashes[m.id()])
                if len(neighbors) > 1:
                    dup_clusters += 1
                    seen_ids.update(neighbors)
    except Exception as e:
        from .tokenization import _tokenise
        logger.warning("MinHash duplicate detection failed, falling back to Jaccard: %s", e)
        active_mems = store.list_active()
        seen_ids: Set[str] = set()
        token_sets: Dict[str, Set[str]] = {}
        for m in active_mems:
            token_sets[m.id()] = set(_tokenise(m.body))
        for i, m1 in enumerate(active_mems):
            if m1.id() in seen_ids:
                continue
            cluster = [m1]
            for m2 in active_mems[i + 1: i + 31]:
                if m2.id() in seen_ids:
                    continue
                s1, s2 = token_sets.get(m1.id(), set()), token_sets.get(m2.id(), set())
                if not s1 or not s2:
                    continue
                union = len(s1 | s2)
                jaccard = len(s1 & s2) / union if union else 0.0
                if jaccard > 0.85:
                    cluster.append(m2)
                    seen_ids.add(m2.id())
            if len(cluster) > 1:
                dup_clusters += 1
                seen_ids.update(m.id() for m in cluster)
    superseded_count = conn.execute("SELECT COUNT(*) FROM supersedes").fetchone()[0]
    return {
        "total_memories": total,
        "active_memories": active,
        "pinned_memories": pinned,
        "superseded_count": superseded_count,
        "zone_counts": zones,
        "duplicate_clusters": dup_clusters,
    }


def validate_index(store: "MemoryStore") -> Dict[str, Any]:
    from .store import read_memory
    conn = store._get_conn()
    rows = conn.execute("SELECT id, path, body_hash FROM memories").fetchall()
    disk_ids: Set[str] = set()
    orphaned_rows: List[str] = []
    hash_mismatches: List[str] = []
    for row in rows:
        path = Path(row["path"])
        if not path.exists():
            orphaned_rows.append(row["id"])
            continue
        m = read_memory(path, row["id"])
        if m is not None:
            disk_ids.add(m.id())
            current_hash = hashlib.sha256(m.body.encode("utf-8")).hexdigest()[:16]
            if current_hash != row["body_hash"]:
                hash_mismatches.append(row["id"])
    orphaned_files: List[str] = []
    for scope, root in (("user", store.user_root), ("project", store.project_root)):
        if root is None or not root.exists():
            continue
        for f in root.rglob("*.md"):
            m = read_memory(f, scope)
            if m is not None and m.id() not in {r["id"] for r in rows}:
                orphaned_files.append(str(f))
    return {
        "total_rows": len(rows),
        "total_disk_files": len(disk_ids) + len(orphaned_files),
        "orphaned_rows": orphaned_rows,
        "orphaned_row_count": len(orphaned_rows),
        "orphaned_files": orphaned_files,
        "orphaned_file_count": len(orphaned_files),
        "hash_mismatches": hash_mismatches,
        "hash_mismatch_count": len(hash_mismatches),
    }


def rebuild_index(store: "MemoryStore") -> Dict[str, Any]:
    with store._lock:
        conn = store._get_conn()
        for tbl in ("entity_links", "entities", "stats", "supersedes", "tags", "memories"):
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()
        store._init_db()
        store._sync_from_disk()
    return validate_index(store)


def prune_index(store: "MemoryStore") -> Dict[str, Any]:
    with store._lock:
        conn = store._get_conn()
        rows = conn.execute("SELECT id, path FROM memories").fetchall()
        removed: List[str] = []
        for row in rows:
            if not Path(row["path"]).exists():
                removed.append(row["id"])
        for mid in removed:
            conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
        conn.execute("DELETE FROM tags WHERE memory_id NOT IN (SELECT id FROM memories)")
        conn.execute(
            "DELETE FROM supersedes WHERE old_id NOT IN (SELECT id FROM memories)"
            " OR new_id NOT IN (SELECT id FROM memories)"
        )
        conn.execute("DELETE FROM stats WHERE memory_id NOT IN (SELECT id FROM memories)")
        conn.execute(
            "DELETE FROM entity_links WHERE memory_id NOT IN (SELECT id FROM memories)"
            " OR entity_id NOT IN (SELECT id FROM entities)"
        )
        store._cleanup_orphan_entities(conn)
        conn.commit()
        if removed:
            store._mark_changed()
    return {"pruned": len(removed), "pruned_ids": removed}
