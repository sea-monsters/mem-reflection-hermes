"""Backend capability surface for future hybrid search backends."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchBackendCapabilities:
    native_hybrid_search: bool = False
    entity_search: bool = False
    keyword_search: bool = True
    vector_search: bool = False
    backend_name: str = "sqlite_markdown"


class SearchBackendLike(Protocol):
    def search_backend_capabilities(self) -> SearchBackendCapabilities: ...


def default_sqlite_backend_capabilities(*, entity_search: bool, vector_search: bool) -> SearchBackendCapabilities:
    """Capabilities for the current SQLite/Markdown backend."""
    return SearchBackendCapabilities(
        native_hybrid_search=False,
        entity_search=entity_search,
        keyword_search=True,
        vector_search=vector_search,
        backend_name="sqlite_markdown",
    )
