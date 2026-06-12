"""mem-reflection-hermes plugin -- Self-evolving memory and reflection system.

v1.5 Architecture (organized by functionality):
- core/: SQLite storage, search engine, graph index
- reflection/: Reflection engine and runtime
- memory/: Curation, bridge, context assembly
- runtime/: Tools, schemas, hooks, registration, state
- web/: FastAPI dashboard endpoints

The package entrypoint below is intentionally thin: it imports from functional
subpackages, re-exports the public API, and delegates registration logic to
runtime.registration.register().
"""
from __future__ import annotations

import logging
import sys
import types

logger = logging.getLogger(__name__)

# Register module in sys.modules early to avoid dataclass resolution failure
# when loaded via importlib.util (Python 3.11 bug workaround).
if __name__ != "__main__" and __name__ not in sys.modules:
    mod_name = getattr(__spec__, "name", __name__) if "__spec__" in dir() else __name__
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
        sys.modules[mod_name].__dict__.update(globals())

# Ensure __package__ is set for relative imports when loaded via importlib
# without submodule_search_locations (e.g. spec_from_file_location with no
# package context).  Relative imports need __package__ to resolve '.'.
if not __package__:
    __package__ = "mem_reflection_hermes"


# ── Core exports ───────────────────────────────────────────────────────
from .core.store import (  # noqa: F401
    hermes_home, load_config, plugin_config, plugin_data_dir,
    user_memories_dir, project_memories_dir, user_skills_dir, project_skills_dir,
    embeddings_enabled, micro_reflection_enabled, palace_mode_enabled, profile_mode_enabled,
    normalize_zone, is_valid_zone, fast_hash,
    palace_index_path, zone_cache_dir, sanitize_zone_filename,
    MemoryStore, MemoryFrontmatter, LoadedMemory, MemoryStatEntry, MemoryEffectiveness,
    SkillFrontmatter, LoadedSkill,
    parse_frontmatter, serialize_frontmatter, read_memory,
    async_write_memory, record_memory_stat, batch_record_stats, load_effectiveness,
    is_cjk, cjk_ratio, adaptive_conflict_threshold, extract_entities,
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

from .core.graph import GraphIndex  # noqa: F401


# ── Reflection exports ─────────────────────────────────────────────────
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
    _reflection_mode,
    _save_pending_skill_candidates,
)


# ── Memory exports ─────────────────────────────────────────────────────
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
    build_context_bundle,
    ContextBundle,
)


# ── Runtime exports (schemas, tools, hooks, helpers, state) ────────────
from .runtime.schemas import (  # noqa: F401
    _SRH_MEMORY_WRITE_SCHEMA,
    _SRH_MEMORY_SEARCH_SCHEMA,
    _SRH_MEMORY_DELETE_SCHEMA,
    _SRH_PALACE_NAVIGATE_SCHEMA,
    _SRH_REFLECT_NOW_SCHEMA,
    _SRH_SKILL_QUERY_SCHEMA,
    _SRH_COMPILE_PROFILE_SCHEMA,
    _SRH_ASSOCIATE_SCHEMA,
    _SRH_GRAPH_RETRIEVE_SCHEMA,
    _SRH_GRAPH_STATS_SCHEMA,
    _SRH_GRAPH_VIZ_SCHEMA,
    _SRH_MEMORY_HEALTH_SCHEMA,
)

from .runtime.tools import (  # noqa: F401
    srh_memory_write,
    srh_memory_search,
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

from .runtime.helpers import (  # noqa: F401
    _build_context_block,
    _build_context_bundle,
    _estimate_tokens,
    load_zone_summary,
    save_zone_summary,
    match_skills,
)

from .runtime.state import (  # noqa: F401
    _palace_instructions_enabled,
    _active_memory_cap,
    _skill_index_cap,
    _relevant_memory_cap,
    _triggered_skill_cap,
    _config_compaction,
    _get_mem_store,
    _get_skill_store,
    _get_search_index,
    _get_graph_mgr,
)


# ── Backward-compat aliases ────────────────────────────────────────────
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


# ── Hermes registration entrypoint ─────────────────────────────────────
def register(ctx):
    """Register plugin with Hermes host. Delegates to runtime.registration."""
    from .runtime.registration import register as _register_impl
    return _register_impl(ctx)


__all__ = [
    # Core
    "MemoryStore", "MemoryFrontmatter", "LoadedMemory", "SearchIndex", "GraphIndex",
    # Reflection
    "ReflectionEngine", "_run_full_reflection", "_run_micro_reflection",
    # Memory
    "archive_superseded", "scan_for_stale", "_refine_body",
    "build_context_block", "build_context_bundle", "ContextBundle",
    # Runtime
    "srh_memory_write", "srh_reflect_now", "srh_skill_query", "srh_compile_profile",
    "on_session_start", "on_session_end", "pre_llm_call", "post_tool_call",
    "register_hooks", "register",
    # Config
    "plugin_config", "plugin_data_dir", "hermes_home",
    # Legacy aliases / _lb targets
    "_get_mem_store", "_get_skill_store", "_get_search_index", "_get_graph_mgr",
    "_build_context_block", "_estimate_tokens", "match_skills",
]
