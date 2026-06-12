"""Memory management: curation, bridging, and context assembly.

This package contains:
- curator/: Composable action pipeline for memory lifecycle (v1.5 refactor)
  - actions.py: CuratorAction classes (ArchiveStale, CompactChains, etc.)
  - helpers.py: is_protected, build_cold_entry, archive_and_delete, config
  - cold_store.py: JSONL append-only cold storage
  - report.py: Report generation and persistence
- bridge.py: Bidirectional sync between built-in and plugin memory
- context.py: Context assembly with stable/dynamic split and graded compression
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
