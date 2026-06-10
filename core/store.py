"""SQLite-backed memory store for mem-reflection-hermes.

This module owns MemoryStore plus thin file-I/O helpers; shared helpers live
in sibling core modules and bulk methods live in core/store_methods.py and
core/store_health.py to keep this file under 800 lines.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import queue
import re
import sqlite3
import sys
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import sys as _sys, types as _types

if not __package__ or __package__ not in _sys.modules:
    _pkg = "mem_reflection_hermes.core"
    if _pkg not in _sys.modules:
        _core_mod = _types.ModuleType("mem_reflection_hermes.core")
        _core_mod.__path__ = [str(Path(__file__).resolve().parent)]
        _sys.modules[_pkg] = _core_mod
    if "mem_reflection_hermes" not in _sys.modules:
        _root_mod = _types.ModuleType("mem_reflection_hermes")
        _root_mod.__path__ = [str(Path(__file__).resolve().parent.parent)]
        _sys.modules["mem_reflection_hermes"] = _root_mod
    __package__ = _pkg

from .config import (  # noqa: F401
    CONFIG_KEY_EMBEDDINGS, CONFIG_KEY_ENTITY, CONFIG_KEY_INTENT_PROTOTYPES,
    CONFIG_KEY_MICRO_REFLECTION, CONFIG_KEY_PALACE_INSTRUCTIONS,
    CONFIG_KEY_PALACE_MODE, CONFIG_KEY_PROFILE_MODE, CONFIG_KEY_RERANKER,
    CONFIG_SECTION, embeddings_enabled, hermes_home, load_config,
    micro_reflection_enabled, palace_index_path, palace_mode_enabled,
    plugin_config, plugin_data_dir, profile_mode_enabled,
    project_memories_dir, project_skills_dir, user_memories_dir,
    user_skills_dir, zone_cache_dir,
)
from .entities import (  # noqa: F401
    _extract_entities_spacy, _normalize_entity_text, entity_enabled,
    entity_weight, extract_entities,
)
from .intent import _classify_update_intent, _is_context_mismatch, _is_expired  # noqa: F401
from .lineage import _lineage_cycle_check, _lineage_depth, _lineage_latest, _lineage_root  # noqa: F401
from .models import (  # noqa: F401
    LoadedMemory, LoadedSkill, MemoryEffectiveness, MemoryFrontmatter,
    MemoryStatEntry, SkillFrontmatter, _load_frontmatter_file,
    parse_frontmatter, read_memory, serialize_frontmatter, write_memory_atomic,
)
from .skill_store import SkillStore, _read_skill_file  # noqa: F401
from .tokenization import (  # noqa: F401
    _CJK_STOPWORDS, _STOPWORDS, _bm25_search, _bm25_search_scored,
    _cosine_similarity, _get_jieba_search, _memory_tokens, _tokenise,
    adaptive_conflict_threshold, cjk_ratio, cjk_tokenizer_mode,
    estimate_tokens, is_cjk, normalize_bm25,
)
from .utils import (  # noqa: F401
    _PROJECT_ZONE_PREFIX, _VALID_ZONES, _ZONE_CORE, _ZONE_EPISODE,
    _ZONE_GENERAL, _ZONE_MERGE_THRESHOLD, _ZONE_SEMANTIC,
    _ZONE_SPLIT_THRESHOLD, _ZONE_WORK, fast_hash, is_valid_zone,
    normalize_zone, sanitize_zone_filename,
)

logger = logging.getLogger(__name__)

_stat_write_lock = threading.Lock()


def _stats_path() -> Path:
    return plugin_data_dir() / "memory-stats.jsonl"


def _append_stat_entries(entries: List[Tuple[str, str]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    sp = _stats_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    with _stat_write_lock:
        with open(sp, "a", encoding="utf-8") as f:
            for memory_id, event in entries:
                f.write(
                    json.dumps(
                        {"memory_id": memory_id, "event": event, "at": now},
                        ensure_ascii=False,
                    )
                    + "\n"
                )


def record_memory_stat(memory_id: str, event: str) -> None:
    try:
        _append_stat_entries([(memory_id, event)])
    except Exception:
        logger.warning("Failed to record memory stat for %s", memory_id)


def batch_record_stats(entries: List[Tuple[str, str]]) -> None:
    try:
        _append_stat_entries(entries)
    except Exception:
        logger.warning("Stat sync write failed")


_write_queue: "queue.Queue[Tuple[Path, str, int] | None]" = queue.Queue(maxsize=500)
_pending_writes: Set[Path] = set()
_write_guard_lock = threading.Lock()
_write_path_locks: Dict[str, threading.RLock] = {}
_write_generations: Dict[str, int] = {}


def _write_path_key(path: Path) -> str:
    return str(path.resolve(strict=False))


def _write_path_lock(path: Path) -> threading.RLock:
    key = _write_path_key(path)
    with _write_guard_lock:
        lock = _write_path_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _write_path_locks[key] = lock
        return lock


def _reserve_write_generation(path: Path) -> int:
    key = _write_path_key(path)
    with _write_guard_lock:
        token = _write_generations.get(key, 0) + 1
        _write_generations[key] = token
        return token


def _is_current_write_generation(path: Path, token: int) -> bool:
    return _write_generations.get(_write_path_key(path), 0) == token


def _cleanup_write_generations(path: Path) -> None:
    with _write_guard_lock:
        if path not in _pending_writes:
            key = _write_path_key(path)
            _write_generations.pop(key, None)
            _write_path_locks.pop(key, None)


def _safe_write(path: Path, content: str) -> None:
    import tempfile
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    f = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    )
    try:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    finally:
        f.close()
    for _ in range(5):
        try:
            os.replace(f.name, path)
            return
        except PermissionError:
            time.sleep(0.01)
    os.replace(f.name, path)


def _frontmatter_to_data(fm: MemoryFrontmatter) -> Dict[str, Any]:
    return {
        "id": fm.id,
        "created": fm.created,
        "source": fm.source,
        "confidence": fm.confidence,
        "pinned": fm.pinned,
        "tags": fm.tags,
        "supersedes": fm.supersedes,
        "supersedes_reason": fm.supersedes_reason,
        "valid_from": fm.valid_from,
        "valid_until": fm.valid_until,
        "context_scope": fm.context_scope,
        "zone": fm.zone,
        "rank": fm.rank,
    }


def _write_memory(path: Path, fm: MemoryFrontmatter, body: str) -> None:
    _safe_write(path, serialize_frontmatter(_frontmatter_to_data(fm), body))


def _file_flush_worker() -> None:
    while True:
        try:
            item = _write_queue.get(timeout=1)
        except Exception:
            continue
        if item is None:
            break
        path, content, token = item
        try:
            with _write_path_lock(path):
                if _is_current_write_generation(path, token):
                    _safe_write(path, content)
        except Exception:
            logger.warning("Async write failed for %s", path)
        finally:
            _pending_writes.discard(path)
            _cleanup_write_generations(path)


_write_thread = threading.Thread(target=_file_flush_worker, daemon=True)
_write_thread.start()


def _shutdown_file_writer() -> None:
    _write_queue.put(None)
    _write_thread.join(timeout=5)


import atexit as _atexit

_atexit.register(_shutdown_file_writer)


def async_write_memory(path: Path, fm: MemoryFrontmatter, body: str) -> None:
    content = serialize_frontmatter(_frontmatter_to_data(fm), body)
    token = _reserve_write_generation(path)
    _pending_writes.add(path)
    try:
        _write_queue.put_nowait((path, content, token))
    except queue.Full:
        _pending_writes.discard(path)
        try:
            with _write_path_lock(path):
                if _is_current_write_generation(path, token):
                    _safe_write(path, content)
        except Exception as e:
            logger.warning("Sync write fallback failed for %s: %s", path, e)


def load_effectiveness() -> Dict[str, MemoryEffectiveness]:
    sp = _stats_path()
    if not sp.exists():
        return {}
    eff: Dict[str, MemoryEffectiveness] = {}
    try:
        with open(sp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mid = entry.get("memory_id", "")
                if not mid:
                    continue
                e = eff.setdefault(mid, MemoryEffectiveness())
                ev = entry.get("event", "")
                if ev == "loaded":
                    e.loaded += 1
                elif ev == "referenced":
                    e.referenced += 1
                elif ev == "accessed":
                    e.accessed += 1
                at = entry.get("at")
                if at and (e.last_event_at is None or at > e.last_event_at):
                    e.last_event_at = at
    except Exception as e:
        logger.warning("Failed to load effectiveness stats from %s: %s", sp, e)
    return eff


# Aliases to canonical tokenization implementation (kept for compat)
_bm25_search_scored = _bm25_search_scored
_bm25_search = _bm25_search
_cosine_similarity = _cosine_similarity

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL,
    zone TEXT NOT NULL DEFAULT 'general', confidence TEXT NOT NULL DEFAULT 'medium',
    pinned INTEGER NOT NULL DEFAULT 0, rank INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'user',
    valid_from TEXT, valid_until TEXT, context_scope TEXT,
    version INTEGER NOT NULL DEFAULT 1, supersedes_reason TEXT,
    body_hash TEXT NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mem_zone ON memories(zone);
CREATE INDEX IF NOT EXISTS idx_mem_pinned ON memories(pinned) WHERE pinned = 1;
CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created);
CREATE TABLE IF NOT EXISTS tags (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    tag TEXT NOT NULL, PRIMARY KEY (memory_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tag_name ON tags(tag);
CREATE TABLE IF NOT EXISTS supersedes (
    old_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    new_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    reason TEXT, PRIMARY KEY (old_id, new_id)
);
CREATE TABLE IF NOT EXISTS stats (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    event TEXT NOT NULL, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stats_mem ON stats(memory_id);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, text TEXT NOT NULL, normalized TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_normalized ON entities(normalized);
CREATE TABLE IF NOT EXISTS entity_links (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL DEFAULT 'regex',
    PRIMARY KEY (entity_id, memory_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_links_memory ON entity_links(memory_id);
CREATE INDEX IF NOT EXISTS idx_entity_links_entity ON entity_links(entity_id);
"""


class MemoryStore:
    """SQLite-indexed memory store with Markdown file persistence."""

    def __init__(
        self,
        user_root: Path,
        project_root: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ):
        self.user_root = user_root
        self.project_root = project_root
        self._lock = threading.RLock()
        self._local = threading.local()
        self._db_path = db_path if db_path is not None else plugin_data_dir() / "memories.db"
        self._search_index = None
        self._graph = None
        self._index_dirty = True
        self._cached_index = ""
        self._last_index_hash = ""
        self._post_delete_callbacks: List[Callable[[str], None]] = []
        self._init_db()
        self._sync_from_disk()

    def _mark_changed(self) -> None:
        self._index_dirty = True
        self._cached_index = ""
        if self._search_index is not None:
            self._search_index.invalidate_cache()

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.Error:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()
        self._ensure_body_column(conn)

    @staticmethod
    def _ensure_body_column(conn: sqlite3.Connection) -> None:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "body" not in cols:
            conn.execute("ALTER TABLE memories ADD COLUMN body TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def _sync_from_disk(self) -> None:
        conn = self._get_conn()
        disk_ids: Set[str] = set()
        for scope, root in (("user", self.user_root), ("project", self.project_root)):
            if root is None or not root.exists():
                continue
            for f in root.rglob("*.md"):
                m = read_memory(f, scope)
                if m is None:
                    continue
                disk_ids.add(m.id())
                self._upsert_memory_row(conn, m)
        existing = {r["id"] for r in conn.execute("SELECT id FROM memories").fetchall()}
        for sid in existing - disk_ids:
            conn.execute("DELETE FROM memories WHERE id = ?", (sid,))
        self._cleanup_orphan_entities(conn)
        conn.commit()

    def _upsert_memory_row(self, conn: sqlite3.Connection, m: LoadedMemory) -> None:
        fm = m.frontmatter
        body_hash = hashlib.sha256(m.body.encode("utf-8")).hexdigest()[:16]
        conn.execute(
            """INSERT OR REPLACE INTO memories
               (id, scope, zone, confidence, pinned, rank, created, source,
                valid_from, valid_until, context_scope, version,
                supersedes_reason, body_hash, path, body)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                fm.id,
                m.scope,
                fm.zone,
                fm.confidence,
                int(fm.pinned),
                fm.rank,
                fm.created,
                fm.source,
                fm.valid_from,
                fm.valid_until,
                fm.context_scope,
                fm.version,
                fm.supersedes_reason,
                body_hash,
                str(m.source_path),
                m.body,
            ),
        )
        conn.execute("DELETE FROM tags WHERE memory_id = ?", (fm.id,))
        for tag in fm.tags or []:
            conn.execute("INSERT OR IGNORE INTO tags (memory_id, tag) VALUES (?, ?)", (fm.id, tag))
        conn.execute("DELETE FROM supersedes WHERE new_id = ?", (fm.id,))
        for old_id in fm.supersedes or []:
            conn.execute(
                "INSERT OR IGNORE INTO supersedes (old_id, new_id, reason) VALUES (?, ?, ?)",
                (old_id, fm.id, fm.supersedes_reason),
            )
        self._refresh_entity_links(conn, m)

    def _refresh_entity_links(self, conn: sqlite3.Connection, m: LoadedMemory) -> None:
        if not entity_enabled():
            return
        try:
            extracted = extract_entities(m.body + "\n" + " ".join(m.frontmatter.tags or []))
        except Exception:
            logger.debug("Entity extraction failed for %s", m.id(), exc_info=True)
            extracted = []
        conn.execute("DELETE FROM entity_links WHERE memory_id = ?", (m.id(),))
        now = datetime.now(timezone.utc).isoformat()
        for entity in extracted:
            row = conn.execute(
                "SELECT id FROM entities WHERE normalized = ?", (entity["normalized"],)
            ).fetchone()
            if row is None:
                entity_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO entities (id, text, normalized, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (entity_id, entity["text"], entity["normalized"], entity["type"], now, now),
                )
            else:
                entity_id = row["id"]
                conn.execute(
                    "UPDATE entities SET text = ?, type = ?, updated_at = ? WHERE id = ?",
                    (entity["text"], entity["type"], now, entity_id),
                )
            conn.execute(
                "INSERT OR REPLACE INTO entity_links (entity_id, memory_id, weight, source) VALUES (?, ?, ?, ?)",
                (entity_id, m.id(), float(entity.get("weight", 1.0)), entity["type"]),
            )
        self._cleanup_orphan_entities(conn)

    @staticmethod
    def _cleanup_orphan_entities(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM entities WHERE id NOT IN (SELECT entity_id FROM entity_links)")

    def _validate_supersedes_targets(self, conn: sqlite3.Connection, fm: MemoryFrontmatter) -> None:
        missing = [
            old
            for old in fm.supersedes or []
            if conn.execute("SELECT 1 FROM memories WHERE id = ?", (old,)).fetchone() is None
        ]
        if missing:
            raise ValueError(f"Cannot supersede missing memory id(s): {', '.join(missing)}")

    def _row_to_loaded(self, row: sqlite3.Row) -> Optional[LoadedMemory]:
        body = row["body"] or ""
        if not body:
            return self._row_to_loaded_from_disk(row)
        try:
            tags = [
                r["tag"]
                for r in self._get_conn()
                .execute("SELECT tag FROM tags WHERE memory_id = ?", (row["id"],))
                .fetchall()
            ]
            supers = [
                r["old_id"]
                for r in self._get_conn()
                .execute("SELECT old_id FROM supersedes WHERE new_id = ?", (row["id"],))
                .fetchall()
            ]
            fm = MemoryFrontmatter(
                id=row["id"],
                created=row["created"],
                source=row["source"],
                confidence=row["confidence"],
                pinned=bool(row["pinned"]),
                tags=tags,
                supersedes=supers,
                zone=row["zone"],
                rank=row["rank"],
                version=row["version"],
                supersedes_reason=row["supersedes_reason"],
                valid_from=row["valid_from"],
                valid_until=row["valid_until"],
                context_scope=row["context_scope"],
            )
            return LoadedMemory(
                frontmatter=fm, body=body, source_path=Path(row["path"]), scope=row["scope"]
            )
        except Exception as exc:
            logger.warning("SQLite row parse failed for %s: %s", row.get("id", "?"), exc)
            return self._row_to_loaded_from_disk(row)

    def _row_to_loaded_from_disk(self, row: sqlite3.Row) -> Optional[LoadedMemory]:
        path = Path(row["path"])
        return read_memory(path, row["scope"]) if path.exists() else None

    def _root_for(self, scope: str) -> Path:
        if scope == "user":
            return self.user_root
        if scope == "project":
            if self.project_root is None:
                raise ValueError("Project scope requested but no project root configured")
            return self.project_root
        raise ValueError(f"Unknown scope: {scope}")

    def put(self, scope: str, fm: MemoryFrontmatter, body: str) -> Path:
        with self._lock:
            conn = self._get_conn()
            if conn.execute("SELECT id FROM memories WHERE id = ?", (fm.id,)).fetchone():
                raise ValueError(f"Duplicate memory id: {fm.id}")
            self._validate_supersedes_targets(conn, fm)
            root = self._root_for(scope)
            raw_date = fm.created[:10] if fm.created else datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = root / f"{re.sub(r'[\\/]', '_', raw_date)}-{re.sub(r'[^a-zA-Z0-9_-]', '_', fm.id[:16])}.md"
            write_memory_atomic(path, fm, body)
            self._upsert_memory_row(conn, LoadedMemory(frontmatter=fm, body=body.strip(), source_path=path, scope=scope))
            conn.commit()
            self._mark_changed()
            return path

    def get(self, mem_id: str) -> Optional[LoadedMemory]:
        row = self._get_conn().execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
        return self._row_to_loaded(row) if row else None

    def get_by_id(self, mem_id: str) -> Optional[LoadedMemory]:
        return self.get(mem_id)

    def delete(self, scope: str, mem_id: str) -> bool:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute("SELECT path FROM memories WHERE id = ?", (mem_id,)).fetchone()
            if row is None:
                return False
            path = Path(row["path"]).resolve()
            expected_root = self._root_for(scope).resolve()
            if not str(path).startswith(str(expected_root)):
                logger.warning("Rejecting delete of out-of-bounds path: %s", path)
                return False
            if path.exists():
                try:
                    path.unlink()
                except OSError as e:
                    logger.warning("Failed to delete memory file %s: %s", path, e)
                    return False
            conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
            self._cleanup_orphan_entities(conn)
            conn.commit()
            self._mark_changed()
            for cb in self._post_delete_callbacks:
                try:
                    cb(mem_id)
                except Exception:
                    logger.warning("Post-delete callback failed for %s", mem_id, exc_info=True)
            return True

    def update(
        self,
        mem_id: str,
        body: Optional[str] = None,
        zone: Optional[str] = None,
        confidence: Optional[str] = None,
        tags: Optional[List[str]] = None,
        pinned: Optional[bool] = None,
        supersedes: Optional[List[str]] = None,
    ) -> Optional[LoadedMemory]:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
            if row is None:
                raise ValueError(f"Memory not found: {mem_id}")
            loaded = self._row_to_loaded(row)
            if loaded is None:
                raise ValueError(f"Memory file missing on disk: {mem_id}")
            fm = loaded.frontmatter
            new_fm = MemoryFrontmatter(
                id=mem_id,
                created=fm.created,
                source=fm.source,
                confidence=confidence if confidence is not None else fm.confidence,
                pinned=pinned if pinned is not None else fm.pinned,
                tags=tags if tags is not None else fm.tags,
                supersedes=supersedes if supersedes is not None else fm.supersedes,
                supersedes_reason=fm.supersedes_reason,
                valid_from=fm.valid_from,
                valid_until=fm.valid_until,
                context_scope=fm.context_scope,
                zone=zone if zone is not None else fm.zone,
                rank=fm.rank,
            )
            new_body = body if body is not None else loaded.body
            write_memory_atomic(loaded.source_path, new_fm, new_body)
            updated = LoadedMemory(
                frontmatter=new_fm, body=new_body.strip(), source_path=loaded.source_path, scope=loaded.scope
            )
            self._upsert_memory_row(conn, updated)
            conn.commit()
            self._mark_changed()
            return updated

    _LIST_SORT_ORDERS: Dict[str, str] = {
        "rank": "rank DESC, created DESC",
        "created": "created DESC",
        "created_asc": "created ASC",
        "zone": "zone ASC, rank DESC",
    }

    def list(
        self,
        *,
        zone: Optional[str] = None,
        active_only: bool = False,
        sort: str = "rank",
        limit: Optional[int] = None,
    ) -> List[LoadedMemory]:
        conn = self._get_conn()
        clauses: List[str] = []
        params: List[Any] = []
        if zone:
            clauses.append("zone = ?")
            params.append(normalize_zone(zone))
        if active_only:
            clauses.append("id NOT IN (SELECT old_id FROM supersedes)")
        where = " AND ".join(clauses) if clauses else "1=1"
        order = self._LIST_SORT_ORDERS.get(sort, self._LIST_SORT_ORDERS["rank"])
        sql = f"SELECT * FROM memories WHERE {where} ORDER BY {order}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        return [m for r in conn.execute(sql, params).fetchall() if (m := self._row_to_loaded(r)) is not None]

    def list_active(self) -> List[LoadedMemory]:
        return self.list(active_only=True)

    def list_pinned(self) -> List[LoadedMemory]:
        rows = self._get_conn().execute(
            "SELECT m.* FROM memories m WHERE m.pinned = 1 AND m.id NOT IN (SELECT old_id FROM supersedes) ORDER BY m.rank DESC, m.created DESC"
        ).fetchall()
        return [m for r in rows if (m := self._row_to_loaded(r)) is not None]

    def list_by_zone(self, zone: str) -> List[LoadedMemory]:
        return self.list(zone=zone, active_only=True)

    def group_by_zone(self) -> Dict[str, List[LoadedMemory]]:
        groups: Dict[str, List[LoadedMemory]] = {}
        for memory in self.list_active():
            groups.setdefault(memory.frontmatter.zone, []).append(memory)
        return groups

    def _get_search_index(self):
        if self._search_index is None:
            try:
                from .search import SearchIndex
            except ImportError:
                search_path = Path(__file__).resolve().with_name("search.py")
                spec = importlib.util.spec_from_file_location("_memory_search_module", search_path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules["_memory_search_module"] = mod
                spec.loader.exec_module(mod)
                SearchIndex = mod.SearchIndex
            try:
                from .reranker import _build_reranker
                reranker = _build_reranker(plugin_config().get(CONFIG_KEY_RERANKER, {}))
            except Exception:
                reranker = None
            self._search_index = SearchIndex(self, graph=self._graph, reranker=reranker)
        return self._search_index

    def set_graph(self, graph) -> None:
        self._graph = graph
        if self._search_index is not None:
            self._search_index._graph = graph
            self._search_index.invalidate_cache()

    def search(self, query: str, k: int = 5, include_history: bool = False, zone: Optional[str] = None) -> List[LoadedMemory]:
        return self._get_search_index().search(query, k=k, zone=zone, include_history=include_history)

    def fusion_search(self, query: str, k: int = 5, zone: Optional[str] = None, include_history: bool = False, **kwargs) -> List[LoadedMemory]:
        return self._get_search_index().search(query, k=k, zone=zone, include_history=include_history, **kwargs)

    def fusion_search_explain(self, query: str, k: int = 5, zone: Optional[str] = None, include_history: bool = False, **kwargs) -> Dict[str, Any]:
        return self._get_search_index().search_explain(query, k=k, zone=zone, include_history=include_history, **kwargs)

    def search_backend_capabilities(self):
        from .backend import default_sqlite_backend_capabilities
        return default_sqlite_backend_capabilities(entity_search=entity_enabled(), vector_search=embeddings_enabled())

    def entity_links_for_memory(self, memory_id: str) -> List[Dict[str, Any]]:
        from . import store_methods as _sm
        return _sm.entity_links_for_memory(self, memory_id)

    def compute_entity_boosts(
        self, query: str, candidate_ids: Optional[Set[str]] = None
    ) -> Tuple[Dict[str, float], Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
        from . import store_methods as _sm
        return _sm.compute_entity_boosts(self, query, candidate_ids)

    def check_conflict(self, body: str, threshold: Optional[float] = None, exclude_ids: Optional[List[str]] = None) -> Optional[Tuple[str, float]]:
        return self._get_search_index().check_conflict(body, threshold=threshold, exclude_ids=exclude_ids)

    def zone_counts(self) -> Dict[str, int]:
        rows = self._get_conn().execute(
            "SELECT zone, COUNT(*) as cnt FROM memories WHERE id NOT IN (SELECT old_id FROM supersedes) GROUP BY zone"
        ).fetchall()
        return {r["zone"]: r["cnt"] for r in rows}

    def is_superseded(self, mem_id: str) -> bool:
        return self._get_conn().execute("SELECT 1 FROM supersedes WHERE old_id = ?", (mem_id,)).fetchone() is not None

    def latest_for(self, mem_id: str) -> Optional[LoadedMemory]:
        from . import store_methods as _sm
        return _sm.latest_for(self, mem_id)

    def lineage_chain(self, mem_id: str, max_depth: int = 10) -> List[LoadedMemory]:
        from . import store_methods as _sm
        return _sm.lineage_chain(self, mem_id, max_depth)

    def _calc_supersedes_depth(
        self, mem_id: str, visited: Optional[Set[str]] = None, max_depth: int = 10, depth: int = 0
    ) -> int:
        from . import lineage as _lineage_mod
        return _lineage_mod._calc_supersedes_depth(self, mem_id, visited, max_depth, depth)

    def record_stat(self, memory_id: str, event: str) -> None:
        from . import store_methods as _sm
        return _sm.record_stat(self, memory_id, event)

    def effectiveness(self, memory_id: Optional[str] = None) -> Dict[str, MemoryEffectiveness]:
        from . import store_methods as _sm
        return _sm.effectiveness(self, memory_id)

    def health_metrics(self) -> Dict[str, Any]:
        from . import store_health as _sh
        return _sh.health_metrics(self)

    def rebuild_index(self) -> Dict[str, Any]:
        from . import store_health as _sh
        return _sh.rebuild_index(self)

    def validate_index(self) -> Dict[str, Any]:
        from . import store_health as _sh
        return _sh.validate_index(self)

    def prune_index(self) -> Dict[str, Any]:
        from . import store_health as _sh
        return _sh.prune_index(self)

    def reorder(self, memory_ids: List[str]) -> List[str]:
        with self._lock:
            conn = self._get_conn()
            for idx, mid in enumerate(memory_ids):
                conn.execute("UPDATE memories SET rank = ? WHERE id = ?", (len(memory_ids) - idx, mid))
            conn.commit()
            self._mark_changed()
            return list(memory_ids)
