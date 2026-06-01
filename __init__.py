"""mem-reflection-hermes plugin -- Self-evolving memory and reflection system.

Ported from https://github.com/coder-brzhang/small-rust-hermes

v0.9.2-beta Architecture (9 modules, approx 7,200 lines):
- core.py: MemoryStore, SkillStore, LoadedMemory, LoadedSkill, config, paths, BM25
- embed.py: ONNX embedding engine, cosine similarity, intent classification
- reflection.py: micro/full reflection pipelines, auto-rebalance, profile compilation
- hooks.py: session hooks (on_session_start/end, pre_llm_call, post_tool_call)
- tools.py: 17 SRH tool handlers exposed to Hermes Agent
- ahe_graph/__init__.py: SQLite-backed Hebbian graph, association engine, decay
- cluqi.py: Cross-Layer Unified Query Interface (Memory + Graph + Supersedes)
- pagerank.py: PageRank centrality computation for graph nodes
- query_cache.py: Query templates and TTL-based result cache
- cross_zone.py: Cross-zone graph analysis (bridges, centrality, recommendations)
- __init__.py: registration, exports, backward compat, standalone bootstrap

Features:
- Structured memories: Markdown + YAML frontmatter (id, created, source,
  confidence, pinned, tags, supersedes, zone, rank, version)
- Dual scope: user (~/.hermes/memories/) and project (./.hermes/memories/)
- Memory Palace: zone-based organization (core, work, episode, general, project:*)
- TF-IDF / BM25 search with effectiveness boosting (zero-dependency, approx 0.8ms/50 mems)
- Semantic search: ONNX Runtime + all-MiniLM-L6-v2, 16x faster than PyTorch (optional)
- Conflict detection on write with supersedes chains and version lineage
- Effectiveness tracking: per-memory scoring with exponential time decay
- Micro-reflection: lightweight per-turn background reflection with backpressure queue
- Full reflection: session-end structured JSON pipeline with human approval for skills
- Skill auto-matching: token-overlap + optional embedding hybrid, always-active skills
- Context layering: Pinned -> Active Index -> Triggered Skills -> Always-Active Skills
- Profile compilation: LLM-driven compilation of all memories into structured profile docs
- ahe_graph integration: graph memory with associate/retrieve/stats/viz tools,
  Hebbian co-occurrence learning, Ebbinghaus decay, adaptive retrieval router
- CLUQI: unified cross-layer query joining MemoryStore, GraphStore, and supersedes
- PageRank: centrality scores for identifying hub memories in the graph
- Query templates: 8 predefined patterns (recent, by_zone, by_tag, graph_neighbors, etc.)
- Result cache: TTL-based caching for BM25 and fusion search results
- Cross-zone analysis: bridge memories, zone centrality, zone recommendations
- Dashboard: React-based UI with graph visualization, CLUQI search, zone analysis

v0.9.1 (2026-05-31): CLUQI, PageRank, SUPERSEDES edges, query templates, result cache, cross-zone analysis
v0.9.2-beta (2026-06-01): Dashboard full ahe_graph integration, node click highlighting, zone bridge UI
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Import from submodules: models, constants, BM25, frontmatter, IO
from .core import (  # noqa: F401
    hermes_home, load_config, plugin_config, plugin_data_dir,
    user_memories_dir, project_memories_dir, user_skills_dir, project_skills_dir,
    embeddings_enabled, micro_reflection_enabled, palace_mode_enabled, profile_mode_enabled,
    normalize_zone, is_valid_zone, fast_hash,
    palace_index_path, zone_cache_dir, sanitize_zone_filename,
    MemoryFrontmatter, LoadedMemory, MemoryStatEntry, MemoryEffectiveness,
    SkillFrontmatter, LoadedSkill,
    parse_frontmatter, serialize_frontmatter, read_memory,
    async_write_memory, record_memory_stat, batch_record_stats, load_effectiveness,
    is_cjk, cjk_ratio, adaptive_conflict_threshold,
    _tokenise, _memory_tokens, _bm25_search, _bm25_search_scored, _cosine_similarity,
    _ZONE_CORE, _ZONE_WORK, _ZONE_EPISODE, _ZONE_GENERAL,
    _VALID_ZONES, _PROJECT_ZONE_PREFIX,
    _ZONE_SPLIT_THRESHOLD, _ZONE_MERGE_THRESHOLD,
    _write_queue, _pending_writes,
)

# Backward-compat aliases (old underscore names used by remaining __init__.py code)
_hermes_home = hermes_home
_load_config = load_config
_get_config = plugin_config
_plugin_data_dir = plugin_data_dir
_user_memories_dir = user_memories_dir
_project_memories_dir = project_memories_dir
_user_skills_dir = user_skills_dir
_project_skills_dir = project_skills_dir
_embeddings_enabled = embeddings_enabled
_micro_reflection_enabled = micro_reflection_enabled
_palace_mode_enabled = palace_mode_enabled
_profile_mode_enabled = profile_mode_enabled
_palace_index_path = palace_index_path
_zone_cache_dir = zone_cache_dir
_sanitize_zone_filename = sanitize_zone_filename
_normalize_zone = normalize_zone
_async_write_memory = async_write_memory
_stats_path = lambda: plugin_data_dir() / "memory-stats.jsonl"

logger = logging.getLogger(__name__)

# Register module in sys.modules early to avoid dataclass resolution failure
# when loaded via importlib.util (Python 3.11 bug workaround).
if __name__ != "__main__" and __name__ not in sys.modules:
    import types
    # Fallback chain: __spec__.name → __name__ → create fresh module
    mod_name = getattr(__spec__, "name", None) if "__spec__" in globals() else None
    if mod_name is None:
        mod_name = __name__
    # Register the current executing module object, not a placeholder
    sys.modules[mod_name] = sys.modules.get(mod_name) or sys.modules.get(__name__) or types.ModuleType(mod_name)

# ---------------------------------------------------------------------------
# AI instruction docstring (injected into register() palace_instructions)
# ---------------------------------------------------------------------------
# CJK-aware token estimation (mirrors small-rust-hermes compaction.rs)
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Estimate token count with CJK awareness (fast bytes-based, P1-1).

    CJK/Unicode text → ~3 bytes per token.
    ASCII text → ~4 bytes per token.
    The hybrid byte-count approach is ~600x faster than char-by-char CJK range checks
    while staying within ±15% of tiktoken cl100k_base for mixed CJK+English text.
    """
    if not text:
        return 0
    encoded = text.encode("utf-8")
    n_bytes = len(encoded)
    # Fast path: mostly ASCII text
    if n_bytes <= len(text) * 1.2:
        return (n_bytes + 3) // 4
    # Mixed CJK: UTF-8 multi-byte characters use 3 bytes each → ~1.5 chars/token
    return (n_bytes + 2) // 3


_PALACE_USAGE_INSTRUCTIONS = """## Memory Palace
Your persistent memory is organized in a Memory Palace with zones.
- Use `srh_palace_zones` to see available zones and their counts
- Use `srh_palace_read_zone` to load all memories from a specific zone
- Use `srh_palace_recall` to search by topic, optionally scoped to a zone
- Use `srh_memory_write` (with zone parameter) to persist new learnings
- Use `srh_memory_delete` to remove outdated memories
Do NOT save task progress, session outcomes, or temporary TODO state here.
Session summaries go to `episode` zone. User identity/preferences go to `core`.
Current work focus goes to `work`. Everything else goes to `general`.
Don't guess about preferences or conventions — load the relevant zone first."""

# ---------------------------------------------------------------------------
# Palace Index & Zone Cache
# ---------------------------------------------------------------------------

def build_palace_index(memories: List[LoadedMemory]) -> str:
    """Generate a code-based palace index (no LLM needed).

    Groups memories by zone, shows counts and first-line previews.
    If ahe_graph is active, appends intra-zone graph cluster density.
    Typically ~200-400 tokens.
    """
    groups: Dict[str, List[LoadedMemory]] = {}
    for m in memories:
        groups.setdefault(m.frontmatter.zone, []).append(m)

    if not groups:
        return "## Memory Palace\nEmpty — no memories yet."

    total = len(memories)
    buf = f"## Memory Palace\n{total} memories across {len(groups)} zones. Use srh_palace_read_zone to load details.\n"

    # ── Scheme B: Query graph density per zone ────────────────
    intra_zone_edges: Dict[str, int] = {}
    try:
        gm = _get_graph_mgr()
        if gm is not None:
            for zone, mems in groups.items():
                mids = set(m.id() for m in mems)
                edge_count = 0
                for m in mems:
                    neighbors = gm.store.get_neighbors(m.id(), min_weight=0.1, limit=50)
                    for n in neighbors:
                        if n["memory_id"] in mids:
                            edge_count += 1
                # Each edge counted twice (once per direction), divide by 2
                intra_zone_edges[zone] = edge_count // 2
    except Exception:
        pass  # Graceful degradation: no graph data

    # Zones in consistent order: core > work > episode > general > custom
    zone_order = ["core", "work", "episode", "general"]
    sorted_zones = sorted(groups.keys(), key=lambda z: (
        (zone_order.index(z), z) if z in zone_order else (99, z)
    ))

    for zone in sorted_zones:
        mems = groups[zone]
        # Re-sort: core/work zone zones by predefined order
        if zone in zone_order:
            idx = zone_order.index(zone)
        else:
            idx = 99
        # Append graph cluster annotation if dense
        edge_count = intra_zone_edges.get(zone, 0)
        cluster_tag = ""
        if edge_count >= 2:
            cluster_tag = f"  [关联簇: {edge_count}个连接]"
        buf += f"\n### {zone} ({len(mems)}){cluster_tag}\n"
        for m in mems[:5]:
            line = m.body.split("\n")[0].strip()[:80]
            buf += f"- {line}\n"
        if len(mems) > 5:
            buf += f"- ... ({len(mems) - 5} more)\n"
    return buf


def load_zone_summary(zone: str) -> Optional[str]:
    """Load a cached zone summary if available."""
    safe = _sanitize_zone_filename(zone)
    path = _zone_cache_dir() / f"{safe}.md"
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    return content or None


def save_zone_summary(zone: str, content: str) -> Path:
    """Save a zone summary atomically (tmp + rename)."""
    safe = _sanitize_zone_filename(zone)
    d = _zone_cache_dir()
    path = d / f"{safe}.md"
    tmp = d / f".{safe}.md.tmp"
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)
    return path


def _plugin_data_dir() -> Path:
    """Plugin data directory (for pending skills, logs, etc.)."""
    # Use the directory containing this file as the plugin root
    plugin_root = Path(__file__).parent.resolve()
    return plugin_root


def _user_memories_dir() -> Path:
    """User-level memories directory."""
    return _hermes_home() / "memories"


def _project_memories_dir() -> Optional[Path]:
    """Project-level memories directory (only if .hermes/ exists in cwd)."""
    p = Path.cwd() / ".hermes" / "memories"
    if not p.exists():
        return None
    # Guard: if project memories dir resolves to the same path as user memories,
    # return None to avoid duplicate scanning (happens when cwd is ~).
    user = _user_memories_dir()
    if p.resolve() == user.resolve():
        return None
    return p


# ---------------------------------------------------------------------------
# Memory Store
# ---------------------------------------------------------------------------

class MemoryStore:
    def __init__(self, user_root: Path, project_root: Optional[Path] = None):
        self.user_root = user_root
        self.project_root = project_root
        self._embed_index: Optional[Any] = None
        self._embed_lock = threading.Lock()
        self._effectiveness_cache: Optional[Dict[str, MemoryEffectiveness]] = None  # lazy-loaded from JSONL
        self._doc_tokens: Optional[List[Tuple[str, List[str]]]] = None  # cached (id, tokens) for TF-IDF
        self._cache: Dict[str, Any] = {}  # In-memory cache
        self._cache_valid = False
        self._id_to_path: Dict[str, Path] = {}  # O(1) delete: memory id → file path
        self._index_dirty: bool = True  # P2-1: event-driven palace index rebuild
        self._last_index_hash: str = ""  # P0-1: write-on-change
        self._cached_index: str = ""  # Cached built index string (avoids rebuild on warm path)

    # -- listing --------------------------------------------------------------

    def _invalidate_cache(self) -> None:
        self._cache_valid = False

    def _update_cache_for_put(self, scope: str, fm: MemoryFrontmatter, body: str, path: Path) -> None:
        """Incrementally update cache after put() without re-reading all files."""
        if not self._cache_valid:
            return  # Will be rebuilt on next access
        loaded = LoadedMemory(frontmatter=fm, body=body.strip(), source_path=path, scope=scope)
        # O(1) id→path index
        self._id_to_path[fm.id] = path
        # Insert into 'all' maintaining sort order
        all_mems = self._cache["all"]
        # Find insertion point
        inserted = False
        for i, m in enumerate(all_mems):
            if m.id() > fm.id:
                all_mems.insert(i, loaded)
                inserted = True
                break
        if not inserted:
            all_mems.append(loaded)
        # Update active if not superseded
        if fm.id not in self._cache["superseded"]:
            self._cache["active"].append(loaded)
            if fm.pinned:
                self._cache["pinned"].append(loaded)
        # Update superseded set
        for old_id in fm.supersedes:
            self._cache["superseded"].add(old_id)
            # Remove superseded from active/pinned
            self._cache["active"] = [m for m in self._cache["active"] if m.id() != old_id]
            self._cache["pinned"] = [m for m in self._cache["pinned"] if m.id() != old_id]
        self._doc_tokens = None  # invalidate on mutation
        self._index_dirty = True  # P2-1: mark palace index for rebuild
        self._cached_index = ""  # Invalidate cached index

    def _update_cache_for_delete(self, mem_id: str) -> None:
        """Incrementally update cache after delete() without re-reading all files."""
        if not self._cache_valid:
            return
        self._cache["all"] = [m for m in self._cache["all"] if m.id() != mem_id]
        self._cache["active"] = [m for m in self._cache["active"] if m.id() != mem_id]
        self._cache["pinned"] = [m for m in self._cache["pinned"] if m.id() != mem_id]
        self._cache["superseded"].discard(mem_id)
        self._doc_tokens = None  # invalidate on mutation
        self._id_to_path.pop(mem_id, None)  # P0-2: clean up id→path index
        self._index_dirty = True  # P2-1: mark palace index for rebuild
        self._cached_index = ""  # Invalidate cached index

    def _ensure_cache(self) -> None:
        if self._cache_valid:
            return
        all_mems: List[LoadedMemory] = []
        self._id_to_path.clear()  # P0-2: rebuild id→path index
        for scope, root in (("user", self.user_root), ("project", self.project_root)):
            if root is None or not root.exists():
                continue
            for f in root.iterdir():
                if f.suffix == ".md":
                    m = _read_memory(f, scope)
                    if m:
                        all_mems.append(m)
                        self._id_to_path[m.id()] = f  # P0-2: populate O(1) index
        all_mems.sort(key=lambda m: m.id())

        superseded: Set[str] = set()
        for m in all_mems:
            for old in m.frontmatter.supersedes:
                superseded.add(old)

        self._cache = {
            "all": all_mems,
            "active": [m for m in all_mems if m.id() not in superseded],
            "pinned": [m for m in all_mems if m.frontmatter.pinned and m.id() not in superseded],
            "superseded": superseded,
        }
        self._cache_valid = True

    def list(self) -> List[LoadedMemory]:
        self._ensure_cache()
        return list(self._cache["all"])

    def list_active(self) -> List[LoadedMemory]:
        self._ensure_cache()
        return list(self._cache["active"])

    def list_pinned(self) -> List[LoadedMemory]:
        self._ensure_cache()
        return list(self._cache["pinned"])

    def list_by_zone(self, zone: str) -> List[LoadedMemory]:
        """Return all active memories in a given zone."""
        return [m for m in self.list_active() if m.frontmatter.zone == zone]

    def group_by_zone(self) -> Dict[str, List[LoadedMemory]]:
        """Group active memories by zone, returning a dict of zone→memories."""
        groups: Dict[str, List[LoadedMemory]] = {}
        for m in self.list_active():
            groups.setdefault(m.frontmatter.zone, []).append(m)
        return groups

    def zone_counts(self) -> Dict[str, int]:
        """Return {zone: count} for all active memories."""
        return {zone: len(mems) for zone, mems in self.group_by_zone().items()}

    def get(self, mem_id: str) -> Optional[LoadedMemory]:
        self._ensure_cache()
        for m in self._cache["all"]:
            if m.id() == mem_id:
                return m
        return None

    # -- write ----------------------------------------------------------------

    def put(self, scope: str, fm: MemoryFrontmatter, body: str) -> Path:
        if self.get(fm.id):
            raise ValueError(f"Duplicate memory id: {fm.id}")
        root = self._root_for(scope)
        date_prefix = fm.created[:10] if fm.created else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        short = fm.id[:16]
        path = root / f"{date_prefix}-{short}.md"
        _async_write_memory(path, fm, body)  # P2-2: async disk I/O
        self._id_to_path[fm.id] = path  # P0-2: O(1) id→path
        self._update_cache_for_put(scope, fm, body, path)
        # Try to index embedding
        self._try_index(fm.id, body)
        return path

    def delete(self, scope: str, mem_id: str) -> bool:
        # P0-2: O(1) lookup via id→path index
        path = self._id_to_path.get(mem_id)
        if path is not None:
            # P2-2: if file is still pending async write, just remove from queue tracking
            _pending_writes.discard(path)
            if path.exists():
                try:
                    path.unlink()
                except OSError as e:
                    logger.warning("Failed to delete memory file %s: %s", path, e)
                    return False
            self._id_to_path.pop(mem_id, None)
            self._update_cache_for_delete(mem_id)
            self._try_remove_index(mem_id)
            return True
        # Fallback: directory scan (backward compat, if index missed)
        root = self._root_for(scope)
        for f in root.iterdir():
            if f.suffix != ".md":
                continue
            m = _read_memory(f, scope)
            if m and m.id() == mem_id:
                f.unlink()
                self._id_to_path.pop(mem_id, None)
                self._update_cache_for_delete(mem_id)
                self._try_remove_index(mem_id)
                return True
        return False

    # -- atomic update / reorder (dashboard API) --------------------------------

    def update(self, mem_id: str, body: Optional[str] = None,
               zone: Optional[str] = None, confidence: Optional[str] = None,
               tags: Optional[List[str]] = None, pinned: Optional[bool] = None) -> LoadedMemory:
        """Atomically update a memory's content or metadata.

        Handles file write, cache invalidation, and index updates in one
        operation.  Preserves data on write failure (write-then-delete swap).
        """
        mem = self.get(mem_id)
        if not mem:
            raise ValueError(f"Memory not found: {mem_id}")

        # Build updated frontmatter
        fm = MemoryFrontmatter(
            id=mem_id,
            created=mem.frontmatter.created,
            source=mem.frontmatter.source,
            confidence=confidence if confidence is not None else mem.frontmatter.confidence,
            pinned=pinned if pinned is not None else mem.frontmatter.pinned,
            tags=tags if tags is not None else mem.frontmatter.tags,
            supersedes=mem.frontmatter.supersedes,
            zone=zone if zone is not None else mem.frontmatter.zone,
            rank=mem.frontmatter.rank,
        )
        new_body = body if body is not None else mem.body

        # Write new file FIRST (preserves data on write failure)
        new_path = self._root_for(mem.scope) / f"{fm.created[:10]}-{fm.id[:16]}.md"
        _write_memory(new_path, fm, new_body)

        # Delete old file only after successful write AND only if path changed
        if new_path != mem.source_path:
            self.delete(mem.scope, mem_id)

        # Rebuild cache atomically: invalidate + re-add
        self._invalidate_cache()
        self._id_to_path[fm.id] = new_path
        self._update_cache_for_put(mem.scope, fm, new_body, new_path)
        # Force all derived indices dirty even for same-path updates
        self._index_dirty = True
        self._cached_index = ""
        self._try_index(fm.id, new_body)

        # Return updated memory
        return LoadedMemory(frontmatter=fm, body=new_body.strip(), source_path=new_path, scope=mem.scope)

    def reorder(self, memory_ids: List[str]) -> List[str]:
        """Reorder memories by assigning explicit rank values.

        The new order is determined by the provided memory_ids list.
        Earlier items get higher rank (appear first when sorted by rank desc).
        This avoids the timestamp-manipulation hack and is stable across
        filtering and sorting modes.
        """
        updated: List[str] = []
        for i, mem_id in enumerate(memory_ids):
            mem = self.get(mem_id)
            if not mem:
                continue

            # Assign rank: first item gets highest rank
            new_rank = len(memory_ids) - i

            # Only rewrite if rank actually changed
            if mem.frontmatter.rank == new_rank:
                updated.append(mem_id)
                continue

            fm = MemoryFrontmatter(
                id=mem_id,
                created=mem.frontmatter.created,
                source=mem.frontmatter.source,
                confidence=mem.frontmatter.confidence,
                pinned=mem.frontmatter.pinned,
                tags=mem.frontmatter.tags,
                supersedes=mem.frontmatter.supersedes,
                zone=mem.frontmatter.zone,
                rank=new_rank,
            )

            # Atomic write via temp file to avoid in-place overwrite corruption
            new_path = self._root_for(mem.scope) / f"{fm.created[:10]}-{fm.id[:16]}.md"
            tmp_path = new_path.with_suffix(new_path.suffix + ".tmp")
            _write_memory(tmp_path, fm, mem.body)
            os.replace(tmp_path, new_path)  # atomic on POSIX
            if new_path != mem.source_path:
                self.delete(mem.scope, mem_id)

            # Update cache
            self._id_to_path[fm.id] = new_path
            self._update_cache_for_put(mem.scope, fm, mem.body, new_path)
            self._try_index(fm.id, mem.body)
            updated.append(mem_id)

        # Invalidate cache to ensure fresh ordering
        self._invalidate_cache()
        return updated

    def _root_for(self, scope: str) -> Path:
        if scope == "user":
            return self.user_root
        if scope == "project":
            if self.project_root is None:
                raise ValueError("Project scope requested but no project root configured")
            return self.project_root
        raise ValueError(f"Unknown scope: {scope}")

    # -- Fusion search (BM25 + Graph + Supersedes) ---------------------------------------

    def _calc_supersedes_depth(self, memory_id: str, visited: Optional[Set[str]] = None,
                               max_depth: int = 10) -> int:
        """Follow supersedes chain recursively to compute depth.

        depth=0: never superseded
        depth=1: supersedes one other memory
        depth=N: chain of N supersedes
        Guarded against cycles via visited set and max_depth.
        """
        if visited is None:
            visited = set()
        if memory_id in visited or len(visited) >= max_depth:
            return len(visited)
        visited.add(memory_id)
        m = self.get(memory_id)
        if m is None:
            return len(visited) - 1
        supers = m.frontmatter.supersedes
        if not supers:
            return len(visited) - 1
        # Follow the first supersedes link (linear chain assumption)
        return self._calc_supersedes_depth(supers[0], visited, max_depth)

    def fusion_search(self, query: str, k: int = 5,
                      zone: Optional[str] = None,
                      alpha: float = 0.7,
                      beta: float = 0.3) -> List[LoadedMemory]:
        """Unified fusion search: BM25 × Graph × Supersedes.

        final_score = α * bm25_norm + β * (graph_activation / (1 + supersedes_depth))

        This replaces the old two-stage "BM25 → graph_expanded as extra" pattern
        with a single fused ranking.
        """
        active = self.list_active()
        if not active:
            return []

        # Step 1: Get BM25 scores with effectiveness
        effectiveness = self._get_effectiveness()
        doc_tokens = self._ensure_doc_tokens(active)
        scored = _bm25_search_scored(active, query, k * 2, effectiveness, doc_tokens)
        if not scored:
            return []

        # Step 2: Normalize BM25 scores to [0, 1]
        max_bm25 = max(s for _, s in scored)
        if max_bm25 <= 0:
            max_bm25 = 1.0

        # Step 3: Get graph activation scores
        gm = _get_graph_mgr()
        has_graph = gm is not None

        fused: List[Tuple[float, LoadedMemory]] = []
        for mem, bm25_score in scored:
            bm25_norm = bm25_score / max_bm25
            graph_score = 0.0
            supersedes_depth = 0

            if has_graph:
                # Get graph activation (1-hop BFS weight sum)
                try:
                    neighbors = gm.store.get_neighbors(mem.id(), min_weight=0.1, limit=20)
                    graph_score = sum(n.get("weight", 0) for n in neighbors) / max(len(neighbors), 1)
                    # Clamp to [0, 1]
                    graph_score = min(1.0, graph_score)
                except Exception:
                    pass

            # Calculate supersedes depth
            supersedes_depth = self._calc_supersedes_depth(mem.id())

            # Combined score: α * bm25 + β * (graph / (1 + depth))
            sup_factor = 1.0 / (1.0 + supersedes_depth)
            final_score = alpha * bm25_norm + beta * graph_score * sup_factor
            fused.append((final_score, mem))

        # Step 4: Re-sort by fused score
        fused.sort(key=lambda x: x[0], reverse=True)
        results = [m for _, m in fused[:k * 2]]

        # Apply zone filter
        if zone:
            results = [m for m in results if m.frontmatter.zone == _normalize_zone(zone)]

        return results[:k]

    def _get_effectiveness(self) -> Dict[str, MemoryEffectiveness]:
        """Lazy-load effectiveness stats from JSONL. Cached per store instance."""
        if self._effectiveness_cache is not None:
            return self._effectiveness_cache
        self._effectiveness_cache = load_effectiveness()
        return self._effectiveness_cache

    def refresh_effectiveness(self) -> None:
        """Force reload of effectiveness stats (call after writing new stats)."""
        self._effectiveness_cache = None

    def _ensure_doc_tokens(self, active: List[LoadedMemory]) -> List[Tuple[str, List[str]]]:
        """Build or return cached tokenized documents for TF-IDF."""
        if self._doc_tokens is not None and len(self._doc_tokens) == len(active):
            # Quick check: same IDs in same order
            if all(self._doc_tokens[i][0] == active[i].id() for i in range(len(active))):
                return self._doc_tokens
        # Rebuild
        self._doc_tokens = [(m.id(), _memory_tokens(m)) for m in active]
        return self._doc_tokens

    def search(self, query: str, k: int = 5, zone: Optional[str] = None) -> List[LoadedMemory]:
        active = self.list_active()
        # Try embedding first if available
        embed_results = self._embed_search(query, k)
        if embed_results is not None:
            logger.debug("search: using embedding strategy for query=%r k=%d zone=%s", query, k, zone)
            id_set = {mid for mid, _ in embed_results}
            results = [m for m in active if m.id() in id_set]
            if zone:
                results = [m for m in results if m.frontmatter.zone == _normalize_zone(zone)]
            return results[:k]
        # Load effectiveness and cached doc_tokens
        logger.debug("search: using BM25 strategy for query=%r k=%d zone=%s", query, k, zone)
        effectiveness = self._get_effectiveness()
        doc_tokens = self._ensure_doc_tokens(active)
        return _bm25_search(active, query, k, effectiveness, doc_tokens)

    def check_conflict(self, body: str, threshold: Optional[float] = None) -> Optional[Tuple[str, float]]:
        """Check for conflicting memories using BM25 similarity.

        Complexity (P2-10): O(n·m) where n=active memories, m=query tokens.
        At ~200 memories this is ~20ms — acceptable. Consider indexing if
        exceeding 1000 memories.

        Args:
            body: text to check for conflicts
            threshold: override (None = adaptive: 0.75 for CJK, 0.85 for Latin)
        """
        if threshold is None:
            threshold = _adaptive_conflict_threshold(body)
        active = self.list_active()
        scored = _bm25_search_scored(active, body, 1)
        if scored:
            m, score = scored[0]
            if score > threshold:
                return (m.id(), score)
        return None

    # -- optional embedding index ---------------------------------------------

    def _ensure_embed(self) -> bool:
        if not _embeddings_enabled():
            return False
        if self._embed_index is not None:
            return True
        with self._embed_lock:
            if self._embed_index is not None:
                return True
            try:
                self._embed_index = {"vectors": {}, "ids": []}
                # Index existing memories using unified embed function
                for m in self.list_active():
                    vec = _embed_single(m.body)
                    if vec is not None:
                        self._embed_index["vectors"][m.id()] = vec
                        self._embed_index["ids"].append(m.id())
                logger.info("Embedding index initialized with %d memories", len(self._embed_index["ids"]))
                return True
            except Exception as e:
                logger.debug("Embedding index unavailable: %s", e)
                return False

    def _try_index(self, mem_id: str, body: str) -> None:
        if not self._ensure_embed():
            return
        try:
            vec = _embed_single(body)
            if vec is not None:
                self._embed_index["vectors"][mem_id] = vec
                if mem_id not in self._embed_index["ids"]:
                    self._embed_index["ids"].append(mem_id)
        except Exception:
            pass

    def _try_remove_index(self, mem_id: str) -> None:
        if self._embed_index is None:
            return
        self._embed_index["vectors"].pop(mem_id, None)
        if mem_id in self._embed_index["ids"]:
            self._embed_index["ids"].remove(mem_id)

    def _embed_search(self, query: str, k: int) -> Optional[List[Tuple[str, float]]]:
        if not self._ensure_embed():
            return None
        try:
            qvec = _embed_single(query)
            if qvec is None:
                return None
            scores: List[Tuple[str, float]] = []
            for mid, vec in self._embed_index["vectors"].items():
                sim = _cosine_sim(qvec, vec)
                scores.append((mid, sim))
            scores.sort(key=lambda x: x[1], reverse=True)
            return scores[:k]
        except Exception as e:
            logger.debug("Embedding search failed: %s", e)
            return None


# ---------------------------------------------------------------------------
# TF-IDF search (pure Python, zero dependency)
# ---------------------------------------------------------------------------

_MIN_TOKEN_LEN = 2

# Pre-compiled regex for tokenisation
_TOKEN_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]+")

# Pre-computed CJK code point ranges for faster check
_CJK_RANGES = [
    (0x4E00, 0x9FFF),
    (0x3400, 0x4DBF),
    (0x3000, 0x303F),
    (0x3040, 0x309F),
    (0x30A0, 0x30FF),
    (0xAC00, 0xD7AF),
]


def _is_cjk(c: str) -> bool:
    cp = ord(c)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _cjk_ratio(text: str) -> float:
    """Return fraction of alphabet-like chars that are CJK in text.

    Skips whitespace, digits, punctuation. Returns 0.0 for empty/short text.
    Used for adaptive conflict threshold: CJK BM25 scores are more polarized
    (0.65-0.75 for related vs 0.85-0.95 for Latin text).
    """
    letter_count = 0
    cjk_count = 0
    for c in text:
        if c.isalpha():
            letter_count += 1
            if _is_cjk(c):
                cjk_count += 1
    if letter_count == 0:
        return 0.0
    return cjk_count / letter_count


def _adaptive_conflict_threshold(body: str) -> float:
    """Return conflict threshold based on CJK ratio.

    CJK-dominant text (>40% CJK) → 0.75 (lower threshold catches more collisions)
    Latin-dominant text → 0.85 (standard)
    Mixed text (10-40%) → 0.80 (interpolated)
    """
    ratio = _cjk_ratio(body)
    if ratio > 0.40:
        return 0.75
    elif ratio > 0.10:
        return 0.80
    return 0.85


def _tokenise(s: str) -> List[str]:
    lower = s.lower()
    tokens = []
    for segment in _TOKEN_RE.split(lower):
        char_count = len(segment)
        if char_count == 0:
            continue
        if char_count >= _MIN_TOKEN_LEN:
            tokens.append(segment)
        # CJK bigrams
        if char_count >= 2 and any(_is_cjk(c) for c in segment):
            chars = list(segment)
            for i in range(len(chars) - 1):
                tokens.append(chars[i] + chars[i + 1])
    return tokens


def _memory_tokens(m: LoadedMemory) -> List[str]:
    tokens = _tokenise(m.body)
    for tag in m.frontmatter.tags:
        tokens.extend(_tokenise(tag))
    return tokens


def _bm25_search(memories: List[LoadedMemory], query: str, k: int,
                  effectiveness: Optional[Dict[str, MemoryEffectiveness]] = None,
                  doc_tokens: Optional[List[Tuple[str, List[str]]]] = None) -> List[LoadedMemory]:
    scored = _bm25_search_scored(memories, query, k, effectiveness, doc_tokens)
    return [m for m, _ in scored]


def _bm25_search_scored(memories: List[LoadedMemory], query: str, k: int,
                         effectiveness: Optional[Dict[str, MemoryEffectiveness]] = None,
                         doc_tokens: Optional[List[Tuple[str, List[str]]]] = None
                         ) -> List[Tuple[LoadedMemory, float]]:
    """BM25 retrieval with effectiveness boosting.

    BM25 formula (Robertson & Zaragoza, 2009):
      score(D,Q) = Σ IDF(q_i) * TF_okapi(q_i,D) * (k1+1)/(TF_okapi(q_i,D)+k1*(1-b+b*|D|/avgdl))
    
    k1=1.5 (saturation), b=0.75 (length normalization) optimized for CJK mixed text.
    Falls back gracefully for empty/singleton corpus.
    """
    k1, b = 1.5, 0.75
    if k == 0 or not memories:
        return []
    q_tokens = _tokenise(query)
    if not q_tokens:
        return []
    n = len(memories)

    # Compute doc frequencies + doc lengths in one pass
    df: Dict[str, int] = Counter()
    doc_lens: List[int] = []
    raw_doc_tokens: List[List[str]]
    if doc_tokens is not None:
        raw_doc_tokens = [tokens for _, tokens in doc_tokens]
    else:
        raw_doc_tokens = [_memory_tokens(m) for m in memories]
    for tokens in raw_doc_tokens:
        doc_lens.append(len(tokens))
        for t in set(tokens):
            df[t] += 1

    avgdl = sum(doc_lens) / max(n, 1)
    
    # BM25 IDF: log(1 + (N - df(q) + 0.5) / (df(q) + 0.5))
    q_tf = Counter(q_tokens)
    idf_cache: Dict[str, float] = {}
    for t in q_tf:
        df_t = df.get(t, 0)
        if df_t == 0:
            continue  # skip unseen terms
        idf_cache[t] = (n - df_t + 0.5) / (df_t + 0.5) + 1.0  # add-one smoothing

    if not idf_cache:
        return []

    scored: List[Tuple[float, LoadedMemory]] = []
    for i, (tokens, m) in enumerate(zip(raw_doc_tokens, memories)):
        doc_len = doc_lens[i]
        m_tf = Counter(tokens)
        score = 0.0
        for t, q_count in q_tf.items():
            idf = idf_cache.get(t)
            if idf is None:
                continue
            tf = m_tf.get(t, 0)
            # BM25 term: (k1+1)*TF / (k1*(1-b+b*|D|/avgdl) + TF)
            norm = k1 * (1 - b + b * doc_len / max(avgdl, 1))
            score += idf * (tf * (k1 + 1)) / (tf + norm) * q_count  # q_count for multi-occur query terms
        if score > 0:
            # Apply effectiveness boosting if available
            if effectiveness:
                eff = effectiveness.get(m.id())
                if eff:
                    score *= eff.factor() * eff.decay_factor()
            scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(m, s) for s, m in scored[:k]]


def _cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    dot = sum(a[k] * b.get(k, 0.0) for k in a)
    norm_a = sum(v * v for v in a.values()) ** 0.5
    norm_b = sum(v * v for v in b.values()) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Skill Store
# ---------------------------------------------------------------------------

class SkillStore:
    def __init__(self, user_root: Path, project_root: Optional[Path] = None):
        self.user_root = user_root
        self.project_root = project_root
        self._cache: Optional[List[LoadedSkill]] = None  # Lazy cache (skills are static per session)
        self._disabled_project_skills: Set[str] = set()  # P2-12: user-disabled project skill names

    def disable_project_skill(self, name: str) -> None:
        """Explicitly disable a project skill by name (user override)."""
        self._disabled_project_skills.add(name)
        self.invalidate_cache()

    def enable_project_skill(self, name: str) -> None:
        """Re-enable a previously disabled project skill."""
        self._disabled_project_skills.discard(name)
        self.invalidate_cache()

    def list_disabled(self) -> List[str]:
        """Return list of disabled project skill names."""
        return sorted(self._disabled_project_skills)

    def list(self) -> List[LoadedSkill]:
        if self._cache is not None:
            return self._cache
        user_skills = self._list_scope(self.user_root, "user")
        project_skills = self._list_scope(self.project_root, "project") if self.project_root else []
        # P2-12: filter out disabled project skills
        project_skills = [s for s in project_skills if s.frontmatter.name not in self._disabled_project_skills]
        project_names = {s.frontmatter.name for s in project_skills}
        user_skills = [s for s in user_skills if s.frontmatter.name not in project_names]
        out = user_skills + project_skills
        out.sort(key=lambda s: s.frontmatter.name)
        self._cache = out
        return out

    def invalidate_cache(self) -> None:
        """Force reload on next list() call (e.g., after skill changes)."""
        self._cache = None

    def _list_scope(self, root: Optional[Path], scope: str) -> List[LoadedSkill]:
        out: List[LoadedSkill] = []
        if root is None or not root.exists():
            return out
        for d in root.iterdir():
            if not d.is_dir():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            s = _read_skill(skill_md, scope)
            if s:
                out.append(s)
        return out

    def get(self, name: str) -> Optional[LoadedSkill]:
        if self.project_root:
            p = self.project_root / name / "SKILL.md"
            if p.exists():
                return _read_skill(p, "project")
        p = self.user_root / name / "SKILL.md"
        if p.exists():
            return _read_skill(p, "user")
        return None


def _read_skill(path: Path, scope: str) -> Optional[LoadedSkill]:
    try:
        raw = path.read_text(encoding="utf-8")
        data, body = _parse_frontmatter(raw)
        fm = SkillFrontmatter(
            name=data.get("name", path.parent.name),
            description=data.get("description", ""),
            triggers=data.get("triggers", []),
            version=data.get("version"),
            license=data.get("license"),
            always_active=bool(data.get("always_active", False)),
        )
        return LoadedSkill(frontmatter=fm, body=body.strip(), source_path=path, scope=scope)
    except Exception as e:
        logger.warning("Failed to read skill %s: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# Skill matcher (token overlap + optional embedding)
# ---------------------------------------------------------------------------

_MIN_SKILL_TOKEN = 3
_TOKEN_WEIGHT = 0.4
_EMBED_WEIGHT = 0.6


def _skill_tokenise(s: str) -> Set[str]:
    return {
        t for t in re.split(r"[^a-z0-9]+", s.lower())
        if len(t) >= _MIN_SKILL_TOKEN
    }


def _skill_bag(s: LoadedSkill) -> Set[str]:
    bag: Set[str] = set()
    for t in s.frontmatter.triggers:
        bag.update(_skill_tokenise(t))
    bag.update(_skill_tokenise(s.frontmatter.name))
    bag.update(_skill_tokenise(s.frontmatter.description))
    return bag


def match_skills(skills: List[LoadedSkill], query: str, k: int = 3) -> List[LoadedSkill]:
    q = _skill_tokenise(query)
    if not q:
        return []
    scored: List[Tuple[float, int, LoadedSkill]] = []
    for s in skills:
        bag = _skill_bag(s)
        raw_token = len(q & bag)
        if raw_token == 0:
            continue
        score = raw_token
        scored.append((score, len(s.frontmatter.triggers), s))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [s for _, _, s in scored[:k]]

# ---------------------------------------------------------------------------
# Reflection runner
# ---------------------------------------------------------------------------
# Plugin state
# ---------------------------------------------------------------------------

_mem_store: Optional[MemoryStore] = None
_skill_store: Optional[SkillStore] = None
_turns_since_reflect: int = 0
_micro_reflect_queue: List[Dict[str, Any]] = []


def _get_mem_store() -> MemoryStore:
    global _mem_store
    if _mem_store is None:
        _mem_store = MemoryStore(_user_memories_dir(), _project_memories_dir())
    return _mem_store


def _get_skill_store() -> SkillStore:
    global _skill_store
    if _skill_store is None:
        _skill_store = SkillStore(_user_skills_dir(), _project_skills_dir())
    return _skill_store


# ---------------------------------------------------------------------------
# Context assembly (Pinned → Active Index → Triggered Skills)
# ---------------------------------------------------------------------------

def _build_context_block(query: str = "") -> str:
    """Build the memory context block injected into the user message.

    Three modes (checked in priority order):
    1. Palace mode: inject palace index (zone map), agent uses tools for retrieval
    2. Profile mode: inject compiled profile.md if available, no per-turn injection
    3. Legacy mode: pinned + active index + per-turn TF-IDF relevance injection

    Note (P2-22): Entire function is wrapped in try/except so a failure in any
    stage degrades gracefully to empty context rather than failing the hook.
    """
    try:
        return _build_context_block_inner(query)
    except Exception as e:
        logger.warning("Context block build failed: %s", e, exc_info=True)
        return ""


def _build_context_block_inner(query: str = "") -> str:
    mem_store = _get_mem_store()
    skill_store = _get_skill_store()
    parts: List[str] = []
    stat_entries: List[Tuple[str, str]] = []  # Batch collect (id, event)

    # Determine mode — cache config lookups
    palace_mode = _palace_mode_enabled()
    profile_mode = _profile_mode_enabled()

    # Pre-load skills once (used in palace index, triggered, always-active)
    all_skills = skill_store.list()

    # ---- Mode 1: Palace (zone-based, tool-driven retrieval) ----
    if palace_mode:
        active = mem_store.list_active()
        if active:
            # P0-1+P2-1: write-on-change + event-driven rebuild
            if mem_store._index_dirty:
                index = build_palace_index(active)
                h = _fast_hash(index)
                if h != mem_store._last_index_hash:
                    _palace_index_path().parent.mkdir(parents=True, exist_ok=True)
                    _palace_index_path().write_text(index, encoding="utf-8")
                    mem_store._last_index_hash = h
                mem_store._index_dirty = False
                mem_store._cached_index = index  # Cache built string
            else:
                # Reuse cached index (don't rebuild, don't write)
                index = mem_store._cached_index
            parts.append(index)
            for m in active:
                stat_entries.append((m.id(), "loaded"))
        else:
            parts.append("## Memory Palace\nEmpty — no memories yet.")

        if _palace_instructions_enabled():
            parts.append(_PALACE_USAGE_INSTRUCTIONS)

        cap = _skill_index_cap()
        if all_skills:
            parts.append("\n## Available skills")
            for s in all_skills[:cap]:
                parts.append(f"- {s.frontmatter.name}: {s.frontmatter.description}")
            if len(all_skills) > cap:
                parts.append(f"- ... ({len(all_skills) - cap} more)")

    # ---- Mode 2: Compiled Profile (LLM-compiled, all-in-one) ----
    elif profile_mode:
        profile_path = _plugin_data_dir() / "profile.md"
        if profile_path.exists():
            profile = profile_path.read_text(encoding="utf-8").strip()
            if profile:
                parts.append("## User Profile\n")
                parts.append(profile)

        if not parts:
            pinned = mem_store.list_pinned()
            if pinned:
                parts.append("=== Pinned memories (always relevant) ===")
                for m in pinned:
                    parts.append(f"- [{m.id()}] {m.body[:200]}")
                    stat_entries.append((m.id(), "loaded"))
                parts.append("")

    # ---- Mode 3: Legacy (pinned + active index + per-turn TF-IDF) ----
    else:
        pinned = mem_store.list_pinned()
        if pinned:
            parts.append("=== Pinned memories (always relevant) ===")
            for m in pinned:
                parts.append(f"- [{m.id()}] {m.body[:200]}")
                stat_entries.append((m.id(), "loaded"))
            parts.append("")

        if query:
            active = mem_store.search(query, k=_relevant_memory_cap())
        else:
            active = mem_store.list_active()[:_active_memory_cap()]
        if active:
            parts.append("=== Relevant memories ===")
            for m in active:
                if m not in pinned:
                    parts.append(f"- [{m.id()}] {m.body[:200]}")
                    stat_entries.append((m.id(), "loaded"))
            parts.append("")

    # Triggered skills (legacy/profile fallback)
    if not palace_mode or (palace_mode and not parts):
        if query:
            skills = match_skills(all_skills, query, k=_triggered_skill_cap())
        else:
            skills = []
        if skills:
            parts.append("=== Triggered skills ===")
            for s in skills:
                parts.append(f"- {s.frontmatter.name}: {s.frontmatter.description}")
            parts.append("")

    # Always-active skills (all modes)
    always_active = [s for s in all_skills if s.frontmatter.always_active]
    if always_active:
        parts.append("\n## Always-Active Skills\n")
        for s in always_active:
            parts.append(f"### {s.frontmatter.name}\n{s.body.strip()}\n")

    # Flush all stat entries in one file open
    if stat_entries:
        _batch_record_stats(stat_entries)

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
    # Register slash commands
    ctx.register_command(
        name="reflect",
        handler=lambda raw: _slash_reflect(raw),
        description="Trigger a full reflection on the current session",
        args_hint="",
    )
    ctx.register_command(
        name="pending-skills",
        handler=lambda raw: _slash_pending_skills(raw),
        description="Show pending skill candidates awaiting approval",
        args_hint="",
    )
    ctx.register_command(
        name="approve-skill",
        handler=lambda raw: _slash_approve_skill(raw),
        description="Approve a pending skill candidate by ID",
        args_hint="<pending_id>",
    )
    ctx.register_command(
        name="reject-skill",
        handler=lambda raw: _slash_reject_skill(raw),
        description="Reject a pending skill candidate by ID",
        args_hint="<pending_id> [reason]",
    )
    ctx.register_command(
        name="memories",
        handler=lambda raw: _slash_memories(raw),
        description="List active memories",
        args_hint="[query]",
    )
    ctx.register_command(
        name="skills",
        handler=lambda raw: _slash_skills(raw),
        description="List or search skills",
        args_hint="[query]",
    )
    ctx.register_command(
        name="compile-profile",
        handler=lambda raw: _slash_compile_profile(raw),
        description="Compile all memories into a structured profile via LLM",
        args_hint="[profile|palace_index|zone]",
    )

    logger.info("mem-reflection-hermes plugin registered")

    # ── ahe_graph integration (v0.6.1+) ───────────────────────
    try:
        try:
            from .ahe_graph import get_graph_manager as _get_gm
        except ImportError:
            logger.debug("Relative import of ahe_graph failed (expected for standalone plugin load), trying importlib fallback")
            # Standalone plugin load: __package__ may be empty, so relative
            # import fails. Fallback to importlib-based absolute load.
            import importlib.util as _iutil
            import sys as _sys
            _ahe_path = str(Path(__file__).parent / "ahe_graph" / "__init__.py")
            if not Path(_ahe_path).exists():
                logger.warning("ahe_graph module not found at %s — graph features disabled", _ahe_path)
                _get_gm = None  # type: ignore
            else:
                _ahe_spec = _iutil.spec_from_file_location(
                    "mem_reflection_hermes.ahe_graph", _ahe_path
                )
                if _ahe_spec is None or _ahe_spec.loader is None:
                    logger.warning("ahe_graph spec could not be loaded — graph features disabled")
                    _get_gm = None  # type: ignore
                else:
                    try:
                        _ahe_mod = _iutil.module_from_spec(_ahe_spec)
                        _sys.modules["mem_reflection_hermes.ahe_graph"] = _ahe_mod
                        _ahe_spec.loader.exec_module(_ahe_mod)  # type: ignore
                        _get_gm = _ahe_mod.get_graph_manager
                        logger.info("ahe_graph loaded successfully via importlib fallback")
                    except Exception as _ahe_err:
                        logger.warning(
                            "ahe_graph loaded but raised %s: %s — graph features disabled",
                            type(_ahe_err).__name__, _ahe_err,
                        )
                        _get_gm = None  # type: ignore

        # ── Common graph setup (runs regardless of import path) ──
        # Lazy-init on first use
        _graph_db_dir = Path(ctx.hermes_home) / "plugins" / "mem-reflection-hermes"
        # Set module-level globals so _on_session_end and _get_graph_neighbors
        # use the same singleton (P1-2, P2-1)
        global _gm_getter_func, _gm_getter_path
        _gm_getter_func = _get_gm
        _gm_getter_path = _graph_db_dir
        _gm_ref = {"instance": None}
        _gm_lock = threading.Lock()

        def _ensure_gm():
            if _gm_ref["instance"] is None:
                with _gm_lock:
                    if _gm_ref["instance"] is None:
                        _gm_ref["instance"] = _get_gm(_graph_db_dir)
            return _gm_ref["instance"]

        # --- tool: srh_associate ---
        MAX_ASSOCIATION_IDS = 20

        def _graph_associate_h(args: dict, **kwargs) -> str:
            gm = _ensure_gm()
            mids = args.get("memory_ids", [])[:MAX_ASSOCIATION_IDS]
            ctx_str = args.get("context", "")
            rel = args.get("relation", "co_occurs")
            result = gm.associate_memories(mids, ctx_str, rel)
            return json.dumps(result)

        ctx.register_tool(
            name="srh_associate",
            toolset="mem_reflection_hermes",
            schema={
                "name": "srh_associate",
                "description": "Create graph associations between memories. Records Hebbian co-occurrence edges so related memories activate each other during retrieval.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of memory IDs to associate (max 20)",
                            "minItems": 2,
                            "maxItems": MAX_ASSOCIATION_IDS,
                        },
                        "relation": {
                            "type": "string",
                            "enum": ["co_occurs", "co_used_in_task"],
                            "description": "Relation type: co_occurs (stronger) or co_used_in_task (weaker)",
                            "default": "co_occurs",
                        },
                    },
                    "required": ["memory_ids"],
                },
            },
            handler=_graph_associate_h,
            description="Associate memories via graph edges",
            emoji="🔗",
        )

        # --- tool: srh_graph_retrieve ---
        def _graph_retrieve_h(args: dict, **kwargs) -> str:
            gm = _ensure_gm()
            mids = args.get("memory_ids", [])[:20]
            task_type = args.get("task_type", "reasoning")
            max_res = min(args.get("max_results", 10), 100)
            tier = args.get("tier", "list")
            # Auto-detect strategy from seed memory zones when task_type is default
            if task_type == "reasoning" and mids and gm.store:
                try:
                    zones = set()
                    for mid in mids:
                        meta = gm.store.get_meta(mid)
                        if meta and meta.get("zone"):
                            zones.add(meta.get("zone"))
                    # Map seed zones to best strategy
                    zone_strategy_map = {
                        "core": "factual",
                        "work": "reasoning",
                        "episode": "recent",
                        "general": "exploration",
                    }
                    for z in zones:
                        inferred = zone_strategy_map.get(z)
                        if inferred:
                            task_type = inferred
                            break
                except Exception:
                    pass  # fallback to "reasoning"
            results = gm.retrieve_related(mids, task_type, max_res, tier=tier)
            return json.dumps({"results": results, "count": len(results), "seed_ids": mids, "tier": tier, "strategy": task_type})

        ctx.register_tool(
            name="srh_graph_retrieve",
            toolset="mem_reflection_hermes",
            schema={
                "name": "srh_graph_retrieve",
                "description": "Retrieve graph-related memories via activation propagation. Given seed memory IDs, finds connected memories through graph edges with Hebbian weights. Progressive tiers: tier='count' (minimal), 'list' (summary, default), 'detail' (full with depth).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Seed memory IDs to start graph traversal from (max 20)",
                            "minItems": 1,
                            "maxItems": 20,
                        },
                        "task_type": {
                            "type": "string",
                            "enum": ["factual", "reasoning", "skill", "recent", "exploration", "personalized"],
                            "description": "Retrieval strategy type",
                            "default": "reasoning",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max results to return",
                            "default": 10,
                            "minimum": 1,
                            "maximum": 100,
                        },
                        "tier": {
                            "type": "string",
                            "enum": ["count", "list", "detail"],
                            "description": "Progressive disclosure tier: 'count' = minimal, 'list' = summary (default), 'detail' = full propagation info",
                            "default": "list",
                        },
                    },
                    "required": ["memory_ids"],
                },
            },
            handler=_graph_retrieve_h,
            description="Retrieve graph-related memories",
            emoji="🕸️",
        )

        # --- tool: srh_graph_stats ---
        def _graph_stats_h(args: dict, **kwargs) -> str:
            gm = _ensure_gm()
            return json.dumps(gm.get_stats())

        ctx.register_tool(
            name="srh_graph_stats",
            toolset="mem_reflection_hermes",
            schema={
                "name": "srh_graph_stats",
                "description": "Get graph memory statistics: node count, edge count, average edge weight, database path.",
                "parameters": {"type": "object", "properties": {}},
            },
            handler=_graph_stats_h,
            description="Get graph memory statistics",
            emoji="📊",
        )

        # ── P2-3: srh_graph_viz — graph visualization data ──
        def _graph_viz_h(args: dict, **kwargs) -> str:
            """Return full graph data for dashboard visualization."""
            gm = _ensure_gm()
            tier = args.get("tier", "summary")
            stats = gm.get_stats(tier="detail")
            if stats.get("node_count", 0) == 0:
                return json.dumps({"nodes": [], "edges": [], "stats": stats})
            try:
                conn = gm.store._connect()
                nodes = conn.execute(
                    "SELECT id, zone, importance, strength, status, access_count FROM graph_memory_meta "
                    "WHERE strength > 0 ORDER BY importance DESC LIMIT 200"
                ).fetchall()
                edges = conn.execute(
                    "SELECT source_id, target_id, relation, weight FROM graph_edges "
                    "WHERE weight >= 0.1 ORDER BY weight DESC LIMIT 500"
                ).fetchall()
                return json.dumps({
                    "nodes": [dict(r) for r in nodes],
                    "edges": [dict(r) for r in edges],
                    "stats": stats,
                })
            except Exception as e:
                return json.dumps({"error": str(e), "stats": stats})

        ctx.register_tool(
            name="srh_graph_viz",
            toolset="mem_reflection_hermes",
            schema={
                "name": "srh_graph_viz",
                "description": "Get full graph visualization data (nodes + edges) for dashboard rendering. tier='summary' returns counts only; 'detail' returns full node/edge lists.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tier": {"type": "string", "enum": ["summary", "detail"], "default": "summary"},
                    },
                },
            },
            handler=_graph_viz_h,
            description="Graph viz data for dashboard",
            emoji="🕸️",
        )

        # --- hook: auto-associate on memory write ---
        def _post_tool_associate(ctx_hook, context: dict) -> dict:
            try:
                tool_name = context.get("tool_name", "")
                if tool_name not in ("srh_memory_write", "srh_memory_delete"):
                    return context

                gm = _ensure_gm()
                args = context.get("tool_args", {})
                result = context.get("result", {})

                if tool_name == "srh_memory_write":
                    # result may be raw JSON string (srh_memory_write returns json.dumps)
                    if isinstance(result, str):
                        try:
                            result = json.loads(result)
                        except json.JSONDecodeError:
                            result = {}
                    # Guard: only create graph metadata for successful writes
                    if not result.get("success") and not result.get("id"):
                        return context
                    memory_id = result.get("id")
                    if not memory_id:
                        return context
                    zone = args.get("zone", "general")
                    gm.store.ensure_meta(memory_id, zone=zone)
                    gm.store.record_access(memory_id)

                    # ── Scheme A-1: Supersedes-aware edge migration ──────────
                    supersedes_ids = args.get("supersedes", [])
                    if supersedes_ids and isinstance(supersedes_ids, list):
                        for old_id in supersedes_ids:
                            # Mark old memory as inactive in graph
                            gm.store.update_importance(old_id, delta=-0.9)
                            gm.store._connect().execute(
                                "UPDATE graph_memory_meta SET strength=0, status='superseded' WHERE id=?",
                                (old_id,)
                            )
                            # Migrate old edges to new memory (weight * 0.3)
                            old_edges = gm.store.get_edges(old_id)
                            for edge in old_edges:
                                src, tgt = edge["source_id"], edge["target_id"]
                                neigh = tgt if src == old_id else src
                                rel = edge.get("relation", "co_occurs")
                                old_w = edge.get("weight", 0.5)
                                # Copy edge from new memory to neighbor with decayed weight
                                gm.store.set_edge_weight(memory_id, neigh, relation=rel,
                                                     weight=old_w * 0.3)
                            conn = gm.store._connect()
                            conn.commit()
                elif tool_name == "srh_memory_delete":
                    # Clean up graph metadata and edges for this memory
                    mem_id = args.get("id", "")
                    if mem_id:
                        # ── Scheme A-2: Soft-delete (mark inactive) instead of hard delete ──
                        gm.store.update_importance(mem_id, delta=-0.9)
                        gm.store._connect().execute(
                            "UPDATE graph_memory_meta SET strength=0, status='deleted' WHERE id=?",
                            (mem_id,)
                        )
                        # Decay connected edges heavily
                        gm.store.decay_edges(decay_rate=0.9)
            except Exception as e:
                logger.debug("ahe_graph auto-associate: %s", e)
            return context

        ctx.register_hook("post_tool_call", _post_tool_associate)

        # --- slash command ---
        def _slash_graph(raw_args: str) -> str:
            gm = _ensure_gm()
            parts = raw_args.strip().split()
            cmd = parts[0] if parts else "stats"

            if cmd == "stats":
                s = gm.get_stats(tier="detail")
                return (
                    f"📊 **Graph Memory Stats**\n"
                    f"- Nodes: {s['node_count']}\n"
                    f"- Edges: {s['edge_count']}\n"
                    f"- Avg Weight: {s['avg_weight']}\n"
                    f"- DB: {s['db_path']}"
                )
            elif cmd == "decay":
                gm.run_decay()
                return "🧹 Decay cycle completed on all graph edges and memory strengths."
            elif cmd == "associate" and len(parts) >= 3:
                mids = parts[1:]
                r = gm.associate_memories(mids)
                return f"🔗 Associated {len(mids)} memories ({r['edges_created']} edges created/updated)"
            else:
                return "Usage: /graph [stats|decay|associate <id1> <id2> ...]"

        ctx.register_command(
            name="graph",
            handler=_slash_graph,
            description="Graph memory operations: stats, decay, associate",
            args_hint="[stats|decay|associate <id1> <id2> ...]",
        )

        logger.info("ahe_graph integration registered (v0.6.1+)")
    except ImportError as e:
        logger.warning("ahe_graph not available (skip integration): %s", e)
    except Exception as e:
        logger.warning("ahe_graph integration error: %s", e)


# Tool handlers extracted to tools.py
# Tool handlers extracted to tools.py

# Sub-module imports
from .reflection import *  # noqa: F401, F403
from .tools import *  # noqa: F401, F403
from .hooks import *  # noqa: F401, F403
