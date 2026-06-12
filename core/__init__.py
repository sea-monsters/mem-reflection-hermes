"""Core storage and search engine modules.

This package contains the foundational components:
- store.py: SQLite-backed MemoryStore with frontmatter I/O
- search.py: BM25 + embedding + fusion search engine
- graph.py: Hebbian co-activation graph with PageRank
"""

from .store import (
    MemoryStore,
    MemoryFrontmatter,
    LoadedMemory,
    plugin_config,
    plugin_data_dir,
    hermes_home,
)

from .search import (
    SearchIndex,
    _embed_single,
    _cosine_sim,
    _bm25_search_scored,
)

from .config import (
    get_plugin_config_model,
    get_config_diagnostics,
)

from .backend import (
    SearchBackendCapabilities,
    default_sqlite_backend_capabilities,
)

from .graph import (
    GraphIndex,
)

__all__ = [
    # Store exports
    "MemoryStore",
    "MemoryFrontmatter",
    "LoadedMemory",
    "plugin_config",
    "plugin_data_dir",
    "hermes_home",
    # Search exports
    "SearchIndex",
    "_embed_single",
    "_cosine_sim",
    "_bm25_search_scored",
    "get_plugin_config_model",
    "get_config_diagnostics",
    "SearchBackendCapabilities",
    "default_sqlite_backend_capabilities",
    # Graph exports
    "GraphIndex",
]
