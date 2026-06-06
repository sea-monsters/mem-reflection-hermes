"""mem-reflection-hermes plugin -- Self-evolving memory and reflection system.

v1.0-beta2 Runtime Architecture (~3,200 LOC across 6 modules + dashboard):
- store.py: SQLite-backed MemoryStore, Markdown cold storage, token estimation, CJK tokenizer
- search.py: Three-layer retrieval (Recall → RRF/Weighted Fusion → Rerank), embedding engine
- graph.py: GraphIndex -- Hebbian edges, spreading activation, PageRank, cross-zone analysis
- reflect.py: ReflectionEngine -- raw_chunk default, heuristic/LLM optional modes
- context.py: Context assembly -- Palace mode, skill matching, token-aware truncation
- __init__.py: Plugin registration, backward compat, runtime singletons
- dashboard/: FastAPI CRUD + graph visualization endpoints

Legacy modules retired in beta3:
- core.py, late_binding.py, search/embed.py, reflection/engine.py, hooks/lifecycle.py,
  tools/handlers.py, graph/ahe_graph.py, graph/cluqi.py, graph/pagerank.py,
  graph/cross_zone.py, query/cache.py
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Import from store: models, constants, BM25, frontmatter, IO
from .store import (  # noqa: F401
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
    _write_queue, _pending_writes, _write_path_lock, _cancel_pending_write, _write_memory,
    _lineage_latest, _lineage_root, _lineage_depth, _lineage_cycle_check,
    _classify_update_intent, _is_expired, _is_context_mismatch,
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
_read_memory = read_memory
_normalize_zone = normalize_zone
_fast_hash = fast_hash
_async_write_memory = async_write_memory
_batch_record_stats = batch_record_stats
_stats_path = lambda: plugin_data_dir() / "memory-stats.jsonl"

def _palace_instructions_enabled() -> bool:
    return bool(plugin_config().get("palace_instructions", True))

def _active_memory_cap() -> int:
    return int(plugin_config().get("active_memory_index_cap", 20))

def _skill_index_cap() -> int:
    return int(plugin_config().get("skill_index_cap", 20))

def _relevant_memory_cap() -> int:
    return int(plugin_config().get("relevant_memory_cap", 5))

def _triggered_skill_cap() -> int:
    return int(plugin_config().get("triggered_skill_cap", 3))

def _config_compaction() -> bool:
    """Check if episode compaction is enabled in plugin config (default: True)."""
    return bool(plugin_config().get("compaction", {}).get("enabled", True))

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
else:
    mod_name = __name__

# Alias bare module name so importlib.import_module("mem_reflection_hermes.*")
# works when Hermes loaded us as "hermes_plugins.mem_reflection_hermes".
_bare_name = "mem_reflection_hermes"
if _bare_name not in sys.modules and mod_name != _bare_name:
    sys.modules[_bare_name] = sys.modules[mod_name]
    # Copy __path__ so submodule imports (mem_reflection_hermes.graph.*) resolve correctly
    _real_path = getattr(sys.modules[mod_name], "__path__", None)
    if _real_path is not None:
        sys.modules[_bare_name].__path__ = _real_path

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
    If graph data is available, appends intra-zone graph cluster density.
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
        gm = globals().get("_get_graph_mgr")
        if callable(gm):
            gm = gm()
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
    except Exception as e:
        logger.warning("build_palace_index graph annotation failed: %s", e)

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
    tmp.replace(path)
    return path


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

def _read_skill(path: Path, scope: str) -> Optional[LoadedSkill]:
    try:
        raw = path.read_text(encoding="utf-8")
        data, body = parse_frontmatter(raw)
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

    # (v1.1) Compacted episode summaries
    try:
        from .context import _build_compacted_episode_block as _episode_blk
        _cfg2 = plugin_config()
        if _cfg2.get("context_compacted_episode", True):
            episode_block = _episode_blk(mem_store)
            if episode_block:
                parts.append(episode_block)
    except Exception:
        pass

    # Flush all stat entries in one file open
    if stat_entries:
        _batch_record_stats(stat_entries)

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Runtime feature registration
# ---------------------------------------------------------------------------
def _register_runtime_features(ctx):
    """Register command and graph runtime features with the Hermes context."""
    from .runtime_hooks import register_commands

    register_commands(ctx)

    logger.info("mem-reflection-hermes plugin registered")

    try:
        from .runtime_graph import register_graph_features
        register_graph_features(
            ctx,
            get_mem_store=_get_mem_store,
            graph_db_path=plugin_data_dir() / "graph.db",
        )
        logger.info("runtime graph integration registered")
        # One-time sync of pre-existing built-in memory entries
        try:
            from .memory_bridge import sync_builtin_to_plugin
            result = sync_builtin_to_plugin(_get_mem_store())
            if result.get("synced", 0):
                logger.info(
                    "startup sync: %d entries mirrored from MEMORY.md",
                    result["synced"],
                )
        except Exception:
            pass
        return
    except ImportError as e:
        logger.warning("runtime graph not available (skip integration): %s", e)
        return
    except Exception as e:
        logger.warning("runtime graph integration error: %s", e)
        return



def _get_mem_store() -> MemoryStore:
    """Return the package-level memory store; keep this before star imports."""
    global _mem_store
    if _mem_store is None:
        _mem_store = MemoryStore(_user_memories_dir(), _project_memories_dir())
    return _mem_store


def _get_skill_store() -> SkillStore:
    """Return the package-level skill store; keep this before star imports."""
    global _skill_store
    if _skill_store is None:
        _skill_store = SkillStore(_user_skills_dir(), _project_skills_dir())
    return _skill_store


# Keep package-native helpers before star imports from submodules add wrappers
# with the same names.
_package_get_mem_store = _get_mem_store
_package_get_skill_store = _get_skill_store
_package_build_context_block = _build_context_block
_package_normalize_zone = _normalize_zone
_package_micro_reflection_enabled = _micro_reflection_enabled
_package_estimate_tokens = _estimate_tokens

# Re-export runtime tool handlers for dashboard / external consumers
from .runtime_tools import _tool_srh_memory_write, _tool_srh_palace_zones  # noqa: E402
from .runtime_hooks import _get_graph_neighbors, _enrich_with_graph, _get_graph_mgr  # noqa: E402, F401
from .reflect import _recent_reflect_outcomes, _save_pending_skill_candidates  # noqa: E402, F401

# Runtime submodules are imported explicitly by register() and compatibility
# entrypoints. Avoid package-root star imports so beta3 no longer exposes every
# private tool/hook/reflection helper as a root-level symbol.

# NOTE: search.py now owns the embedding helpers used by the package root.
_embed_single = None  # noqa: F811
_cosine_sim = None    # noqa: F811
try:
    from .search import _embed_single, _cosine_sim  # noqa: F401, F402
except ImportError:
    import importlib.util as _i_util
    _search_path = Path(__file__).parent / "search.py"
    _spec = _i_util.spec_from_file_location("mem_reflection_hermes.search", str(_search_path))
    _search_mod = _i_util.module_from_spec(_spec)
    sys.modules.setdefault("mem_reflection_hermes.search", _search_mod)
    _spec.loader.exec_module(_search_mod)
    _embed_single = _search_mod._embed_single
    _cosine_sim = _search_mod._cosine_sim

def _auto_rebalance_zones(dry_run: bool = False) -> dict:
    """Delegate zone rebalance to the canonical runtime tool implementation."""
    from .runtime_tools import _auto_rebalance_zones as _runtime_auto_rebalance
    return _runtime_auto_rebalance(dry_run=dry_run)

def _reflection_mode() -> str:
    return plugin_config().get("reflection_mode", "raw_chunk")  # W2: default to raw_chunk


def register(ctx) -> None:
    """Register all Hermes plugin tools, hooks, slash commands, and graph tools."""
    from . import runtime_hooks as _hooks_mod
    from . import runtime_tools as _tools_mod

    if hasattr(_hooks_mod, "set_plugin_context"):
        _hooks_mod.set_plugin_context(ctx)
    _tools_mod.register_tools(ctx)
    if hasattr(_hooks_mod, "register_hooks"):
        _hooks_mod.register_hooks(ctx)
    _register_runtime_features(ctx)


# ---------------------------------------------------------------------------
# Runtime services
# ---------------------------------------------------------------------------

from . import store as _storage_module      # noqa: E402
from . import search as _search_module      # noqa: E402
from . import graph as _graph_module        # noqa: E402
from . import reflect as _reflection_module # noqa: E402
from . import context as _context_module    # noqa: E402

_memory_store = None
_search_index = None
_graph_index = None
_reflection_engine = None


def _get_indexed_mem_store():
    """Get the SQLite-indexed memory store."""
    global _memory_store
    if _memory_store is None:
        _memory_store = _storage_module.MemoryStore(
            _storage_module.user_memories_dir(),
            _storage_module.project_memories_dir(),
        )
        _memory_store.set_graph(_get_graph_index())
    return _memory_store


def _get_search_index():
    """Get the memory retrieval index."""
    global _search_index
    if _search_index is None:
        _search_index = _search_module.SearchIndex(
            _get_indexed_mem_store(),
            graph=_get_graph_index(),
        )
    return _search_index


def _get_graph_index():
    """Get the associative memory graph."""
    global _graph_index
    if _graph_index is None:
        _graph_index = _graph_module.GraphIndex(
            _storage_module.plugin_data_dir() / "graph.db"
        )
    return _graph_index


def _get_reflection_engine():
    """Get the reflection engine."""
    global _reflection_engine
    if _reflection_engine is None:
        _reflection_engine = _reflection_module.ReflectionEngine(
            _get_indexed_mem_store(),
            _get_search_index(),
            _get_graph_index(),
            log_path=_storage_module.plugin_data_dir() / "reflect-log.jsonl",
        )
    return _reflection_engine


def _get_memory_context(query: str = "", max_tokens: int = 4000) -> str:
    """Build context from memory, search, and skills."""
    from .store import SkillStore
    store = _get_indexed_mem_store()
    search = _get_search_index()
    skills = SkillStore(_storage_module.user_skills_dir(), _storage_module.project_skills_dir())
    return _context_module.build_context(store, search, skills, query, max_tokens)


def _get_indexed_skill_store():
    """Get the skill store."""
    return _storage_module.SkillStore(
        _storage_module.user_skills_dir(),
        _storage_module.project_skills_dir(),
    )


# Route package-level late-binding consumers to the indexed persistence layer.
_get_mem_store = _get_indexed_mem_store
_get_skill_store = _get_indexed_skill_store

