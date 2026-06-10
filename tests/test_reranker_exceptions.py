"""test_reranker_exceptions.py — Tests for reranker failure fallback paths.

Coverage:
- CrossEncoderReranker.rerank() exception fallback (returns candidates unchanged)
- CohereReranker.rerank() exception fallback
- CohereReranker missing API key path

Run: pytest tests/test_reranker_exceptions.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from mem_reflection_hermes.core.reranker import (
    CrossEncoderReranker,
    CohereReranker,
)


class DummyCandidate:
    def __init__(self, body: str):
        self.body = body


class TestCrossEncoderRerankerFailures:
    def test_rerank_exception_returns_candidates_unchanged(self):
        """If model.predict() raises, candidates should be returned as-is."""
        rr = CrossEncoderReranker()
        # Pretend model is already loaded
        mock_model = MagicMock()
        mock_model.predict.side_effect = RuntimeError("simulated OOM")
        rr._model = mock_model

        candidates = [DummyCandidate("a"), DummyCandidate("b")]
        result = rr.rerank("query", candidates)

        assert result == candidates

    def test_rerank_empty_candidates_short_circuits(self):
        """Empty candidates should return immediately without touching model."""
        rr = CrossEncoderReranker()
        assert rr.rerank("q", []) == []


class TestCohereRerankerFailures:
    def test_rerank_no_api_key_returns_candidates(self):
        """Without API key, rerank should return candidates unchanged."""
        rr = CohereReranker(api_key=None)
        candidates = [DummyCandidate("a")]
        assert rr.rerank("q", candidates) == candidates

    def test_rerank_exception_returns_candidates_unchanged(self):
        """If Cohere API raises, candidates should be returned as-is."""
        rr = CohereReranker(api_key="fake-key")
        mock_client = MagicMock()
        mock_client.rerank.side_effect = RuntimeError("simulated API failure")
        rr._client = mock_client

        candidates = [DummyCandidate("x"), DummyCandidate("y")]
        result = rr.rerank("query", candidates)

        assert result == candidates

    def test_rerank_sdk_shape_fallback(self):
        """Older Cohere SDK shapes (document.text) should be handled."""
        rr = CohereReranker(api_key="fake-key")
        mock_client = MagicMock()

        # Simulate old SDK shape where result has .document.text instead of .index
        old_result = MagicMock()
        old_result.index = None
        old_result.document = MagicMock()
        old_result.document.text = "second"

        resp = MagicMock()
        resp.results = [old_result]
        mock_client.rerank.return_value = resp
        rr._client = mock_client

        candidates = [DummyCandidate("first"), DummyCandidate("second")]
        result = rr.rerank("q", candidates)

        # Should still include both candidates (old shape resolved + unseen appended)
        bodies = [c.body for c in result]
        assert "second" in bodies
