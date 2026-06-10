"""Memory management: curation, bridging, and context assembly.

This package contains:
- curator.py: Automated memory lifecycle management (4-phase pipeline)
- bridge.py: Bidirectional sync between built-in and plugin memory
- context.py: Palace-mode context assembly
"""

from .curator import (
    archive_superseded,
    scan_for_stale,
    scan_for_similar,
    archive_expired,
    _run_curator,
    _curator_enabled,
    _curator_config,
)

from .bridge import (
    mirror_builtin_to_plugin,
    mirror_plugin_to_builtin,
    bridge_enabled,
    sync_builtin_to_plugin,
    _refine_body,
)

from .context import (
    build_context_block,
    build_context_bundle,
    ContextBundle,
)

__all__ = [
    # Curator exports
    "archive_superseded",
    "scan_for_stale",
    "scan_for_similar",
    "archive_expired",
    "_run_curator",
    "_curator_enabled",
    "_curator_config",
    # Bridge exports
    "mirror_builtin_to_plugin",
    "mirror_plugin_to_builtin",
    "bridge_enabled",
    "sync_builtin_to_plugin",
    "_refine_body",
    # Context exports
    "build_context_block",
    "build_context_bundle",
    "ContextBundle",
]
