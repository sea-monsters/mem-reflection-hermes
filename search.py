"""Search layer for mem-reflection-hermes.

Three-layer retrieval: Recall (embedding + BM25) → Fusion (pool + normalize)
→ Rerank (weighted + Hebbian boost).

Replaces core.py BM25 + search/embed.py + fusion_search from __init__.py.
Uses store.py as the dependency (not core.py).
"""
from __future__ import annotations

import functools
import logging
import math
import os
import re
import threading
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import numpy as np
    _HAS_NUMPY = True
except Exception:
    _HAS_NUMPY = False

from cachetools import TTLCache

try:
    from .store import (
        LoadedMemory,
        MemoryEffectiveness,
        adaptive_conflict_threshold,
        normalize_bm25,
        _tokenise,
        embeddings_enabled,
        estimate_tokens,
    )
except ImportError:
    import store as _store_mod
    LoadedMemory = _store_mod.LoadedMemory
    MemoryEffectiveness = _store_mod.MemoryEffectiveness
    adaptive_conflict_threshold = _store_mod.adaptive_conflict_threshold
    normalize_bm25 = _store_mod.normalize_bm25
    _tokenise = _store_mod._tokenise
    embeddings_enabled = _store_mod.embeddings_enabled
    estimate_tokens = _store_mod.estimate_tokens

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Embedding engine (ONNX Runtime — fast, lightweight)
# ---------------------------------------------------------------------------

_onnx_session: Optional[Any] = None
_onnx_tokenizer: Optional[Any] = None
_embed_model_lock = threading.Lock()


def _get_onnx_session():
    """Lazy-load ONNX Runtime model session."""
    global _onnx_session, _onnx_tokenizer
    if _onnx_session is not None:
        return _onnx_session, _onnx_tokenizer
    with _embed_model_lock:
        if _onnx_session is not None:
            return _onnx_session, _onnx_tokenizer
        try:
            from optimum.onnxruntime import ORTModelForFeatureExtraction
            from transformers import AutoTokenizer
            model_name = os.environ.get("MEM_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
            cache_dir = os.path.expanduser("~/.cache/mem-reflection-hermes/onnx")
            tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir=cache_dir)
            model = ORTModelForFeatureExtraction.from_pretrained(model_name, cache_dir=cache_dir)
            _onnx_session = model
            _onnx_tokenizer = tokenizer
            logger.info("ONNX embedding model loaded: %s", model_name)
            return _onnx_session, _onnx_tokenizer
        except Exception as e:
            logger.info("ONNX embedding model not available: %s", e)
            return None, None


def _get_st_model():
    """Fallback: sentence-transformers model."""
    try:
        from sentence_transformers import SentenceTransformer
        model_name = os.environ.get("MEM_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        return SentenceTransformer(model_name)
    except Exception:
        return None


@functools.lru_cache(maxsize=500)
def _embed_single(text: str) -> Optional[List[float]]:
    """Embed a single text string. Returns None if embeddings unavailable.

    Uses ONNX Runtime if available, falls back to sentence-transformers.
    Results are cached via functools.lru_cache.
    """
    if not text or not text.strip():
        return None
    try:
        model, tokenizer = _get_onnx_session()
        if model is not None and tokenizer is not None:
            inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512)
            import torch
            with torch.no_grad():
                outputs = model(**inputs)
            vec = outputs.last_hidden_state.mean(dim=1).squeeze().numpy()
            # Normalize to unit vector
            norm = float((vec ** 2).sum() ** 0.5)
            if norm > 0:
                vec = vec / norm
            return vec.tolist()
    except Exception:
        pass
    # Fallback to sentence-transformers
    try:
        st_model = _get_st_model()
        if st_model is not None:
            vec = st_model.encode(text, normalize_embeddings=True)
            return vec.tolist() if hasattr(vec, "tolist") else list(vec)
    except Exception:
        pass
    return None


def _cosine_sim(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two dense vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        from scipy.spatial.distance import cosine as _cd
        return 1.0 - float(_cd(a, b))
    except Exception:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# BM25 search
# ---------------------------------------------------------------------------



def _bm25_search_scored(
    memories: List[LoadedMemory],
    query: str,
    k: int = 5,
    effectiveness: Optional[Dict[str, MemoryEffectiveness]] = None,
    doc_tokens: Optional[List[Counter]] = None,
    query_tokens: Optional[List[str]] = None,
) -> List[Tuple[LoadedMemory, float]]:
    """BM25 search with IDF-based scoring.

    Formula: IDF(q) * (k1+1)*TF / (TF + k1*(1-b+b*|D|/avgdl))
    k1=1.5, b=0.75 optimized for CJK mixed text.
    """
    k1, b = 1.5, 0.75
    if k == 0 or not memories:
        return []
    q_tokens = query_tokens if query_tokens is not None else _tokenise(query)
    if not q_tokens:
        return []
    n = len(memories)

    df: Dict[str, int] = {}
    doc_lens: List[int] = []
    raw_doc_tokens: List[Counter]
    if doc_tokens is not None:
        raw_doc_tokens = doc_tokens
    else:
        raw_doc_tokens = [Counter(_tokenise(m.body + " " + " ".join(m.frontmatter.tags or []))) for m in memories]

    for tokens in raw_doc_tokens:
        doc_lens.append(sum(tokens.values()))
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1

    avgdl = sum(doc_lens) / max(n, 1)

    q_tf = Counter(q_tokens)
    idf_cache: Dict[str, float] = {}
    for t in q_tf:
        df_t = df.get(t, 0)
        if df_t == 0:
            continue
        idf_cache[t] = math.log((n - df_t + 0.5) / (df_t + 0.5) + 1.0)

    if not idf_cache:
        return []

    scored: List[Tuple[float, LoadedMemory]] = []
    for i, (tokens, m) in enumerate(zip(raw_doc_tokens, memories)):
        doc_len = doc_lens[i]
        m_tf = tokens
        score = 0.0
        for t, q_count in q_tf.items():
            idf = idf_cache.get(t)
            if idf is None:
                continue
            tf = m_tf.get(t, 0)
            norm = k1 * (1 - b + b * doc_len / max(avgdl, 1))
            score += idf * (tf * (k1 + 1)) / (tf + norm) * q_count
        if score > 0:
            if effectiveness:
                eff = effectiveness.get(m.id())
                if eff:
                    score *= eff.factor() * eff.decay_factor()
            scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(m, s) for s, m in scored[:k]]



# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

def _extract_keywords(text: str, top_k: int = 5) -> List[str]:
    """Extract top-k keywords from text using TF-like scoring."""
    tokens = _tokenise(text)
    if not tokens:
        return []
    tf = Counter(tokens)
    scored = sorted(tf.items(), key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[:top_k]]


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SearchIndex — three-layer retrieval
# ---------------------------------------------------------------------------

class SearchIndex:
    """Three-layer retrieval: Recall → Fusion → Rerank.

    Parameters:
        store: MemoryStore instance for listing memories
        cache_ttl: TTL cache for search results (seconds)
    """

    def __init__(self, store, graph=None, cache_ttl: int = 60):
        self.store = store
        self._graph = graph  # GraphIndex, optional — enables Hebbian boost
        self._embed_array: Optional[Any] = None  # numpy array, rebuilt lazily
        self._embed_ids: List[str] = []
        self._embed_version = 0
        self._embed_lock = threading.Lock()
        # BM25s index (lazy, rebuilt on invalidate)
        self._bm25_retriever: Optional[Any] = None
        self._bm25_ids: List[str] = []
        self._bm25_lock = threading.Lock()
        # Result cache: (query, k, zone) -> results
        self._cache: TTLCache = TTLCache(maxsize=200, ttl=cache_ttl)
        self._cache_lock = threading.Lock()

    def invalidate_cache(self) -> None:
        """Clear result cache (call after mutations)."""
        with self._cache_lock:
            self._cache.clear()
        with self._embed_lock:
            self._embed_array = None
            self._embed_ids = []
            self._embed_version += 1
        with self._bm25_lock:
            self._bm25_retriever = None
            self._bm25_ids = []
        # Module-level lru_cache for single embeddings must also be cleared
        # after mutations to avoid stale vector comparisons in conflict detection.
        _embed_single.cache_clear()

    def _ensure_embed_index(self) -> bool:
        """Build numpy embedding array lazily."""
        if not embeddings_enabled():
            return False
        with self._embed_lock:
            if self._embed_array is not None:
                return True
            try:
                active = self.store.list_active()
                if not active:
                    return False
                vectors = []
                ids = []
                for m in active:
                    vec = _embed_single(m.body)
                    if vec is not None:
                        vectors.append(vec)
                        ids.append(m.id())
                if not vectors:
                    return False
                if _HAS_NUMPY:
                    self._embed_array = np.array(vectors, dtype=np.float32)
                else:
                    self._embed_array = vectors
                self._embed_ids = ids
                logger.info("Embedding index built: %d memories", len(ids))
                return True
            except Exception as e:
                logger.info("Embedding index build failed: %s", e)
                return False

    def _embed_search(self, query: str, k: int) -> Optional[Dict[str, float]]:
        """Embedding-based search returning {memory_id: cosine_similarity}."""
        if not self._ensure_embed_index():
            return None
        qvec = _embed_single(query)
        if qvec is None:
            return None
        try:
            if _HAS_NUMPY and isinstance(self._embed_array, np.ndarray):
                qarr = np.array(qvec, dtype=np.float32)
                scores = self._embed_array @ qarr  # dot product (all normalized)
                top_idx = np.argpartition(-scores, min(k, len(scores) - 1))[:k]
                return {self._embed_ids[i]: float(scores[i]) for i in top_idx}
            else:
                # Pure Python fallback
                scores = {}
                for mid, vec in zip(self._embed_ids, self._embed_array):
                    scores[mid] = _cosine_sim(qvec, vec)
                return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k])
        except Exception as e:
            logger.debug("Embedding search failed: %s", e)
            return None

    def _ensure_bm25_index(self) -> bool:
        """Build bm25s index lazily from active memories."""
        with self._bm25_lock:
            if self._bm25_retriever is not None:
                return True
            try:
                import bm25s as _bm25s
            except Exception:
                return False
            try:
                active = self.store.list_active()
                if not active:
                    return False
                corpus: List[str] = []
                ids: List[str] = []
                for m in active:
                    tokens = _tokenise(m.body + " " + " ".join(m.frontmatter.tags or []))
                    corpus.append(" ".join(tokens))
                    ids.append(m.id())
                if not corpus:
                    return False
                tok = _bm25s.tokenize(corpus, token_pattern=r"\S+", stopwords=[])
                retriever = _bm25s.BM25(corpus=corpus, k1=1.5, b=0.75)
                retriever.index(tok)
                self._bm25_retriever = retriever
                self._bm25_ids = ids
                logger.info("BM25 index built (bm25s): %d memories", len(ids))
                return True
            except Exception as e:
                logger.info("BM25 index build (bm25s) failed: %s", e)
                return False

    def _bm25_search_bm25s(
        self,
        query: str,
        k: int,
        effectiveness: Optional[Dict[str, MemoryEffectiveness]] = None,
    ) -> Dict[str, float]:
        """BM25 search via bm25s library returning {memory_id: score}."""
        if not self._ensure_bm25_index():
            return {}
        query_tokens = _tokenise(query)
        if not query_tokens:
            return {}
        try:
            scores = self._bm25_retriever.get_scores(query_tokens)
        except Exception:
            return {}
        # top-k via argpartition (numpy) or sort (pure Python)
        if _HAS_NUMPY:
            top_idx = np.argpartition(-scores, min(k, len(scores) - 1))[:k]
        else:
            indexed = [(float(scores[i]), i) for i in range(len(scores))]
            indexed.sort(reverse=True)
            top_idx = [idx for _, idx in indexed[:k]]
        results: Dict[str, float] = {}
        active_map = {m.id(): m for m in self.store.list_active()}
        for idx in top_idx:
            score = float(scores[idx])
            if score <= 0:
                continue
            mid = self._bm25_ids[idx]
            if mid not in active_map:
                continue
            if effectiveness:
                eff = effectiveness.get(mid)
                if eff:
                    score *= eff.factor() * eff.decay_factor()
            results[mid] = score
        return results

    # -----------------------------------------------------------------------
    # Three-layer retrieval
    # -----------------------------------------------------------------------

    def search(
        self,
        query: str,
        k: int = 5,
        zone: Optional[str] = None,
        alpha: float = 0.5,
        beta: float = 0.3,
        gamma: float = 0.1,
        delta: float = 0.1,
        hebbian_beta: float = 0.0,
        include_history: bool = False,
        fusion_mode: str = "rrf",
    ) -> List[LoadedMemory]:
        """Three-layer retrieval: Recall → Fusion → Rerank.

        Args:
            fusion_mode: \"rrf\" (default, parameter-free Reciprocal Rank Fusion)
                         or \"weighted\" (alpha/beta/gamma/delta hyperparameters)
            alpha, beta, gamma, delta: weights for weighted fusion mode only
            hebbian_beta: Hebbian boost coefficient (default 0 = off)
        """
        cache_key = (
            query.lower().strip(),
            k,
            zone,
            include_history,
            fusion_mode,
            alpha,
            beta,
            gamma,
            delta,
            hebbian_beta,
        )
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        active = self.store.list() if include_history else self.store.list_active()
        if not active:
            return []

        now = datetime.now(timezone.utc)
        effectiveness = self.store.effectiveness()
        recall_k = k * 4
        active_map: Dict[str, LoadedMemory] = {m.id(): m for m in active}

        # Layer 1: Recall
        embed_results: Dict[str, float] = {}
        if not include_history:
            try:
                emb = self._embed_search(query, recall_k)
                if emb is not None:
                    embed_results = emb
            except Exception as e:
                logger.warning("Embedding search failed, degrading to BM25: %s", e)

        # BM25: prefer bm25s (10-50x faster), fallback to handrolled
        bm25_results: Dict[str, float] = {}
        if not include_history:
            try:
                bm25_results = self._bm25_search_bm25s(query, recall_k, effectiveness)
            except Exception as e:
                logger.debug("bm25s search failed, falling back to handrolled: %s", e)
        if not bm25_results:
            bm25_scored = _bm25_search_scored(active, query, recall_k, effectiveness)
            bm25_results = {m.id(): s for m, s in bm25_scored}

        # Layer 2: Fusion
        if fusion_mode == "rrf":
            fused_scores = self._rrf_fusion(embed_results, bm25_results, active_map)
        else:
            fused_scores = self._weighted_fusion(
                embed_results, bm25_results, active_map, alpha, beta
            )

        if not fused_scores:
            return []

        # Layer 3: Rerank — recency, effectiveness, Hebbian, supersedes
        reranked: List[Tuple[float, LoadedMemory]] = []
        max_rerank_score = 0.0
        for mid, base_score in fused_scores.items():
            mem = active_map[mid]

            try:
                created = mem.frontmatter.created
                if isinstance(created, str):
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                else:
                    created_dt = created
                age_days = (now - created_dt).total_seconds() / 86400.0
                recency = math.exp(-age_days / 30.0)
            except Exception:
                recency = 0.5

            eff = effectiveness.get(mid)
            eff_factor = (eff.factor() * eff.decay_factor()) if eff else 0.0

            sup_depth = self._calc_supersedes_depth(mid)
            sup_factor = (1.0 + sup_depth) / (2.0 + sup_depth)

            if fusion_mode == "rrf":
                # RRF base score × bonuses (recency, eff, supersedes)
                score = base_score
                if gamma > 0:
                    score *= (1.0 + gamma * recency)
                if delta > 0:
                    score *= (1.0 + delta * eff_factor)
                score *= sup_factor
            else:
                # Weighted: recency and eff are additive terms in the weighted sum
                score = base_score * sup_factor

            if score > max_rerank_score:
                max_rerank_score = score
            reranked.append((score, mem))

        # Hebbian boost — spreading activation (HeLa-Mem §3.4 additive fusion)
        # Formula: S(v_j) = S_base(v_j) + β · Σ_{i∈N(j)} S_base(v_i) · w_ij
        # We approximate the sum term via graph.spread() and scale it to the
        # same magnitude as the max rerank score so the two signals are comparable.
        if hebbian_beta > 0 and self._graph is not None:
            try:
                pool_ids = [mid for mid in fused_scores]
                activation = self._graph.spread(pool_ids, decay=0.7, max_iter=30)
                scale = max_rerank_score if max_rerank_score > 0 else 1.0
                for i, (score, mem) in enumerate(reranked):
                    act = activation.get(mem.id(), 0.0)
                    if act > 0:
                        hebbian_score = hebbian_beta * min(act, 1.0) * scale
                        reranked[i] = (score + hebbian_score, mem)
            except Exception as e:
                logger.debug("Hebbian boost skipped: %s", e)

        reranked.sort(key=lambda x: x[0], reverse=True)
        results = [m for _, m in reranked]

        # MMR diversity re-ranking (optional, λ=0.7 balances relevance vs diversity)
        if k > 1:
            results = self._mmr_rerank(query, results, lambda_param=0.7, top_n=k * 2)

        if zone:
            nz = zone.lower().strip()
            results = [m for m in results if m.frontmatter.zone == nz]

        results = results[:k]
        with self._cache_lock:
            self._cache[cache_key] = results
        return results

    def _mmr_rerank(
        self,
        query: str,
        candidates: List[LoadedMemory],
        lambda_param: float = 0.7,
        top_n: int = 20,
    ) -> List[LoadedMemory]:
        """Maximal Marginal Relevance re-ranking.

        MMR = λ · Sim(query, doc) - (1-λ) · max Sim(doc, selected)
        λ=0.7: 70% relevance, 30% diversity penalty.
        """
        candidates = candidates[:top_n]
        if len(candidates) <= 1:
            return candidates

        # Build token overlap similarity matrix
        query_tokens = set(_tokenise(query))
        doc_tokens: List[Set[str]] = []
        for m in candidates:
            doc_tokens.append(set(_tokenise(m.body)))

        def _sim(i: int, j: int) -> float:
            if i == j:
                return 1.0
            a = doc_tokens[i]
            b = doc_tokens[j]
            inter = len(a & b)
            union = len(a | b)
            return inter / union if union else 0.0

        def _query_sim(i: int) -> float:
            a = query_tokens
            b = doc_tokens[i]
            inter = len(a & b)
            union = len(a | b)
            return inter / union if union else 0.0

        selected: List[int] = []
        remaining = set(range(len(candidates)))

        # First: pick most relevant to query
        first = max(remaining, key=lambda i: _query_sim(i))
        selected.append(first)
        remaining.remove(first)

        while remaining and len(selected) < top_n:
            best_mmr_score = -1.0
            best_idx = -1
            for i in remaining:
                rel = _query_sim(i)
                div = max(_sim(i, j) for j in selected)
                mmr = lambda_param * rel - (1.0 - lambda_param) * div
                if mmr > best_mmr_score:
                    best_mmr_score = mmr
                    best_idx = i
            if best_idx < 0:
                break
            selected.append(best_idx)
            remaining.remove(best_idx)

        return [candidates[i] for i in selected]

    def _rrf_fusion(
        self,
        embed_results: Dict[str, float],
        bm25_results: Dict[str, float],
        active_map: Dict[str, LoadedMemory],
        k: int = 60,
    ) -> Dict[str, float]:
        """Reciprocal Rank Fusion — parameter-free hybrid retrieval.

        RRF score = Σ 1/(k + rank_i) across channels.
        k=60 is the academic consensus constant (Cormack et al., 2009).
        """
        # Build rank maps (rank 1 = best)
        embed_sorted = sorted(embed_results.items(), key=lambda x: x[1], reverse=True)
        bm25_sorted = sorted(bm25_results.items(), key=lambda x: x[1], reverse=True)

        embed_ranks = {mid: i + 1 for i, (mid, _) in enumerate(embed_sorted)}
        bm25_ranks = {mid: i + 1 for i, (mid, _) in enumerate(bm25_sorted)}

        all_ids = set(embed_results.keys()) | set(bm25_results.keys())
        scores: Dict[str, float] = {}
        for mid in all_ids:
            if mid not in active_map:
                continue
            score = 0.0
            er = embed_ranks.get(mid)
            if er is not None:
                score += 1.0 / (k + er)
            br = bm25_ranks.get(mid)
            if br is not None:
                score += 1.0 / (k + br)
            if score > 0:
                scores[mid] = score
        return scores

    def _weighted_fusion(
        self,
        embed_results: Dict[str, float],
        bm25_results: Dict[str, float],
        active_map: Dict[str, LoadedMemory],
        alpha: float,
        beta: float,
    ) -> Dict[str, float]:
        """Weighted fusion with per-channel min-max normalization."""
        pool: Dict[str, Dict[str, float]] = {}
        for mid, score in embed_results.items():
            if mid in active_map:
                pool.setdefault(mid, {})["cosine"] = score
        for mid, score in bm25_results.items():
            if mid in active_map:
                pool.setdefault(mid, {})["bm25"] = score

        for ch in ("cosine", "bm25"):
            vals = [v.get(ch, 0.0) for v in pool.values()]
            max_val = max(vals) if vals else 1.0
            if max_val > 0:
                for mid in pool:
                    pool[mid][ch] = pool[mid].get(ch, 0.0) / max_val

        for mid in pool:
            pool[mid].setdefault("cosine", 0.0)
            pool[mid].setdefault("bm25", 0.0)

        scores: Dict[str, float] = {}
        for mid, ch in pool.items():
            scores[mid] = alpha * ch["cosine"] + beta * ch["bm25"]
        return scores

    def _calc_supersedes_depth(self, mem_id: str) -> int:
        """Proxy to MemoryStore's canonical implementation."""
        return self.store._calc_supersedes_depth(mem_id)

    # -----------------------------------------------------------------------
    # Conflict detection — dual-path
    # -----------------------------------------------------------------------

    def check_conflict(
        self,
        body: str,
        threshold: Optional[float] = None,
        exclude_ids: Optional[List[str]] = None,
    ) -> Optional[Tuple[str, float]]:
        """Check for conflicting memories using dual-path detection.

        Path 1: Embedding cosine (semantic duplication, preferred)
        Path 2: BM25 sigmoid (keyword overlap, supplementary)

        Returns (memory_id, score) if conflict found, None otherwise.
        """
        tokens = _tokenise(body)
        if threshold is None:
            threshold = adaptive_conflict_threshold(body)
            if len(tokens) < 20:
                threshold = max(0.65, threshold - 0.05)

        active = self.store.list_active()
        if exclude_ids:
            exclude = set(exclude_ids)
            active = [m for m in active if m.id() not in exclude]

        # Path 1: Embedding
        exclude_set = set(exclude_ids) if exclude_ids else set()
        if self._ensure_embed_index():
            try:
                qvec = _embed_single(body)
                if qvec is not None and _HAS_NUMPY and isinstance(self._embed_array, np.ndarray):
                    qarr = np.array(qvec, dtype=np.float32)
                    scores = self._embed_array @ qarr
                    if exclude_set:
                        for i, mid in enumerate(self._embed_ids):
                            if mid in exclude_set:
                                scores[i] = -1.0
                    best_idx = int(np.argmax(scores))
                    best_score = float(scores[best_idx])
                    if best_score > threshold:
                        return (self._embed_ids[best_idx], best_score)
                elif qvec is not None:
                    best_score = 0.0
                    best_id = None
                    for mid, vec in zip(self._embed_ids, self._embed_array):
                        if mid in exclude_set:
                            continue
                        sim = _cosine_sim(qvec, vec)
                        if sim > best_score:
                            best_score = sim
                            best_id = mid
                    if best_score > threshold and best_id:
                        return (best_id, best_score)
            except Exception:
                pass

        # Path 2: BM25
        scored = _bm25_search_scored(active, body, 1, query_tokens=tokens)
        if scored:
            m, raw_score = scored[0]
            normalized = normalize_bm25(raw_score)
            if normalized > threshold:
                return (m.id(), normalized)
        return None
