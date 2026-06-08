"""Runtime components: tools and lifecycle hooks.

This package contains:
- tools.py: 12 base tool handlers for memory operations
- hooks.py: Lifecycle hooks and slash commands
"""

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

__all__ = [
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
]
