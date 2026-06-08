"""test_reranker.py — Tests for the optional reranker layer.

Covers:
- BaseReranker interface compliance
- CrossEncoderReranker construction (lazy model load)
- CohereReranker construction (lazy client load)
- _extract_text helper with LoadedMemory and plain objects
- _build_reranker factory with valid/invalid configs
- Graceful fallback on missing optional dependencies
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from mem_reflection_hermes.core.reranker import (
    BaseReranker,
    CrossEncoderReranker,
    CohereReranker,
    _extract_text,
    _build_reranker,
)
from mem_reflection_hermes.core.search import SearchIndex


class DummyCandidate:
    """Minimal stand-in for LoadedMemory."""
    def __init__(self, body: str):
        self.body = body


class MockReranker(BaseReranker):
    """Test double that reverses candidate order."""
    def rerank(self, query, candidates, top_k=None):
        result = list(reversed(candidates))
        if top_k:
            result = result[:top_k]
        return result


# ---------------------------------------------------------------------------
# Interface tests
# ---------------------------------------------------------------------------

def test_mock_reranker_reverses():
    r = MockReranker()
    c = [DummyCandidate("a"), DummyCandidate("b"), DummyCandidate("c")]
    assert [x.body for x in r.rerank("q", c)] == ["c", "b", "a"]


def test_mock_reranker_top_k():
    r = MockReranker()
    c = [DummyCandidate("a"), DummyCandidate("b"), DummyCandidate("c")]
    assert [x.body for x in r.rerank("q", c, top_k=2)] == ["c", "b"]


# ---------------------------------------------------------------------------
# _extract_text helper
# ---------------------------------------------------------------------------

def test_extract_text_from_body():
    assert _extract_text(DummyCandidate("hello")) == "hello"


def test_extract_text_from_str():
    assert _extract_text("plain string") == "plain string"


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

def test_build_reranker_empty_config():
    assert _build_reranker({}) is None


def test_build_reranker_unknown_provider():
    assert _build_reranker({"provider": "does_not_exist"}) is None


def test_build_reranker_cross_encoder():
    rr = _build_reranker({"provider": "cross_encoder"})
    assert isinstance(rr, CrossEncoderReranker)


def test_build_reranker_cross_encoder_custom_model():
    rr = _build_reranker({"provider": "cross_encoder", "model": "custom-model"})
    assert rr.model_name == "custom-model"


def test_build_reranker_cohere_no_key():
    # No COHERE_API_KEY set; should still build but will skip at runtime
    rr = _build_reranker({"provider": "cohere"})
    assert isinstance(rr, CohereReranker)
    assert rr.api_key is None or isinstance(rr.api_key, str)


# ---------------------------------------------------------------------------
# Lazy-load / graceful-fallback tests
# ---------------------------------------------------------------------------

def test_cross_encoder_model_not_loaded_until_rerank():
    rr = CrossEncoderReranker()
    assert rr._model is None


def test_cohere_client_not_loaded_until_rerank():
    rr = CohereReranker()
    assert rr._client is None


# ---------------------------------------------------------------------------
# Integration-style: SearchIndex accepts reranker
# ---------------------------------------------------------------------------

def test_search_index_accepts_reranker(temp_store):
    r = MockReranker()
    idx = SearchIndex(temp_store, reranker=r)
    assert idx._reranker is r


# ---------------------------------------------------------------------------
# Regression: reranker does not break search with zero candidates
# ---------------------------------------------------------------------------

def test_reranker_with_empty_candidates():
    r = MockReranker()
    assert r.rerank("q", []) == []
