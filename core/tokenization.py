"""Token estimation and CJK-aware tokenization for mem-reflection-hermes.

This module owns tiktoken-based estimation, CJK detection, stopword filtering,
and the tokenizer used by BM25 indexing. It depends on core.config for the
search config subtree and core.utils for zone-related constants only when
needed by callers (not internally).
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter
from typing import TYPE_CHECKING, List, Set

from .config import plugin_config

if TYPE_CHECKING:
    from .models import LoadedMemory


# ---------------------------------------------------------------------------
# Token estimation (tiktoken)
# ---------------------------------------------------------------------------
_tiktoken_enc = None
_tiktoken_lock = threading.Lock()


def _get_tiktoken_encoding():
    """Lazy-initialise the tiktoken encoder (thread-safe)."""
    global _tiktoken_enc
    if _tiktoken_enc is not None:
        return _tiktoken_enc
    with _tiktoken_lock:
        if _tiktoken_enc is not None:
            return _tiktoken_enc
        try:
            import tiktoken
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _tiktoken_enc = None
        return _tiktoken_enc


def estimate_tokens(text: str) -> int:
    """Estimate the token count for *text*, CJK-aware fallback."""
    enc = _get_tiktoken_encoding()
    if enc:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text.encode("utf-8")) // 3


# ---------------------------------------------------------------------------
# CJK detection & adaptive threshold
# ---------------------------------------------------------------------------
_CJK_RANGES = [
    (0x4E00, 0x9FFF),
    (0x3400, 0x4DBF),
    (0x3000, 0x303F),
    (0x3040, 0x309F),
    (0x30A0, 0x30FF),
    (0xAC00, 0xD7AF),
]


def is_cjk(c: str) -> bool:
    """Return True if *c* is a CJK character."""
    cp = ord(c)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def cjk_ratio(text: str) -> float:
    """Return fraction of alphabet-like chars that are CJK in text."""
    letter_count = 0
    cjk_count = 0
    for c in text:
        if c.isalpha():
            letter_count += 1
            if is_cjk(c):
                cjk_count += 1
    if letter_count == 0:
        return 0.0
    return cjk_count / letter_count


def adaptive_conflict_threshold(body: str) -> float:
    """Return conflict threshold based on CJK ratio.

    CJK-dominant (>40%) -> 0.75  |  Mixed (10-40%) -> 0.80  |  Latin -> 0.85

    Note: These thresholds are intentionally higher (more permissive) than
    early beta values (0.55/0.65) to reduce false-positive conflict matches
    in diverse content types. The 0.75/0.80/0.85 triage has been validated
    across CJK mixed, Latin-heavy, and code-dominant corpora.
    """
    ratio = cjk_ratio(body)
    if ratio > 0.40:
        return 0.75
    elif ratio > 0.10:
        return 0.80
    return 0.85


# ---------------------------------------------------------------------------
# Tokenization & stopwords
# ---------------------------------------------------------------------------
_MIN_TOKEN_LEN = 2

_TOKEN_RE = re.compile(
    r"[^a-z0-9一-鿿㐀-䶿぀-ゟ゠-ヿ가-힯]+"
)

_STOPWORDS: Set[str] = set()
try:
    from nltk.corpus import stopwords
    _STOPWORDS = set(stopwords.words('english'))
except Exception:
    _STOPWORDS = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "shall",
        "should", "may", "might", "can", "could", "must", "ought", "need",
        "dare", "used", "here", "there", "now", "then", "today", "tomorrow",
        "yesterday", "just", "only", "also", "even", "back", "after", "again",
        "further", "once", "about", "up", "out", "down", "off", "over",
        "under", "again", "this", "that", "these", "those", "it", "its",
        "and", "or", "but", "not", "no", "nor", "so", "yet", "for",
        "with", "without", "to", "from", "in", "on", "at", "by",
        "into", "through", "during", "before", "after", "above", "below",
        "between", "of", "per", "via", "as", "than", "then", "if",
        "while", "because", "since", "until", "i", "you", "he", "she",
        "we", "they", "me", "him", "her", "us", "them",
    }

_CJK_STOPWORDS: Set[str] = {
    # Chinese
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "那",
    "什么", "怎么", "为什么", "如何", "可以", "一下", "一些",
    "与", "及", "等", "对", "将", "还", "但", "而", "或",
    "如果", "因为", "所以", "虽然", "但是", "然而", "而且",
    "使用", "进行", "通过", "根据", "关于", "需要", "应该",
    # Japanese
    "の", "に", "は", "を", "た", "が", "で", "て", "と", "し",
    "な", "から", "れる", "られる", "よう", "もの", "こと",
    "これ", "それ", "あれ", "どれ", "この", "その", "あの",
    # Korean
    "은", "는", "이", "가", "을", "를", "에", "의", "로", "과",
    "와", "한", "하", "고", "도", "지", "다", "니다", "세요",
}


# ---------------------------------------------------------------------------
# CJK tokenizer mode
# ---------------------------------------------------------------------------
_JIEBA_SEARCH = None
_JIEBA_AVAILABLE: bool | None = None


def _search_config() -> dict:
    """Return the search configuration subtree."""
    cfg = plugin_config().get("search", {})
    return cfg if isinstance(cfg, dict) else {}


def _store_hook(name: str):
    """Return a monkeypatched attribute from core.store if present and distinct."""
    try:
        import sys

        store_mod = sys.modules.get("core.store")
        if store_mod is None:
            return None
        val = getattr(store_mod, name, None)
        local = globals().get(name)
        if val is not None and val is not local:
            return val
    except Exception:
        pass
    return None


def cjk_tokenizer_mode() -> str:
    """Return normalized CJK tokenizer mode: auto, bigram, or jieba."""
    hook = _store_hook("cjk_tokenizer_mode")
    if hook is not None:
        raw = str(hook()).strip().lower()
    else:
        raw = str(_search_config().get("cjk_tokenizer", "auto")).strip().lower()
    if raw in {"auto", "bigram", "jieba"}:
        return raw
    return "auto"


def _get_jieba_search():
    """Return jieba.cut_for_search if available, else None."""
    hook = _store_hook("_get_jieba_search")
    if hook is not None:
        return hook()
    global _JIEBA_SEARCH, _JIEBA_AVAILABLE
    if _JIEBA_AVAILABLE is False:
        return None
    if _JIEBA_SEARCH is not None:
        return _JIEBA_SEARCH
    try:
        import jieba  # type: ignore
        _JIEBA_SEARCH = jieba.cut_for_search
        _JIEBA_AVAILABLE = True
        return _JIEBA_SEARCH
    except Exception:
        _JIEBA_AVAILABLE = False
        return None


def _tokenise_cjk_bigram(part: str) -> List[str]:
    """Tokenize a CJK-bearing fragment via non-overlapping bigrams."""
    tokens: List[str] = []
    i = 0
    while i < len(part) - 1:
        bigram = part[i:i + 2]
        if all(is_cjk(c) for c in bigram):
            tokens.append(bigram)
            i += 2
        else:
            i += 1
    non_cjk = "".join(c for c in part if not is_cjk(c))
    if len(non_cjk) >= _MIN_TOKEN_LEN and non_cjk not in _STOPWORDS:
        tokens.append(non_cjk)
    return [t for t in tokens if t not in _CJK_STOPWORDS]


def _tokenise_cjk_jieba(part: str) -> List[str]:
    """Tokenize a CJK-bearing fragment via jieba search mode."""
    # Prefer a monkeypatchable hook in core.store if present (tests patch there).
    cut_for_search = None
    try:
        import sys
        store_mod = sys.modules.get("core.store")
        if store_mod is not None:
            cut_for_search = store_mod._get_jieba_search()
    except Exception:
        pass
    if cut_for_search is None:
        cut_for_search = _get_jieba_search()
    if cut_for_search is None:
        return _tokenise_cjk_bigram(part)
    tokens: List[str] = []
    for token in cut_for_search(part):
        piece = token.strip().lower()
        if not piece:
            continue
        if any(is_cjk(c) for c in piece):
            if piece not in _CJK_STOPWORDS:
                tokens.append(piece)
            continue
        for sub in _TOKEN_RE.split(piece):
            if len(sub) >= _MIN_TOKEN_LEN and sub not in _STOPWORDS:
                tokens.append(sub)
    return tokens


def _tokenise_cjk_part(part: str) -> List[str]:
    """Tokenize a CJK-bearing fragment using the configured strategy."""
    mode = cjk_tokenizer_mode()
    if mode == "bigram":
        return _tokenise_cjk_bigram(part)
    if mode == "jieba":
        return _tokenise_cjk_jieba(part)
    if _get_jieba_search() is not None:
        return _tokenise_cjk_jieba(part)
    return _tokenise_cjk_bigram(part)


def _tokenise(s: str) -> List[str]:
    """Tokenize text for BM25 search.

    Preserves CJK bigrams, alphanumeric tokens, filters stopwords and
    short tokens.
    """
    lower = s.lower()
    tokens: List[str] = []
    for part in _TOKEN_RE.split(lower):
        if not part:
            continue
        has_cjk_flag = any(is_cjk(c) for c in part)
        if has_cjk_flag:
            tokens.extend(_tokenise_cjk_part(part))
        else:
            if len(part) >= _MIN_TOKEN_LEN and part not in _STOPWORDS:
                tokens.append(part)
    return tokens


def _memory_tokens(memory: "LoadedMemory") -> Counter:
    """Tokenize a memory's body + tags for BM25 indexing."""
    return _tokenise(memory.body + " " + " ".join(memory.frontmatter.tags or []))


def normalize_bm25(raw_score: float) -> float:
    """Normalize a BM25 raw score to [0, 1) range."""
    return raw_score / (raw_score + 1.0)


def _bm25_search_scored(
    memories: List["LoadedMemory"],
    query: str,
    k: int = 5,
    effectiveness: Optional[Dict[str, Any]] = None,
    doc_tokens: Optional[List[Tuple[str, Counter]]] = None,
    query_tokens: Optional[List[str]] = None,
) -> List[Tuple["LoadedMemory", float]]:
    k1, b = 1.5, 0.75
    if k == 0 or not memories:
        return []
    q_tokens = query_tokens if query_tokens is not None else _tokenise(query)
    if not q_tokens:
        return []
    n = len(memories)
    df: Dict[str, int] = Counter()
    doc_lens: List[int] = []
    if doc_tokens is not None:
        raw_doc_tokens = [tokens for _, tokens in doc_tokens]
    else:
        raw_doc_tokens = [_memory_tokens(m) for m in memories]
    for tokens in raw_doc_tokens:
        doc_lens.append(len(tokens))
        for t in set(tokens):
            df[t] += 1
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
    scored: List[Tuple[float, "LoadedMemory"]] = []
    for i, (tokens, m) in enumerate(zip(raw_doc_tokens, memories)):
        doc_len = doc_lens[i]
        m_tf = Counter(tokens)
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
                if getattr(eff, "factor", None) and getattr(eff, "decay_factor", None):
                    score *= eff.factor() * eff.decay_factor()
            scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(m, s) for s, m in scored[:k]]


def _bm25_search(
    memories: List["LoadedMemory"],
    query: str,
    k: int,
    effectiveness: Optional[Dict[str, Any]] = None,
    doc_tokens: Optional[List[Tuple[str, List[str]]]] = None,
) -> List["LoadedMemory"]:
    scored = _bm25_search_scored(memories, query, k, effectiveness, doc_tokens)
    return [m for m, _ in scored]


def _cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    intersection = set(a) & set(b)
    if not intersection:
        return 0.0
    dot = sum(a[k] * b[k] for k in intersection)
    norm_a = sum(v * v for v in a.values()) ** 0.5
    norm_b = sum(v * v for v in b.values()) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
