"""Optional reranker layer — ported from mem0 pattern, adapted for SRH.

Provides pluggable second-stage reranking after the primary retrieval pipeline
(recall → fusion → Hebbian boost).  Rerankers are optional; if the provider is
unavailable or misconfigured the pipeline falls back to the original order.

Supported providers:
    cross_encoder  – Local sentence-transformers CrossEncoder (default)
    cohere         – Cohere rerank API (rerank-english-v3.0)

Configuration (config.yaml under plugins.mem_reflection_hermes):
    reranker:
        provider: cross_encoder
        model: cross-encoder/ms-marco-MiniLM-L-6-v2
        # api_key: <cohere-key>   # required for cohere provider
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------

class BaseReranker(ABC):
    """Abstract reranker — receives candidates and returns them reordered."""

    @abstractmethod
    def rerank(
        self,
        query: str,
        candidates: List[Any],
        top_k: Optional[int] = None,
    ) -> List[Any]:
        """Rerank candidates for *query*.

        Args:
            query: the original search query
            candidates: list of LoadedMemory (or any object with ``.body``)
            top_k: optional hard limit on returned items

        Returns:
            Reordered list of candidates.  Must preserve every input item
            unless *top_k* truncates.  On failure returns *candidates* unchanged.
        """
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(candidate: Any) -> str:
    """Best-effort text extraction from a candidate object."""
    if hasattr(candidate, "body"):
        body = candidate.body
        if body:
            return str(body)
    # Fallback — frontmatter body if present
    if hasattr(candidate, "frontmatter"):
        fm = candidate.frontmatter
        if hasattr(fm, "body") and fm.body:
            return str(fm.body)
    # Final fallback
    return str(candidate)


def _build_reranker(config: Dict[str, Any]) -> Optional[BaseReranker]:
    """Factory — build a reranker from a config dict.

    Returns *None* if the provider is unknown or optional dependencies are missing.
    """
    provider = (config.get("provider") or "").strip().lower()
    if not provider:
        return None
    model = config.get("model")
    try:
        if provider == "cross_encoder":
            return CrossEncoderReranker(
                model=model or "cross-encoder/ms-marco-MiniLM-L-6-v2"
            )
        if provider == "cohere":
            return CohereReranker(
                model=model or "rerank-english-v3.0",
                api_key=config.get("api_key"),
            )
        logger.warning("Unknown reranker provider: %s", provider)
        return None
    except Exception as e:
        logger.warning("Reranker init failed (%s): %s", provider, e)
        return None


# ---------------------------------------------------------------------------
# CrossEncoder (local, zero network cost)
# ---------------------------------------------------------------------------

class CrossEncoderReranker(BaseReranker):
    """Local cross-encoder reranker via sentence-transformers.

    Suitable for privacy-sensitive deployments because no data leaves the host.
    The model is loaded lazily on first ``rerank()`` call.
    """

    def __init__(self, model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.model_name = model
        self._model: Optional[Any] = None

    def _get_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(
        self,
        query: str,
        candidates: List[Any],
        top_k: Optional[int] = None,
    ) -> List[Any]:
        if not candidates:
            return candidates
        try:
            texts = [_extract_text(c) for c in candidates]
            pairs = [[query, t] for t in texts]
            scores = self._get_model().predict(
                pairs,
                batch_size=min(32, len(pairs)),
                show_progress_bar=False,
            )
            # scores may be np.ndarray or list
            if hasattr(scores, "tolist"):
                scores = scores.tolist()
            scored = list(zip(scores, candidates))
            scored.sort(key=lambda x: x[0], reverse=True)
            result = [c for _, c in scored]
            if top_k:
                result = result[:top_k]
            return result
        except Exception as e:
            logger.warning("CrossEncoder rerank failed: %s", e)
            return candidates


# ---------------------------------------------------------------------------
# Cohere (API-based, highest quality)
# ---------------------------------------------------------------------------

class CohereReranker(BaseReranker):
    """API-based reranker using Cohere rerank-v3.

    Requires ``cohere`` package and a valid API key (env var ``COHERE_API_KEY``
    or explicit ``api_key``).
    """

    def __init__(
        self,
        model: str = "rerank-english-v3.0",
        api_key: Optional[str] = None,
    ):
        self.model = model
        self.api_key = api_key or os.getenv("COHERE_API_KEY")
        self._client: Optional[Any] = None

    def _get_client(self) -> Any:
        if self._client is None:
            import cohere
            self._client = cohere.Client(self.api_key)
        return self._client

    def rerank(
        self,
        query: str,
        candidates: List[Any],
        top_k: Optional[int] = None,
    ) -> List[Any]:
        if not candidates:
            return candidates
        if not self.api_key:
            logger.warning("Cohere rerank skipped: no API key")
            return candidates
        try:
            texts = [_extract_text(c) for c in candidates]
            resp = self._client.rerank(
                model=self.model,
                query=query,
                documents=texts,
                top_n=top_k or len(candidates),
            )
            # resp.results is list of Result objects with .index and .relevance_score
            ranked = []
            for r in resp.results:
                idx = getattr(r, "index", None)
                if idx is None and hasattr(r, "document"):
                    # Fallback for older SDK shapes
                    idx = texts.index(getattr(r.document, "text", str(r.document)))
                if idx is not None and 0 <= idx < len(candidates):
                    ranked.append(candidates[idx])
            # Ensure all candidates are present (SDK may drop low-scorers)
            seen = {id(c) for c in ranked}
            for c in candidates:
                if id(c) not in seen:
                    ranked.append(c)
            if top_k:
                ranked = ranked[:top_k]
            return ranked
        except Exception as e:
            logger.warning("Cohere rerank failed: %s", e)
            return candidates
