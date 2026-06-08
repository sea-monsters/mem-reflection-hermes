#!/usr/bin/env python3
"""One-time migration script for the memory SQLite index.

Migrates:
- .md memory files → SQLite memories.db (with python-frontmatter)
- memory-stats.jsonl → SQLite stats table
- Old graph.db → new graph.db schema (edges + graph_meta)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import frontmatter
from core.store import (
    MemoryFrontmatter,
    LoadedMemory,
    user_memories_dir,
    project_memories_dir,
    plugin_data_dir,
    normalize_zone,
    _tokenise,
)


def _init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        scope TEXT NOT NULL,
        zone TEXT NOT NULL DEFAULT 'general',
        confidence TEXT NOT NULL DEFAULT 'medium',
        pinned INTEGER NOT NULL DEFAULT 0,
        rank INTEGER NOT NULL DEFAULT 0,
        created TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'user',
        valid_from TEXT,
        valid_until TEXT,
        context_scope TEXT,
        version INTEGER NOT NULL DEFAULT 1,
        supersedes_reason TEXT,
        body_hash TEXT NOT NULL,
        path TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_mem_zone ON memories(zone);
    CREATE INDEX IF NOT EXISTS idx_mem_pinned ON memories(pinned) WHERE pinned = 1;
    CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created);

    CREATE TABLE IF NOT EXISTS tags (
        memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        tag TEXT NOT NULL,
        PRIMARY KEY (memory_id, tag)
    );
    CREATE INDEX IF NOT EXISTS idx_tag_name ON tags(tag);

    CREATE TABLE IF NOT EXISTS supersedes (
        old_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        new_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        reason TEXT,
        PRIMARY KEY (old_id, new_id)
    );

    CREATE TABLE IF NOT EXISTS stats (
        memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        event TEXT NOT NULL,
        at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_stats_mem ON stats(memory_id);
    """)
    conn.commit()
    return conn


def migrate_memories(conn: sqlite3.Connection, root: Path, scope: str) -> int:
    """Scan .md files and insert into SQLite."""
    count = 0
    if not root.exists():
        return 0
    for path in root.rglob("*.md"):
        try:
            post = frontmatter.load(str(path))
            fm_dict = dict(post.metadata)
            fm = MemoryFrontmatter.from_dict(fm_dict)
            body = post.content.strip()
            body_hash = hash(body) & 0xFFFFFFFF

            conn.execute(
                """INSERT OR REPLACE INTO memories
                   (id, scope, zone, confidence, pinned, rank, created, source,
                    valid_from, valid_until, context_scope, version, supersedes_reason,
                    body_hash, path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fm.id, scope, fm.zone, fm.confidence, int(fm.pinned), fm.rank,
                    fm.created, fm.source, fm.valid_from, fm.valid_until,
                    fm.context_scope, fm.version, fm.supersedes_reason,
                    str(body_hash), str(path),
                ),
            )
            # Tags
            conn.execute("DELETE FROM tags WHERE memory_id = ?", (fm.id,))
            for tag in (fm.tags or []):
                conn.execute(
                    "INSERT OR IGNORE INTO tags (memory_id, tag) VALUES (?, ?)",
                    (fm.id, tag),
                )
            # Supersedes
            for old_id in (fm.supersedes or []):
                conn.execute(
                    "INSERT OR IGNORE INTO supersedes (old_id, new_id, reason) VALUES (?, ?, ?)",
                    (old_id, fm.id, fm.supersedes_reason),
                )
            count += 1
        except Exception as e:
            print(f"  Skip {path}: {e}")
    return count


def migrate_stats(conn: sqlite3.Connection) -> int:
    """Migrate memory-stats.jsonl to SQLite stats table."""
    stats_path = plugin_data_dir() / "memory-stats.jsonl"
    if not stats_path.exists():
        return 0
    count = 0
    with open(stats_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                mem_id = entry.get("memory_id", "")
                event = entry.get("event", "")
                at = entry.get("at", datetime.now(timezone.utc).isoformat())
                if mem_id and event:
                    conn.execute(
                        "INSERT INTO stats (memory_id, event, at) VALUES (?, ?, ?)",
                        (mem_id, event, at),
                    )
                    count += 1
            except Exception:
                continue
    return count


def migrate_graph() -> int:
    """Migrate old graph.db to new schema."""
    old_db = plugin_data_dir() / "graph.db"
    if not old_db.exists():
        return 0
    try:
        old_conn = sqlite3.connect(str(old_db))
        old_conn.row_factory = sqlite3.Row
        # Check if old schema has edges table with different columns
        cursor = old_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r["name"] for r in cursor.fetchall()}
        if "edges" not in tables:
            old_conn.close()
            return 0

        # New graph.db (same file, migrate in place)
        new_conn = sqlite3.connect(str(old_db))
        new_conn.executescript("""
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
            access_count INTEGER NOT NULL DEFAULT 0,
            last_access TEXT,
            importance REAL NOT NULL DEFAULT 0.5,
            strength REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'active'
        );
        """)

        # Try to migrate old edges if schema compatible
        try:
            rows = old_conn.execute(
                "SELECT source_id, target_id, relation, weight, co_occurrence, last_activated FROM edges"
            ).fetchall()
            for r in rows:
                new_conn.execute(
                    """INSERT OR REPLACE INTO edges
                       (source_id, target_id, relation, weight, co_occurrence, last_activated)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (r["source_id"], r["target_id"], r["relation"] if len(r) > 2 else "co_occurs",
                     r["weight"], r["co_occurrence"] if len(r) > 4 else 1,
                     r["last_activated"] if len(r) > 5 else None),
                )
            count = len(rows)
        except Exception as e:
            print(f"  Graph edge migration skipped: {e}")
            count = 0

        # Migrate meta
        if "graph_memory_meta" in tables:
            try:
                rows = old_conn.execute(
                    "SELECT memory_id, access_count, last_access, importance, strength, status FROM graph_memory_meta"
                ).fetchall()
                for r in rows:
                    new_conn.execute(
                        """INSERT OR REPLACE INTO graph_meta
                           (memory_id, access_count, last_access, importance, strength, status)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (r["memory_id"], r["access_count"], r["last_access"],
                         r["importance"], r["strength"], r.get("status", "active")),
                    )
            except Exception as e:
                print(f"  Graph meta migration skipped: {e}")

        new_conn.commit()
        new_conn.close()
        old_conn.close()
        return count
    except Exception as e:
        print(f"  Graph migration failed: {e}")
        return 0


def main():
    print("=" * 60)
    print("Memory Index Migration Tool")
    print("=" * 60)

    db_path = plugin_data_dir() / "memories.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n[1/4] Initializing SQLite database: {db_path}")
    conn = _init_db(db_path)

    print(f"\n[2/4] Migrating memory files...")
    user_root = user_memories_dir()
    proj_root = project_memories_dir()
    user_count = migrate_memories(conn, user_root, "user")
    proj_count = migrate_memories(conn, proj_root, "project") if proj_root else 0
    print(f"  User memories: {user_count}")
    print(f"  Project memories: {proj_count}")

    print(f"\n[3/4] Migrating stats...")
    stats_count = migrate_stats(conn)
    print(f"  Stats entries: {stats_count}")

    conn.commit()
    conn.close()
    print(f"  Database saved: {db_path}")

    print(f"\n[4/4] Migrating graph...")
    graph_count = migrate_graph()
    print(f"  Graph edges: {graph_count}")

    print("\n" + "=" * 60)
    print("Migration complete!")
    print(f"  memories.db: {user_count + proj_count} memories")
    print(f"  stats: {stats_count} entries")
    print(f"  graph.db: {graph_count} edges")
    print("=" * 60)


if __name__ == "__main__":
    main()
