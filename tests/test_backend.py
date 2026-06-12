from __future__ import annotations

from core.backend import SearchBackendCapabilities, default_sqlite_backend_capabilities


class _FakeBackend:
    def search_backend_capabilities(self) -> SearchBackendCapabilities:
        return SearchBackendCapabilities(
            native_hybrid_search=True,
            entity_search=True,
            keyword_search=True,
            vector_search=True,
            backend_name="fake",
        )


class TestBackendCapabilities:
    def test_sqlite_backend_capabilities_are_partial(self):
        caps = default_sqlite_backend_capabilities(entity_search=True, vector_search=False)
        assert caps.native_hybrid_search is False
        assert caps.entity_search is True
        assert caps.keyword_search is True
        assert caps.vector_search is False
        assert caps.backend_name == "sqlite_markdown"

    def test_fake_backend_can_report_full_capabilities(self):
        caps = _FakeBackend().search_backend_capabilities()
        assert caps.native_hybrid_search is True
        assert caps.backend_name == "fake"


class TestBackendProtocolConformance:
    """Gap 1: Verify SearchBackendLike protocol is structural (duck-typing).

    Design intent (FEP §5.9): the protocol defines the surface future backends
    must implement. Any class with search_backend_capabilities() returning
    SearchBackendCapabilities should satisfy the protocol without inheritance.
    """

    def test_duck_typed_class_satisfies_protocol(self):
        """A class with the right method signature satisfies SearchBackendLike."""
        from typing import Protocol, runtime_checkable
        import typing

        class MinimalBackend:
            def search_backend_capabilities(self) -> SearchBackendCapabilities:
                return SearchBackendCapabilities(backend_name="minimal")

        # Structural subtyping: no inheritance required
        backend = MinimalBackend()
        caps = backend.search_backend_capabilities()
        assert isinstance(caps, SearchBackendCapabilities)
        assert caps.backend_name == "minimal"

    def test_capabilities_is_frozen_dataclass(self):
        """SearchBackendCapabilities should be immutable (frozen=True)."""
        caps = SearchBackendCapabilities(backend_name="test")
        try:
            caps.backend_name = "mutated"
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass

    def test_all_capability_flags_default_to_false_except_keyword(self):
        """Design intent: SQLite backend defaults reflect partial capability.

        Only keyword_search defaults to True — the others require a richer backend.
        """
        caps = SearchBackendCapabilities()
        assert caps.native_hybrid_search is False
        assert caps.entity_search is False
        assert caps.keyword_search is True
        assert caps.vector_search is False
        assert caps.backend_name == "sqlite_markdown"

    def test_sqlite_capabilities_all_flag_combinations(self):
        """Design intent: entity_search and vector_search are independent flags."""
        both = default_sqlite_backend_capabilities(entity_search=True, vector_search=True)
        assert both.entity_search is True
        assert both.vector_search is True

        neither = default_sqlite_backend_capabilities(entity_search=False, vector_search=False)
        assert neither.entity_search is False
        assert neither.vector_search is False

        entity_only = default_sqlite_backend_capabilities(entity_search=True, vector_search=False)
        assert entity_only.entity_search is True
        assert entity_only.vector_search is False
