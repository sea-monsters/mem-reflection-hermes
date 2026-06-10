"""Runtime components: tools and lifecycle hooks.

This package contains:
- tools.py: 12 base tool handlers for memory operations
- hooks.py: Lifecycle hooks and slash commands
"""

from .schemas import (
    _SRH_ASSOCIATE_SCHEMA,
    _SRH_COMPILE_PROFILE_SCHEMA,
    _SRH_GRAPH_RETRIEVE_SCHEMA,
    _SRH_GRAPH_STATS_SCHEMA,
    _SRH_GRAPH_VIZ_SCHEMA,
    _SRH_MEMORY_DELETE_SCHEMA,
    _SRH_MEMORY_HEALTH_SCHEMA,
    _SRH_MEMORY_SEARCH_SCHEMA,
    _SRH_MEMORY_WRITE_SCHEMA,
    _SRH_PALACE_NAVIGATE_SCHEMA,
    _SRH_REFLECT_NOW_SCHEMA,
    _SRH_SKILL_QUERY_SCHEMA,
)

from .tools import (
    srh_memory_write,
    srh_memory_search,
    srh_memory_delete,
    srh_palace_navigate,
    srh_reflect_now,
    srh_skill_query,
    srh_compile_profile,
)

from .hooks import (
    on_session_start,
    on_session_end,
    pre_llm_call,
    post_tool_call,
    register_hooks,
    register_commands,
)

from ._lb import _lb

__all__ = [
    # Schemas
    "_SRH_MEMORY_WRITE_SCHEMA",
    "_SRH_MEMORY_SEARCH_SCHEMA",
    "_SRH_MEMORY_DELETE_SCHEMA",
    "_SRH_PALACE_NAVIGATE_SCHEMA",
    "_SRH_REFLECT_NOW_SCHEMA",
    "_SRH_SKILL_QUERY_SCHEMA",
    "_SRH_COMPILE_PROFILE_SCHEMA",
    "_SRH_ASSOCIATE_SCHEMA",
    "_SRH_GRAPH_RETRIEVE_SCHEMA",
    "_SRH_GRAPH_STATS_SCHEMA",
    "_SRH_GRAPH_VIZ_SCHEMA",
    "_SRH_MEMORY_HEALTH_SCHEMA",
    # Tools exports
    "srh_memory_write",
    "srh_memory_search",
    "srh_memory_delete",
    "srh_palace_navigate",
    "srh_reflect_now",
    "srh_skill_query",
    "srh_compile_profile",
    # Hooks exports
    "on_session_start",
    "on_session_end",
    "pre_llm_call",
    "post_tool_call",
    "register_hooks",
    "register_commands",
    # Late-binding helper
    "_lb",
]
