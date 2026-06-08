"""mem-reflection-hermes plugin -- Self-evolving memory and reflection system.

v1.2-beta2 Architecture (organized by functionality):
- core/: SQLite storage, search engine, graph index (3,650 LOC)
- reflection/: Reflection engine and runtime (2,503 LOC)
- memory/: Curation, bridge, context assembly (1,783 LOC)
- runtime/: Tools and lifecycle hooks (1,708 LOC)
- web/: FastAPI dashboard endpoints (830 LOC)

Total: 11 core modules, 10,689 lines
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Import from core modules
from .core.store import (  # noqa: F401
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
    _lineage_latest, _lineage_root, _lineage_depth, _lineage_cycle_check,
    _classify_update_intent, _is_expired, _is_context_mismatch,
)

from .core.search import (  # noqa: F401
    SearchIndex,
    _embed_single, _cosine_sim, _extract_keywords, _bm25_search_scored,
    _is_explicit_memory_intent,
)

from .core.graph import (  # noqa: F401
    GraphIndex,
)

# Import from reflection modules
from .reflection.engine import (  # noqa: F401
    ReflectionEngine,
    _is_memorable_content,
    _is_explicit_memory_intent as _is_explicit_memory_intent_reflect,
)

from .reflection.runtime import (  # noqa: F401
    _run_full_reflection,
    _run_micro_reflection,
    _run_embedding_micro_reflection,
    _append_reflect_log,
    _compact_episode_zone,
    _approve_skill,
    _reject_skill,
    _load_pending_skill_candidates,
    _format_pending_skills_for_display,
    _reset_current_session_memory_ids,
    _recent_reflect_outcomes,
)

# Import from memory modules
from .memory.curator import (  # noqa: F401
    archive_superseded,
    scan_for_stale,
    scan_for_similar,
    archive_expired,
    _run_curator,
    _curator_enabled,
    _curator_config,
)

from .memory.bridge import (  # noqa: F401
    mirror_builtin_to_plugin,
    mirror_plugin_to_builtin,
    bridge_enabled,
    sync_builtin_to_plugin,
    _refine_body,
)

from .memory.context import (  # noqa: F401
    build_context_block,
)

# Import from runtime modules
from .runtime.tools import (  # noqa: F401
    srh_memory_write,
    srh_memory_delete,
    srh_palace_navigate,
    srh_reflect_now,
    srh_skill_query,
    srh_compile_profile,
)

from .runtime.hooks import (  # noqa: F401
    on_session_start,
    on_session_end,
    pre_llm_call,
    post_tool_call,
    register_hooks,
    register_commands,
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
_batch_record_stats = batch_record_stats

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
    mod_name = getattr(__spec__, "name", __name__) if "__spec__" in dir() else __name__
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
        sys.modules[mod_name].__dict__.update(globals())


# ── Singleton getters (used by tools and hooks) ────────────────────────

_mem_store: Optional[Any] = None
_mem_store_lock = threading.Lock()

_skill_store: Optional[Any] = None
_skill_store_lock = threading.Lock()

_search_index: Optional[Any] = None
_search_lock = threading.Lock()

_graph_mgr: Optional[Any] = None
_graph_mgr_lock = threading.Lock()


def _get_mem_store():
    """Get or create the global MemoryStore singleton."""
    global _mem_store
    if _mem_store is None:
        with _mem_store_lock:
            if _mem_store is None:
                from .core.store import MemoryStore
                _mem_store = MemoryStore()
    return _mem_store


def _get_skill_store():
    """Get or create the global SkillStore singleton."""
    global _skill_store
    if _skill_store is None:
        with _skill_store_lock:
            if _skill_store is None:
                from .core.store import SkillStore
                _skill_store = SkillStore()
    return _skill_store


def _get_search_index():
    """Get or create the global SearchIndex singleton."""
    global _search_index
    if _search_index is None:
        with _search_lock:
            if _search_index is None:
                from .core.search import SearchIndex
                try:
                    from .core.reranker import _build_reranker
                    from .core.store import plugin_config, CONFIG_KEY_RERANKER
                    cfg = plugin_config()
                    reranker = _build_reranker(cfg.get(CONFIG_KEY_RERANKER, {}))
                except Exception:
                    reranker = None
                _search_index = SearchIndex(_get_mem_store(), reranker=reranker)
    return _search_index


def _get_graph_mgr():
    """Get or create the global graph manager singleton."""
    global _graph_mgr
    if _graph_mgr is None:
        with _graph_mgr_lock:
            if _graph_mgr is None:
                # Import from runtime.graph (which uses core.graph)
                from .runtime.graph import _get_graph_mgr as _ggm
                _graph_mgr = _ggm()
    return _graph_mgr


# ── Legacy tool surface for Hermes host (used by __init__.py exports) ─────

def _build_context_block(query: str = "") -> str:
    """Build context block using memory.context module."""
    from .memory.context import build_context_block
    return build_context_block(query)


def _estimate_tokens(text: str) -> int:
    """Estimate token count using store module."""
    from .core.store import _memory_tokens
    return _memory_tokens(text)


def match_skills(skills, query, k=10):
    """Match skills using search module."""
    from .core.search import SearchIndex
    # Skills are matched via BM25 on their description field
    return SearchIndex.match_skills(skills, query, k)


# ── Tool Schemas ──────────────────────────────────────────────────────

_SRH_MEMORY_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string", "description": "Memory content to store"},
        "scope": {"type": "string", "enum": ["user", "project"], "description": "User or project scope"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"], "description": "Confidence level"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags"},
        "pinned": {"type": "boolean", "description": "Pin the memory to the top"},
        "zone": {"type": "string", "description": "Memory zone (general, work, episode, core, or project:<name>)"},
        "supersedes": {"type": "array", "items": {"type": "string"}, "description": "IDs of memories this replaces"},
    },
    "required": ["body"],
}

_SRH_MEMORY_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query text"},
        "k": {"type": "integer", "description": "Maximum results to return (default 5)"},
        "zone": {"type": "string", "description": "Filter to a specific zone"},
        "include_history": {"type": "boolean", "description": "Include superseded memories"},
    },
    "required": ["query"],
}

_SRH_MEMORY_DELETE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Memory ID to delete"},
        "scope": {"type": "string", "enum": ["user", "project"], "description": "User or project scope"},
    },
    "required": ["id"],
}

_SRH_PALACE_NAVIGATE_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "description": "Topic to recall memories for"},
        "limit": {"type": "integer", "description": "Maximum memories to return (default 5)"},
        "zone": {"type": "string", "description": "Specific zone to search, or null for active zone"},
    },
    "required": ["topic"],
}

_SRH_REFLECT_NOW_SCHEMA = {
    "type": "object",
    "properties": {
        "messages": {"type": "array", "description": "Conversation messages to reflect on"},
        "mode": {"type": "string", "enum": ["full", "micro", "embedding"], "description": "Reflection mode"},
    },
    "required": ["messages"],
}

_SRH_SKILL_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Skill search query"},
        "k": {"type": "integer", "description": "Maximum results to return (default 3)"},
    },
    "required": ["query"],
}

_SRH_COMPILE_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["profile", "summary", "stats"], "description": "Compilation mode"},
    },
    "required": ["mode"],
}

_SRH_ASSOCIATE_SCHEMA = {
    "type": "object",
    "properties": {
        "memory_ids": {"type": "array", "items": {"type": "string"}, "description": "Memory IDs to associate (max 20)"},
        "context": {"type": "string", "description": "Optional context string"},
        "relation": {"type": "string", "enum": ["co_occurs", "supersedes", "related"], "description": "Relation type"},
        "seed_ids": {"type": "array", "items": {"type": "string"}, "description": "Seed memory IDs for spreading activation"},
    },
    "required": ["memory_ids"],
}

_SRH_GRAPH_RETRIEVE_SCHEMA = {
    "type": "object",
    "properties": {
        "seed_ids": {"type": "array", "items": {"type": "string"}, "description": "Seed memory IDs to start retrieval from"},
        "max_results": {"type": "integer", "description": "Maximum number of results (default 10)"},
        "tier": {"type": "string", "enum": ["count", "rank", "all"], "description": "Result tier"},
    },
    "required": ["seed_ids"],
}

_SRH_GRAPH_STATS_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "string", "enum": ["adjacency", "nodes", "edges"], "description": "Output format"},
        "depth": {"type": "integer", "description": "Graph traversal depth (default 2)"},
    },
    "required": [],
}

_SRH_GRAPH_VIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "string", "enum": ["adjacency", "nodes", "edges"], "description": "Visualization format"},
        "depth": {"type": "integer", "description": "Graph traversal depth (default 2)"},
    },
    "required": [],
}

_SRH_MEMORY_HEALTH_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
}

_TOOLSET = "mem_reflection_hermes"

# ── Plugin Registration ───────────────────────────────────────────────

def register(ctx):
    """Register plugin with Hermes host."""
    # Register runtime hooks
    from .runtime.hooks import register_hooks
    register_hooks(ctx)

    # Register slash commands
    from .runtime.hooks import register_commands
    register_commands(ctx)

    # Sync built-in memory to plugin store (one-time)
    try:
        from .memory.bridge import sync_builtin_to_plugin
        sync_builtin_to_plugin(_get_mem_store())
    except Exception as e:
        logger.warning("Built-in memory sync failed: %s", e)

    # Register tools (7 base + 5 graph/health = 12 total)
    from .runtime.tools import (
        srh_memory_write,
        srh_memory_search,
        srh_memory_delete,
        srh_palace_navigate,
        srh_reflect_now,
        srh_skill_query,
        srh_compile_profile,
    )

    ctx.register_tool(
        name="srh_memory_write",
        toolset=_TOOLSET,
        schema=_SRH_MEMORY_WRITE_SCHEMA,
        handler=srh_memory_write,
        description="Write a new memory or update existing memory",
    )
    ctx.register_tool(
        name="srh_memory_search",
        toolset=_TOOLSET,
        schema=_SRH_MEMORY_SEARCH_SCHEMA,
        handler=srh_memory_search,
        description="Search memories by query",
    )
    ctx.register_tool(
        name="srh_memory_delete",
        toolset=_TOOLSET,
        schema=_SRH_MEMORY_DELETE_SCHEMA,
        handler=srh_memory_delete,
        description="Delete a memory by ID",
    )
    ctx.register_tool(
        name="srh_palace_navigate",
        toolset=_TOOLSET,
        schema=_SRH_PALACE_NAVIGATE_SCHEMA,
        handler=srh_palace_navigate,
        description="Navigate palace index",
    )
    ctx.register_tool(
        name="srh_reflect_now",
        toolset=_TOOLSET,
        schema=_SRH_REFLECT_NOW_SCHEMA,
        handler=srh_reflect_now,
        description="Trigger immediate reflection",
    )
    ctx.register_tool(
        name="srh_skill_query",
        toolset=_TOOLSET,
        schema=_SRH_SKILL_QUERY_SCHEMA,
        handler=srh_skill_query,
        description="Query skills",
    )
    ctx.register_tool(
        name="srh_compile_profile",
        toolset=_TOOLSET,
        schema=_SRH_COMPILE_PROFILE_SCHEMA,
        handler=srh_compile_profile,
        description="Compile user profile from memories",
    )

    # Register graph/health tools (5)
    from .runtime.graph import (
        srh_associate,
        srh_graph_retrieve,
        srh_graph_stats,
        srh_graph_viz,
        srh_memory_health,
    )

    ctx.register_tool(
        name="srh_associate",
        toolset=_TOOLSET,
        schema=_SRH_ASSOCIATE_SCHEMA,
        handler=srh_associate,
        description="Associate memories in graph",
    )
    ctx.register_tool(
        name="srh_graph_retrieve",
        toolset=_TOOLSET,
        schema=_SRH_GRAPH_RETRIEVE_SCHEMA,
        handler=srh_graph_retrieve,
        description="Retrieve graph neighbors",
    )
    ctx.register_tool(
        name="srh_graph_stats",
        toolset=_TOOLSET,
        schema=_SRH_GRAPH_STATS_SCHEMA,
        handler=srh_graph_stats,
        description="Get graph statistics",
    )
    ctx.register_tool(
        name="srh_graph_viz",
        toolset=_TOOLSET,
        schema=_SRH_GRAPH_VIZ_SCHEMA,
        handler=srh_graph_viz,
        description="Generate graph visualization",
    )
    ctx.register_tool(
        name="srh_memory_health",
        toolset=_TOOLSET,
        schema=_SRH_MEMORY_HEALTH_SCHEMA,
        handler=srh_memory_health,
        description="Check memory health",
    )

    logger.info("mem-reflection-hermes plugin registered successfully")

# ── Exports for backward compatibility ───────────────────────────────────

__all__ = [
    # Core exports
    "MemoryStore",
    "MemoryFrontmatter",
    "LoadedMemory",
    "SearchIndex",
    "GraphIndex",
    # Reflection exports
    "ReflectionEngine",
    "_run_full_reflection",
    "_run_micro_reflection",
    # Memory exports
    "archive_superseded",
    "scan_for_stale",
    "_refine_body",
    "build_context_block",
    # Runtime exports
    "srh_memory_write",
#    "srh_memory_read",
#    "srh_memory_search",
#    "srh_memory_list",
#    "srh_memory_delete",
#    "srh_palace_navigate",
    "srh_reflect_now",
    "srh_skill_query",
    "srh_compile_profile",
    "on_session_start",
    "on_session_end",
    "pre_llm_call",
    "post_tool_call",
    "register_hooks",
    "register",
    # Config helpers
    "plugin_config",
    "plugin_data_dir",
    "hermes_home",
    # Legacy aliases
    "_get_mem_store",
    "_get_skill_store",
    "_get_search_index",
    "_get_graph_mgr",
    "_build_context_block",
    "_estimate_tokens",
    "match_skills",
]
