"""core.py — Models, constants, and utility functions for mem-reflection-hermes.

Zero-dependency leaf module (no imports from other mem-reflection modules).
All dataclasses, configuration constants, path helpers, frontmatter I/O,
async queues, and zone utilities live here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config & paths (with caching)
# ---------------------------------------------------------------------------

_cached_config: Optional[Dict[str, Any]] = None
_cached_config_mtime: float = 0.0

# Configuration keys (centralized for easy customization)
CONFIG_SECTION = "mem_reflection_hermes"
CONFIG_KEY_EMBEDDINGS = "embeddings"
CONFIG_KEY_MICRO_REFLECTION = "micro_reflection"
CONFIG_KEY_REFLECTION_MODE = "reflection_mode"
CONFIG_KEY_PALACE_MODE = "palace_mode"
CONFIG_KEY_PROFILE_MODE = "profile_mode"
CONFIG_KEY_PALACE_INSTRUCTIONS = "palace_instructions"
CONFIG_KEY_ACTIVE_MEMORY_CAP = "active_memory_index_cap"
CONFIG_KEY_SKILL_INDEX_CAP = "skill_index_cap"
CONFIG_KEY_RELEVANT_MEMORY_CAP = "relevant_memory_cap"
CONFIG_KEY_TRIGGERED_SKILL_CAP = "triggered_skill_cap"
CONFIG_KEY_INTENT_PROTOTYPES = "intent_prototypes"  # P2-4: user-configurable intent prototypes


def hermes_home() -> Path:
    """Resolve Hermes home directory."""
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        return Path(env_home)
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except Exception:
        return Path.home() / ".hermes"


def load_config() -> Dict[str, Any]:
    """Load Hermes config.yaml with caching (reloads if file changed)."""
    global _cached_config, _cached_config_mtime
    cfg_path = hermes_home() / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        mtime = cfg_path.stat().st_mtime
        if _cached_config is not None and mtime == _cached_config_mtime:
            return _cached_config
        import yaml  # type: ignore
        with open(cfg_path, "r", encoding="utf-8") as f:
            _cached_config = yaml.safe_load(f) or {}
        _cached_config_mtime = mtime
        return _cached_config
    except Exception:
        return {}


def plugin_config() -> Dict[str, Any]:
    """Get the plugin-specific configuration section."""
    cfg = load_config()
    return cfg.get("plugins", {}).get(CONFIG_SECTION, {})


def plugin_data_dir() -> Path:
    """Path to plugin data directory (~/.hermes/memory/)."""
    d = hermes_home() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_memories_dir() -> Path:
    """User-level memories directory."""
    d = hermes_home() / "memories"
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_memories_dir() -> Optional[Path]:
    """Project-level memories directory (optional, CWD-based)."""
    p = Path.cwd() / ".hermes" / "memories"
    return p if p.exists() else None


def user_skills_dir() -> Path:
    """User-level skills directory."""
    d = hermes_home() / "memory" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_skills_dir() -> Optional[Path]:
    """Project-level skills directory (optional)."""
    p = Path.cwd() / ".hermes" / "memory" / "skills"
    return p if p.exists() else None


def _plugin_data_dir_legacy() -> Path:
    """Legacy plugin data directory (kept for backward compat)."""
    d = hermes_home() / "plugins" / "mem-reflection-hermes"
    return d


def embeddings_enabled() -> bool:
    return plugin_config().get(CONFIG_KEY_EMBEDDINGS, True)


def micro_reflection_enabled() -> bool:
    return bool(plugin_config().get(CONFIG_KEY_MICRO_REFLECTION, False))


def palace_mode_enabled() -> bool:
    return bool(plugin_config().get(CONFIG_KEY_PALACE_MODE, True))


def profile_mode_enabled() -> bool:
    return bool(plugin_config().get(CONFIG_KEY_PROFILE_MODE, False))


# ---------------------------------------------------------------------------
# Zone constants
# ---------------------------------------------------------------------------

_ZONE_CORE = "core"
_ZONE_WORK = "work"
_ZONE_EPISODE = "episode"
_ZONE_GENERAL = "general"
_VALID_ZONES = {_ZONE_CORE, _ZONE_WORK, _ZONE_EPISODE, _ZONE_GENERAL}
_PROJECT_ZONE_PREFIX = "project:"
_ZONE_SPLIT_THRESHOLD = 20
_ZONE_MERGE_THRESHOLD = 3


def normalize_zone(zone: Optional[str]) -> str:
    """Normalize a zone string to a valid zone."""
    if not zone:
        return _ZONE_GENERAL
    zone = zone.strip().lower()
    if zone in _VALID_ZONES or zone.startswith(_PROJECT_ZONE_PREFIX):
        return zone
    return _ZONE_GENERAL


def is_valid_zone(zone: str) -> bool:
    return zone in _VALID_ZONES or zone.startswith(_PROJECT_ZONE_PREFIX)


# ---------------------------------------------------------------------------
# Palace Index helpers
# ---------------------------------------------------------------------------

def palace_index_path() -> Path:
    """Path to palace-index.md cache file."""
    return _plugin_data_dir_legacy() / "palace-index.md"


def zone_cache_dir() -> Path:
    """Path to zone-cache directory for per-zone summaries."""
    d = _plugin_data_dir_legacy() / "zone-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sanitize_zone_filename(zone: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", zone)


def fast_hash(text: str) -> str:
    """Fast non-crypto hash for write-on-change comparison."""
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class MemoryFrontmatter:
    id: str
    created: str  # ISO-8601
    source: str  # reflection | user | imported
    confidence: str  # low | medium | high
    pinned: bool = False
    tags: List[str] = field(default_factory=list)
    supersedes: List[str] = field(default_factory=list)
    zone: str = "general"
    rank: int = 0


@dataclass
class LoadedMemory:
    frontmatter: MemoryFrontmatter
    body: str
    source_path: Path
    scope: str  # "user" | "project"

    def id(self) -> str:
        return self.frontmatter.id


@dataclass
class MemoryStatEntry:
    memory_id: str
    event: str  # "loaded" | "referenced" | "accessed"
    at: str  # ISO-8601 timestamp


@dataclass
class MemoryEffectiveness:
    """Per-memory effectiveness summary — computed from stats.jsonl."""
    loaded: int = 0
    referenced: int = 0
    accessed: int = 0
    last_event_at: Optional[str] = None

    def factor(self) -> float:
        """Effectiveness factor in [0.5, 1.0]."""
        if self.loaded == 0:
            return 1.0
        ratio = self.referenced / self.loaded
        return 0.5 + 0.5 * ratio

    def decay_factor(self, now: Optional[datetime] = None) -> float:
        """Exponential decay with 30-day half-life, floor 0.3."""
        if self.last_event_at is None:
            return 1.0
        now_dt = now or datetime.now(timezone.utc)
        try:
            last_dt = datetime.fromisoformat(self.last_event_at)
            days = max(0, (now_dt - last_dt).days)
            return max(0.3, 0.5 ** (days / 30.0))
        except Exception:
            return 1.0


@dataclass
class SkillFrontmatter:
    name: str
    description: str
    triggers: List[str] = field(default_factory=list)
    version: Optional[str] = None
    license: Optional[str] = None
    always_active: bool = False


@dataclass
class LoadedSkill:
    frontmatter: SkillFrontmatter
    body: str
    source_path: Path
    scope: str


# ---------------------------------------------------------------------------
# Frontmatter IO
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from a memory file.

    Note (P2-3): Uses manual re.search instead of pyyaml to avoid the
    dependency. Only supports simple key-value pairs and lists. Does NOT
    support multi-line strings, nested lists, or special YAML escapes.

    Uses msgspec for fast YAML parsing if available (8x faster than PyYAML),
    with fallback to PyYAML for edge cases.
    """
    s = text.strip()
    if s.startswith("\ufeff"):
        s = s[1:]
    if not s.startswith("---"):
        return {}, text
    after_open = s[3:].lstrip("-\n")
    close_idx = after_open.find("\n---")
    if close_idx == -1:
        return {}, raw
    yaml_part = after_open[:close_idx]
    body_part = after_open[close_idx + 4:].lstrip("-\n")
    try:
        import msgspec
        class _FrontmatterStruct(msgspec.Struct):
            id: str = ""
            created: str = ""
            source: str = "conversation"
            confidence: str = "medium"
            pinned: bool = False
            tags: List[str] = []
            supersedes: List[str] = []
            zone: str = "general"
            always_active: bool = False
            rank: int = 0
        decoded = msgspec.yaml.decode(yaml_part, type=_FrontmatterStruct)
        data: Dict[str, Any] = {
            "id": decoded.id,
            "created": decoded.created,
            "source": decoded.source,
            "confidence": decoded.confidence,
            "pinned": decoded.pinned,
            "tags": decoded.tags,
            "supersedes": decoded.supersedes,
            "zone": decoded.zone,
            "always_active": decoded.always_active,
            "rank": decoded.rank,
        }
        return data, body_part.strip()
    except Exception:
        pass
    try:
        import yaml
        data = yaml.safe_load(yaml_part) or {}
        if not isinstance(data, dict):
            return {}, raw
        data.setdefault("id", "")
        data.setdefault("created", "")
        data.setdefault("source", "conversation")
        data.setdefault("confidence", "medium")
        data.setdefault("pinned", False)
        data.setdefault("tags", [])
        data.setdefault("supersedes", [])
        data.setdefault("zone", "general")
        data.setdefault("always_active", False)
        data.setdefault("rank", 0)
        return data, body_part.strip()
    except Exception:
        return {}, raw


def serialize_frontmatter(data: Dict[str, Any], body: str) -> str:
    """Serialize a dict + body into YAML frontmatter format.

    Does NOT depend on PyYAML for output — uses manual YAML construction
    to avoid dependency and ensure consistent formatting.
    """
    buf = ["---"]
    for key in ("id", "created", "source", "confidence", "zone"):
        val = data.get(key, "")
        if val:
            buf.append(f"{key}: {val}")
    if data.get("pinned"):
        buf.append("pinned: true")
    tags = data.get("tags", [])
    if tags:
        buf.append("tags:")
        for t in tags:
            buf.append(f"  - {t}")
    supersedes = data.get("supersedes", [])
    if supersedes:
        buf.append("supersedes:")
        for s in supersedes:
            buf.append(f"  - {s}")
    if data.get("always_active"):
        buf.append("always_active: true")
    rank = data.get("rank")
    if rank is not None and rank != 0:
        buf.append(f"rank: {rank}")
    buf.append("---")
    if body:
        buf.append("")
        buf.append(body)
    return "\n".join(buf)


def read_memory(path: Path, scope: str) -> Optional[LoadedMemory]:
    """Parse a memory file into LoadedMemory."""
    try:
        raw = path.read_text(encoding="utf-8")
        data, body = parse_frontmatter(raw)
        fm = MemoryFrontmatter(
            id=data.get("id", ""),
            created=data.get("created", ""),
            source=data.get("source", "user"),
            confidence=data.get("confidence", "medium"),
            pinned=bool(data.get("pinned", False)),
            tags=data.get("tags", []),
            supersedes=data.get("supersedes", []),
            zone=normalize_zone(data.get("zone", "general")),
            rank=int(data.get("rank", 0)),
        )
        return LoadedMemory(frontmatter=fm, body=body.strip(), source_path=path, scope=scope)
    except Exception as e:
        logger.warning("Failed to read memory %s: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# Async queues with backpressure
# ---------------------------------------------------------------------------

# P1-2: Background stat writer
_stat_queue: "queue.Queue[List[Tuple[str, str]]]" = queue.Queue(maxsize=500)


def _stat_flush_worker() -> None:
    """Drain the stat queue and append to JSONL in a single file open per batch."""
    while True:
        try:
            batch = _stat_queue.get(timeout=1)
        except Exception:
            continue
        if batch is None:
            break
        try:
            now = datetime.now(timezone.utc).isoformat()
            sp = _stats_path()
            sp.parent.mkdir(parents=True, exist_ok=True)
            with open(sp, "a", encoding="utf-8") as f:
                for memory_id, event in batch:
                    f.write(json.dumps({
                        "memory_id": memory_id,
                        "event": event,
                        "at": now,
                    }, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("Failed to batch record %d memory stats", len(batch))


_stat_thread = threading.Thread(target=_stat_flush_worker, daemon=True)
_stat_thread.start()


def _stats_path() -> Path:
    return plugin_data_dir() / "memory-stats.jsonl"


def record_memory_stat(memory_id: str, event: str) -> None:
    """Append a memory stat entry to stats.jsonl. Best-effort."""
    batch_record_stats([(memory_id, event)])


def batch_record_stats(entries: List[Tuple[str, str]]) -> None:
    """Append multiple stat entries. Uses bounded queue (maxsize=500).
    Falls back to synchronous write when queue is full."""
    try:
        _stat_queue.put_nowait(entries)
    except queue.Full:
        try:
            now = datetime.now(timezone.utc).isoformat()
            sp = _stats_path()
            sp.parent.mkdir(parents=True, exist_ok=True)
            with open(sp, "a", encoding="utf-8") as f:
                for memory_id, event in entries:
                    f.write(json.dumps({
                        "memory_id": memory_id,
                        "event": event,
                        "at": now,
                    }, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("Stat sync fallback write failed")


import atexit as _atexit

# P2-2: Async file writer
_write_queue: "queue.Queue[Tuple[Path, str]]" = queue.Queue(maxsize=500)
_pending_writes: Set[Path] = set()


def _safe_write(path: Path, content: str) -> None:
    """Write content to path with fsync for crash-safe persistence.

    P1: MemoryStore.put() previously used Path.write_text() which does NOT
    call os.fsync(), leaving data in kernel page cache for up to 30 seconds.
    This helper ensures the data reaches the storage device before returning.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def _file_flush_worker() -> None:
    """Drain write queue in background, writing files to disk."""
    while True:
        try:
            item = _write_queue.get(timeout=1)
        except Exception:
            continue
        if item is None:
            break
        path, content = item
        try:
            _safe_write(path, content)
        except Exception:
            logger.debug("Async write failed for %s", path)
        finally:
            _pending_writes.discard(path)


_write_thread = threading.Thread(target=_file_flush_worker, daemon=True)
_write_thread.start()


def async_write_memory(path: Path, fm: MemoryFrontmatter, body: str) -> None:
    """Submit memory file write to background thread.
    Uses bounded queue (maxsize=500). Falls back to sync write when queue is full.
    """
    data = {
        "id": fm.id,
        "created": fm.created,
        "source": fm.source,
        "confidence": fm.confidence,
        "pinned": fm.pinned,
        "tags": fm.tags,
        "supersedes": fm.supersedes,
        "zone": fm.zone,
        "rank": fm.rank,
    }
    content = serialize_frontmatter(data, body)
    _pending_writes.add(path)
    try:
        _write_queue.put_nowait((path, content))
    except queue.Full:
        _pending_writes.discard(path)
        try:
            _safe_write(path, content)
        except Exception as e:
            logger.warning("Sync write fallback failed for %s: %s", path, e)


def _shutdown_file_writer() -> None:
    """Flush remaining file writes on process exit."""
    _write_queue.put(None)
    _write_thread.join(timeout=5)


_atexit.register(_shutdown_file_writer)


def load_effectiveness() -> Dict[str, MemoryEffectiveness]:
    """Load effectiveness stats for all memories from stats.jsonl."""
    sp = _stats_path()
    if not sp.exists():
        return {}
    eff: Dict[str, MemoryEffectiveness] = {}
    try:
        with open(sp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mid = entry.get("memory_id", "")
                if not mid:
                    continue
                e = eff.setdefault(mid, MemoryEffectiveness())
                ev = entry.get("event", "")
                if ev == "loaded":
                    e.loaded += 1
                elif ev == "referenced":
                    e.referenced += 1
                elif ev == "accessed":
                    e.accessed += 1
                at = entry.get("at")
                if at and (e.last_event_at is None or at > e.last_event_at):
                    e.last_event_at = at
    except Exception:
        logger.debug("Failed to load effectiveness stats")
    return eff


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
    cp = ord(c)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def cjk_ratio(text: str) -> float:
    """Return fraction of alphabet-like chars that are CJK in text.
    Skips whitespace, digits, punctuation.
    """
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
    CJK-dominant (>40%) → 0.75  |  Mixed (10-40%) → 0.80  |  Latin → 0.85
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

_TOKEN_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]+")

_STOPWORDS: Set[str] = {
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


def _tokenise(s: str) -> List[str]:
    """Tokenize text for BM25 search.
    Preserves CJK bigrams, alphanumeric tokens, filters stopwords and short tokens.
    """
    lower = s.lower()
    tokens: List[str] = []
    # First split by non-word boundary regex
    for part in _TOKEN_RE.split(lower):
        if not part:
            continue
        # Check if the part contains CJK characters
        has_cjk = any(is_cjk(c) for c in part)
        if has_cjk:
            # CJK bigram tokenization: sliding window of 2 chars
            for i in range(len(part) - 1):
                bigram = part[i:i+2]
                if all(is_cjk(c) for c in bigram):
                    tokens.append(bigram)
                    i += 1  # skip one for overlap
            # Also include the whole part if it has non-CJK
            non_cjk = ''.join(c for c in part if not is_cjk(c))
            if len(non_cjk) >= _MIN_TOKEN_LEN and non_cjk not in _STOPWORDS:
                tokens.append(non_cjk)
        else:
            if len(part) >= _MIN_TOKEN_LEN and part not in _STOPWORDS:
                tokens.append(part)
    return tokens


def _memory_tokens(memory: LoadedMemory) -> Counter:
    """Tokenize a memory's body + tags for BM25 indexing.

    Note (P2-2): Uses regex [a-zA-Z0-9_\\-]{2,}|[\\u4e00-\\u9fff]+ for
    tokenization. Mixed CJK+ASCII text (e.g. "GPT-4模型") may be split into
    unexpected tokens like ["gpt", "4", "模型"] rather than ["gpt-4模型"].
    Future improvement: apply CJK n-gram post-processing.
    """
    return _tokenise(m.body)


# ---------------------------------------------------------------------------
# BM25 scoring
# ---------------------------------------------------------------------------

def _bm25_search_scored(memories: List[LoadedMemory], query: str, k: int = 5,
                 effectiveness: Optional[Dict[str, MemoryEffectiveness]] = None,
                 doc_tokens: Optional[List[Tuple[str, Counter]]] = None,
                 ) -> List[Tuple[LoadedMemory, float]]:
    """BM25 search with IDF-based scoring.

    Note (P2-1): Uses log(N/df) instead of the standard BM25 saturating IDF
    formula log((N-df+0.5)/(df+0.5)). No k1 saturating factor or b length
    normalization. Acceptable at ~100-memory scale (<1ms), but may show
    degraded ranking for multi-term queries on larger datasets.

    BM25 formula (Robertson & Zaragoza, 2009):
      score(D,Q) = Σ IDF(q_i) * (k1+1)*TF / (TF + k1*(1-b+b*|D|/avgdl))
    k1=1.5, b=0.75 optimized for CJK mixed text.
    """
    k1, b = 1.5, 0.75
    if k == 0 or not memories:
        return []
    q_tokens = tokenise(query)
    if not q_tokens:
        return []
    n = len(memories)

    df: Dict[str, int] = Counter()
    doc_lens: List[int] = []
    raw_doc_tokens: List[List[str]]
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
        idf_cache[t] = (n - df_t + 0.5) / (df_t + 0.5) + 1.0

    if not idf_cache:
        return []

    scored: List[Tuple[float, LoadedMemory]] = []
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
                if eff:
                    score *= eff.factor() * eff.decay_factor()
            scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(m, s) for s, m in scored[:k]]


def _bm25_search(memories: List[LoadedMemory], query: str, k: int,
                effectiveness: Optional[Dict[str, MemoryEffectiveness]] = None,
                doc_tokens: Optional[List[Tuple[str, List[str]]]] = None) -> List[LoadedMemory]:
    scored = _bm25_search_scored(memories, query, k, effectiveness, doc_tokens)
    return [m for m, _ in scored]


def _cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors."""
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
