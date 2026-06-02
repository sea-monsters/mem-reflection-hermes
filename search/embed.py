"""search/embed.py — ONNX embedding engine for mem-reflection-hermes.

Lazy-loaded embedding infrastructure: ONNX Runtime model session, LRU cache,
intent classification (zero-shot + keyword fallback), batch encoding.

Zero-dependency leaf module (only imports from .core, not from __init__).
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core import hermes_home as _hermes_home, _tokenise

logger = logging.getLogger(__name__)

__all__ = [
    # Module state
    "_onnx_session", "_onnx_tokenizer", "_embed_model_lock",
    "_embed_cache", "_embed_cache_lock", "_EMBED_CACHE_MAX",
    "_INTENT_PROTOTYPE_EMBEDDINGS", "_INTENT_PROTOTYPE_LOCK",
    # Cache helpers
    "_embed_cache_key", "_get_cached_embed", "_put_cached_embed", "_set_cached_embed",
    # Intent classification
    "_intent_prototypes", "_ensure_intent_prototypes", "_classify_intent",
    "_is_explicit_memory_intent_kw", "_is_correction_kw", "_is_procedure_kw",
    "_is_explicit_memory_intent", "_is_correction", "_is_procedure",
    # ONNX / Sentence-Transformers
    "_get_onnx_session", "_get_st_model",
    # Encoding
    "_embed_texts", "_embed_single", "_cosine_sim", "_extract_keywords",
]


# ---------------------------------------------------------------------------
# Embedding engine (ONNX Runtime — fast, lightweight)
# ---------------------------------------------------------------------------
# Lazy-loaded ONNX embedding components
# ---------------------------------------------------------------------------

_onnx_session: Optional[Any] = None
_onnx_tokenizer: Optional[Any] = None
_embed_model_lock = threading.Lock()

# LRU cache for embeddings: text_hash -> vector (max 500 entries, O(1) eviction)
_embed_cache: "OrderedDict[str, Any]" = OrderedDict()
_embed_cache_lock = threading.Lock()
_EMBED_CACHE_MAX = 500


def _embed_cache_key(text: str) -> str:
    """Hash text for cache key."""
    import hashlib
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _get_cached_embed(text: str) -> Optional[Any]:
    """Get cached embedding if available (LRU: refreshes access order)."""
    key = _embed_cache_key(text)
    with _embed_cache_lock:
        if key in _embed_cache:
            _embed_cache.move_to_end(key)  # O(1) LRU refresh
            return _embed_cache[key]
        return None


def _put_cached_embed(text: str, vector: Any) -> None:
    """Store embedding in LRU cache. Evicts oldest if at capacity."""
    key = _embed_cache_key(text)
    with _embed_cache_lock:
        _embed_cache[key] = vector
        _embed_cache.move_to_end(key)
        if len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)  # O(1) evict oldest


def _intent_prototypes() -> Dict[str, List[str]]:
    """Semantic intent prototypes for embedding-based zero-shot classification.

    Each intent keeps multiple prototype phrasings so the classifier can pick
    the strongest semantic match across languages and styles. Users can
    override any intent with either a single string or a list of strings in
    config.yaml.
    """
    defaults: Dict[str, List[str]] = {
        "memory": [
            "This is something important I want to remember about the user's preferences and habits.",
            "这是我需要记住的重要信息，关于用户的偏好、习惯或长期事实。",
        ],
        "correction": [
            "That was wrong, let me correct what I said previously.",
            "刚才那句话不对，我来更正前面的说法。",
        ],
        "procedure": [
            "Here is a step-by-step workflow or process I need to follow.",
            "这里是需要遵循的步骤、流程或操作说明。",
        ],
    }

    try:
        from ..core import plugin_config, CONFIG_KEY_INTENT_PROTOTYPES
        cfg = plugin_config()
        custom = cfg.get(CONFIG_KEY_INTENT_PROTOTYPES)
        if isinstance(custom, dict) and custom:
            merged: Dict[str, List[str]] = {}
            for key, default_values in defaults.items():
                value = custom.get(key, default_values)
                if isinstance(value, str):
                    values = [value.strip()] if value.strip() else list(default_values)
                elif isinstance(value, (list, tuple)):
                    values = [str(v).strip() for v in value if str(v).strip()]
                    if not values:
                        values = list(default_values)
                else:
                    values = list(default_values)
                merged[key] = values
            return merged
    except Exception:
        pass

    return defaults


_INTENT_PROTOTYPE_EMBEDDINGS: Optional[Dict[str, List[Any]]] = None
_INTENT_PROTOTYPE_SIGNATURE: Optional[Tuple[Tuple[str, Tuple[str, ...]], ...]] = None
_INTENT_PROTOTYPE_LOCK = threading.Lock()


def _ensure_intent_prototypes() -> Optional[Dict[str, List[Any]]]:
    """Lazy-load intent prototype embeddings for zero-shot classification."""
    global _INTENT_PROTOTYPE_EMBEDDINGS, _INTENT_PROTOTYPE_SIGNATURE
    prototypes = _intent_prototypes()
    signature = tuple((intent, tuple(texts)) for intent, texts in sorted(prototypes.items()))
    if _INTENT_PROTOTYPE_EMBEDDINGS is not None and _INTENT_PROTOTYPE_SIGNATURE == signature:
        return _INTENT_PROTOTYPE_EMBEDDINGS
    with _INTENT_PROTOTYPE_LOCK:
        if _INTENT_PROTOTYPE_EMBEDDINGS is not None and _INTENT_PROTOTYPE_SIGNATURE == signature:
            return _INTENT_PROTOTYPE_EMBEDDINGS
        try:
            embs: Dict[str, List[Any]] = {}
            for intent, texts in prototypes.items():
                vecs = _embed_texts(texts)
                if vecs is None:
                    continue
                valid_vecs = [vec for vec in vecs if vec is not None]
                if valid_vecs:
                    embs[intent] = valid_vecs
            if embs:
                _INTENT_PROTOTYPE_EMBEDDINGS = embs
                _INTENT_PROTOTYPE_SIGNATURE = signature
                return _INTENT_PROTOTYPE_EMBEDDINGS
        except Exception:
            pass
        return None


# P2-6: counters to track keyword vs embedding classification ratio
_classify_intent_stats = {"embedding": 0, "keyword": 0, "fallback_none": 0}


def _classify_intent(text: str) -> str:
    """Classify user turn intent using embedding zero-shot classification.
    
    Returns one of: "memory", "correction", "procedure", "none"
    
    Uses semantic similarity with intent prototypes.
    Falls back to keyword-based detection (old methods) when embeddings unavailable.
    """
    global _classify_intent_stats
    if not text or len(text) < 5:
        _classify_intent_stats["fallback_none"] += 1
        return "none"
    
    # Try embedding-based classification first
    try:
        proto_embs = _ensure_intent_prototypes()
        if proto_embs is not None:
            import numpy as np
            text_emb = _embed_single(text)
            if text_emb is not None:
                text_norm = text_emb / (np.linalg.norm(text_emb) + 1e-8)
                best_intent = "none"
                best_score = 0.30  # minimum threshold
                for intent, proto_vecs in proto_embs.items():
                    for proto_vec in proto_vecs:
                        proto_norm = proto_vec / (np.linalg.norm(proto_vec) + 1e-8)
                        sim = float(np.dot(text_norm, proto_norm))
                        if sim > best_score:
                            best_score = sim
                            best_intent = intent
                if best_intent != "none":
                    _classify_intent_stats["embedding"] += 1
                    return best_intent
    except Exception:
        pass
    
    # Fallback to keyword-based detection
    if _is_explicit_memory_intent_kw(text):
        _classify_intent_stats["keyword"] += 1
        return "memory"
    if _is_correction_kw(text):
        _classify_intent_stats["keyword"] += 1
        return "correction"
    if _is_procedure_kw(text):
        _classify_intent_stats["keyword"] += 1
        return "procedure"
    _classify_intent_stats["fallback_none"] += 1
    return "none"


def _is_explicit_memory_intent_kw(text: str) -> bool:
    """Keyword-based memory intent detection (fallback)."""
    lower = text.lower()
    markers = [
        "remember", "prefer", "always", "important", "note",
        "remind", "keep in mind", "save this",
        "我的", "我喜欢", "我讨厌",
    ]
    return any(m in lower for m in markers)


def _is_correction_kw(text: str) -> bool:
    """Keyword-based correction detection (fallback)."""
    lower = text.lower()
    markers = [
        "actually", "wrong", "correct", "instead",
        "不对", "错了", "更正", "纠正",
        "no,", "nope", "incorrect",
    ]
    return any(m in lower for m in markers)


def _is_procedure_kw(text: str) -> bool:
    """Keyword-based procedure detection (fallback)."""
    lower = text.lower()
    markers = [
        "how to", "steps", "workflow", "process", "procedure",
        "configure", "setup", "安装", "配置", "步骤", "流程",
        "way to", "method of", "always use", "never use", "make sure",
    ]
    return any(m in lower for m in markers)


def _set_cached_embed(text: str, vec: Any) -> None:
    """Cache embedding with LRU eviction."""
    key = _embed_cache_key(text)
    with _embed_cache_lock:
        if len(_embed_cache) >= _EMBED_CACHE_MAX:
            # Simple eviction: clear half the cache
            items = list(_embed_cache.items())
            _embed_cache.clear()
            _embed_cache.update(items[_EMBED_CACHE_MAX // 2:])
        _embed_cache[key] = vec


def _get_onnx_session() -> Tuple[Optional[Any], Optional[Any]]:
    """Lazy-load ONNX Runtime session and tokenizer.

    Uses all-MiniLM-L6-v2 in ONNX format for minimal memory footprint
    and fast inference.

    Model resolution priority:
    1. SRH_MODEL_DIR environment variable
    2. ~/.hermes/models/all-MiniLM-L6-v2-onnx/
    3. sentence-transformers fallback (auto-download)
    """
    global _onnx_session, _onnx_tokenizer
    if _onnx_session is not None and _onnx_tokenizer is not None:
        return _onnx_session, _onnx_tokenizer

    with _embed_model_lock:
        if _onnx_session is not None and _onnx_tokenizer is not None:
            return _onnx_session, _onnx_tokenizer

        # Resolve model directory
        env_model_dir = os.environ.get("SRH_MODEL_DIR")
        if env_model_dir:
            model_dir = Path(env_model_dir)
        else:
            model_dir = _hermes_home() / "models" / "all-MiniLM-L6-v2-onnx"
        model_path = model_dir / "model.onnx"

        # Fallback: try sentence-transformers if ONNX model not available
        if not model_path.exists():
            logger.warning("ONNX model not found at %s, falling back to sentence-transformers", model_path)
            return _get_st_model()

        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            _onnx_session = ort.InferenceSession(
                str(model_path),
                providers=["CPUExecutionProvider"],
            )
            _onnx_tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
            _onnx_tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
            _onnx_tokenizer.enable_truncation(max_length=512)
            logger.info("Loaded ONNX embedding model from %s (lightweight tokenizer)", model_dir)
            return _onnx_session, _onnx_tokenizer
        except Exception as e:
            logger.warning("Failed to load ONNX model: %s", e)
            return _get_st_model()


def _get_st_model() -> Tuple[Optional[Any], Optional[Any]]:
    """Fallback to sentence-transformers if ONNX unavailable."""
    global _onnx_session, _onnx_tokenizer
    try:
        from sentence_transformers import SentenceTransformer
        _onnx_session = SentenceTransformer("all-MiniLM-L6-v2")
        _onnx_tokenizer = None  # ST has built-in tokenization
        logger.info("Loaded sentence-transformers fallback model")
        return _onnx_session, _onnx_tokenizer
    except Exception as e:
        logger.warning("Failed to load fallback embedding model: %s", e)
        return None, None


def _embed_texts(texts: List[str]) -> Optional[Any]:
    """Encode a list of texts into normalized embedding vectors."""
    if not texts:
        return None

    # Check cache for all texts
    cached_results = []
    uncached_texts = []
    uncached_indices = []
    for i, text in enumerate(texts):
        cached = _get_cached_embed(text)
        if cached is not None:
            cached_results.append((i, cached))
        else:
            uncached_texts.append(text)
            uncached_indices.append(i)

    # If all cached, return directly
    if not uncached_texts:
        return [vec for _, vec in sorted(cached_results, key=lambda x: x[0])]

    # Encode uncached texts
    session, tokenizer = _get_onnx_session()
    if session is None:
        return None

    embeddings = None

    # sentence-transformers fallback path
    if tokenizer is None and hasattr(session, "encode"):
        try:
            import numpy as np
            embeddings = session.encode(uncached_texts, convert_to_numpy=True, normalize_embeddings=True)
        except Exception as e:
            logger.debug("ST encoding failed: %s", e)
            return None
    else:
        # ONNX Runtime + tokenizers path
        try:
            import numpy as np

            # Tokenize
            encodings = tokenizer.encode_batch(uncached_texts)
            max_len = max(len(e.ids) for e in encodings)

            input_ids = np.array(
                [e.ids + [0] * (max_len - len(e.ids)) for e in encodings],
                dtype=np.int64,
            )
            attention_mask = np.array(
                [e.attention_mask + [0] * (max_len - len(e.attention_mask)) for e in encodings],
                dtype=np.int64,
            )
            token_type_ids = np.zeros_like(input_ids)

            # Run inference
            outputs = session.run(
                None,
                {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "token_type_ids": token_type_ids,
                },
            )
            last_hidden_state = outputs[0]  # (batch, seq_len, hidden_dim)

            # Mean pooling with attention mask
            mask_expanded = np.expand_dims(attention_mask, -1).astype(np.float32)
            sum_embeddings = np.sum(last_hidden_state * mask_expanded, axis=1)
            sum_mask = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
            embeddings = sum_embeddings / sum_mask

            # L2 normalize
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / norms

        except Exception as e:
            logger.debug("ONNX encoding failed: %s", e)
            return None

    # Cache new embeddings
    for text, vec in zip(uncached_texts, embeddings):
        _set_cached_embed(text, vec)

    # Merge cached + new results
    all_results = cached_results + list(zip(uncached_indices, embeddings))
    all_results.sort(key=lambda x: x[0])
    return [vec for _, vec in all_results]


def _embed_single(text: str) -> Optional[Any]:
    """Encode a single text into an embedding vector."""
    embs = _embed_texts([text])
    if embs is not None:
        return embs[0]
    return None


def _cosine_sim(a, b) -> float:
    """Cosine similarity between two normalized vectors.
    Includes dimension safety check (P1): if dimensions mismatch, logs
    a warning and returns 0.0 instead of crashing or silently truncating.
    """
    if len(a) != len(b):
        logger.warning("Cosine sim dimension mismatch: %d vs %d", len(a), len(b))
        return 0.0
    try:
        import numpy as np
        return float(np.dot(a, b))
    except Exception:
        return 0.0


def _extract_keywords(text: str, top_k: int = 5) -> List[str]:
    """Extract distinctive keywords from text using TF-IDF-like heuristics."""
    tokens = _tokenise(text)
    if not tokens:
        return []
    # Filter out very common stopwords
    stops = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "shall", "can", "need", "dare",
        "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
        "from", "as", "into", "through", "during", "before", "after",
        "above", "below", "between", "under", "and", "but", "or", "yet",
        "so", "if", "because", "although", "though", "while", "where",
        "when", "that", "which", "who", "whom", "whose", "what", "this",
        "these", "those", "i", "you", "he", "she", "it", "we", "they",
        "me", "him", "her", "us", "them", "my", "your", "his", "its",
        "our", "their", "mine", "yours", "hers", "ours", "theirs",
        "myself", "yourself", "himself", "herself", "itself", "ourselves",
        "themselves", "what", "which", "who", "whom", "this", "that",
        "these", "those", "am", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "shall", "should", "may", "might", "can", "could",
        "must", "ought", "need", "dare", "used", "here", "there",
        "now", "then", "today", "tomorrow", "yesterday", "just", "only",
        "also", "even", "back", "after", "again", "further", "once",
        "about", "up", "out", "down", "off", "over", "under", "again",
    }
    # Count and score by rarity (rarer = higher score)
    tf = Counter(tokens)
    scored = []
    for t, c in tf.items():
        if t in stops or len(t) < 3:
            continue
        # Prefer longer, less frequent tokens
        score = c * len(t) / (1 + sum(1 for x in tokens if x == t))
        scored.append((score, t))
    scored.sort(reverse=True)
    seen = set()
    out = []
    for _, t in scored:
        if t not in seen:
            seen.add(t)
            out.append(t)
            if len(out) >= top_k:
                break
    return out


def _is_explicit_memory_intent(text: str) -> bool:
    """Detect if user wants to remember something — uses embedding zero-shot + keyword fallback."""
    return _classify_intent(text) == "memory"


def _is_correction(text: str) -> bool:
    """Detect if user is correcting — uses embedding zero-shot + keyword fallback."""
    return _classify_intent(text) == "correction"


def _is_procedure(text: str) -> bool:
    """Detect if user is describing a procedure — uses embedding zero-shot + keyword fallback."""
    return _classify_intent(text) == "procedure"

