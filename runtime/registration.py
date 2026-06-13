"""Plugin registration logic for Hermes Agent.

register() in __init__.py delegates here so the package entrypoint stays
focused on imports and exports.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_TOOLSET = "mem_reflection_hermes"


def register(ctx: Any) -> None:
    """Register plugin with Hermes host."""
    # Deferred imports to avoid circular dependency at module load time.
    from ..runtime.hooks import register_hooks, register_commands
    from ..runtime.schemas import (
        _SRH_ASSOCIATE_SCHEMA,
        _SRH_COMPILE_PROFILE_SCHEMA,
        _SRH_GRAPH_RETRIEVE_SCHEMA,
        _SRH_GRAPH_STATS_SCHEMA,
        _SRH_GRAPH_VIZ_SCHEMA,
        _SRH_MEMORY_DELETE_SCHEMA,
        _SRH_MEMORY_HEALTH_SCHEMA,
        _SRH_MEMORY_HISTORY_SCHEMA,
        _SRH_MEMORY_SEARCH_SCHEMA,
        _SRH_MEMORY_WRITE_SCHEMA,
        _SRH_PALACE_NAVIGATE_SCHEMA,
        _SRH_REFLECT_NOW_SCHEMA,
        _SRH_SKILL_QUERY_SCHEMA,
    )
    from ..runtime.tools import (
        srh_compile_profile,
        srh_memory_delete,
        srh_memory_history,
        srh_memory_search,
        srh_memory_write,
        srh_palace_navigate,
        srh_reflect_now,
        srh_skill_query,
    )
    from .. import _get_mem_store, _get_graph_mgr

    # Register runtime hooks
    register_hooks(ctx)

    # Register slash commands
    register_commands(ctx)

    # Sync built-in memory to plugin store (one-time)
    try:
        from ..memory.bridge import sync_builtin_to_plugin
        sync_builtin_to_plugin(_get_mem_store())
    except Exception as e:
        logger.warning("Built-in memory sync failed: %s", e)

    # Register tools (8 base + 5 graph/health = 13 total)
    ctx.register_tool(
        name="srh_memory_write",
        toolset=_TOOLSET,
        schema=_SRH_MEMORY_WRITE_SCHEMA,
        handler=srh_memory_write,
        description="Write a new memory or update existing memory, including optional user_id/agent_id/run_id scope",
    )
    ctx.register_tool(
        name="srh_memory_search",
        toolset=_TOOLSET,
        schema=_SRH_MEMORY_SEARCH_SCHEMA,
        handler=srh_memory_search,
        description="Search memories by query with optional scope filters",
    )
    ctx.register_tool(
        name="srh_memory_delete",
        toolset=_TOOLSET,
        schema=_SRH_MEMORY_DELETE_SCHEMA,
        handler=srh_memory_delete,
        description="Delete a memory by ID or batch-delete by scope filters",
    )
    ctx.register_tool(
        name="srh_memory_history",
        toolset=_TOOLSET,
        schema=_SRH_MEMORY_HISTORY_SCHEMA,
        handler=srh_memory_history,
        description="Trace supersedes chain history",
    )
    ctx.register_tool(
        name="srh_palace_navigate",
        toolset=_TOOLSET,
        schema=_SRH_PALACE_NAVIGATE_SCHEMA,
        handler=srh_palace_navigate,
        description="Navigate palace index with optional zone and scope filters",
    )
    ctx.register_tool(
        name="srh_reflect_now",
        toolset=_TOOLSET,
        schema=_SRH_REFLECT_NOW_SCHEMA,
        handler=srh_reflect_now,
        description="Trigger immediate reflection with optional scope filters",
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
        description="Compile profile/palace/zone summaries, optionally scoped to a tenant/user/run",
    )

    # Register graph/health tools (5)
    from ..runtime.graph import (
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

    # P2a: Wire graph manager into the store and register post-delete callback.
    # Without set_graph(), SearchIndex._graph and typed-fact sidecar remain
    # disabled for the lifetime of the process (v1.7 regression).
    try:
        ms = _get_mem_store()
        gm = _get_graph_mgr()
        if ms is not None and gm is not None:
            ms.set_graph(gm)
            def _on_memory_delete(mem_id: str) -> None:
                try:
                    gm.store.remove_memory(mem_id)
                except Exception:
                    logger.warning("Graph cleanup failed for deleted memory %s", mem_id, exc_info=True)
            ms._post_delete_callbacks.append(_on_memory_delete)
    except Exception as e:
        logger.warning("Failed to wire graph manager to store: %s", e)
