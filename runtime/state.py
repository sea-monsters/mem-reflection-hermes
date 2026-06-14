"""Singleton getters and config cap helpers for the package entrypoint.

These live here so __init__.py stays focused on imports, aliases, and the
register() delegator. All symbols are re-exported from mem_reflection_hermes
so existing _lb() callers continue to work.
"""
from __future__ import annotations

import threading
from typing import Any, Optional


# ── Config cap helpers ─────────────────────────────────────────────────

def _palace_instructions_enabled() -> bool:
    from ..core.store import plugin_config
    return bool(plugin_config().get("palace_instructions", True))


def _active_memory_cap() -> int:
    from ..core.store import plugin_config
    return int(plugin_config().get("active_memory_index_cap", 20))


def _skill_index_cap() -> int:
    from ..core.store import plugin_config
    return int(plugin_config().get("skill_index_cap", 20))


def _relevant_memory_cap() -> int:
    from ..core.store import plugin_config
    return int(plugin_config().get("relevant_memory_cap", 5))


def _triggered_skill_cap() -> int:
    from ..core.store import plugin_config
    return int(plugin_config().get("triggered_skill_cap", 3))


def _config_compaction() -> bool:
    """Check if episode compaction is enabled in plugin config (default: True)."""
    from ..core.store import plugin_config
    return bool(plugin_config().get("compaction", {}).get("enabled", True))


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
                from ..core.store import MemoryStore
                from ..core.config import user_memories_dir
                _mem_store = MemoryStore(user_root=user_memories_dir())
    return _mem_store


def _get_skill_store():
    """Get or create the global SkillStore singleton."""
    global _skill_store
    if _skill_store is None:
        with _skill_store_lock:
            if _skill_store is None:
                from ..core.store import SkillStore
                from ..core.config import user_memories_dir
                _skill_store = SkillStore(user_root=user_memories_dir())
    return _skill_store


def _get_search_index():
    """Get or create the global SearchIndex singleton."""
    global _search_index
    if _search_index is None:
        with _search_lock:
            if _search_index is None:
                from ..core.search import SearchIndex
                try:
                    from ..core.reranker import _build_reranker
                    from ..core.store import plugin_config, CONFIG_KEY_RERANKER
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
                from ..runtime.graph import _get_graph_mgr as _ggm
                _graph_mgr = _ggm()
    return _graph_mgr
