"""core.py — Models, constants, and utility functions for mem-reflection-hermes.

Zero-dependency leaf module (no imports from other mem-reflection modules).
All dataclasses, configuration constants, path helpers, frontmatter I/O,
async queues, and zone utilities live here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import queue
import re
import threading
import time
import uuid
from collections import Counter, OrderedDict
from weakref import WeakValueDictionary
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
    supersedes_reason: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    context_scope: Optional[str] = None
    zone: str = "general"
    rank: int = 0

    @classmethod
    def new(
        cls,
        source: str,
        confidence: str = "medium",
        tags: Optional[List[str]] = None,
        zone: Optional[str] = None,
    ) -> "MemoryFrontmatter":
        """Create frontmatter for a new memory with the current timestamp."""
        return cls(
            id=str(uuid.uuid4()),
            created=datetime.now(timezone.utc).isoformat(),
            source=source,
            confidence=confidence,
            tags=list(tags or []),
            zone=normalize_zone(zone),
        )


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

    The parser supports full YAML frontmatter when a YAML backend is
    available, including nested mappings, nested lists, and multiline values.
    Unknown keys are preserved in the returned dict.
    """
    s = text.strip()
    if s.startswith("\ufeff"):
        s = s[1:]
    if not s.startswith("---"):
        return {}, text
    lines = s.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text

    yaml_lines: List[str] = []
    body_start = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = idx + 1
            break
        yaml_lines.append(line)

    if body_start is None:
        return {}, text

    yaml_part = "".join(yaml_lines)
    body_part = "".join(lines[body_start:])
    data = _load_frontmatter_yaml(yaml_part)
    if not isinstance(data, dict):
        return {}, text
    data = dict(data)
    data.setdefault("id", "")
    data.setdefault("created", "")
    data.setdefault("source", "conversation")
    data.setdefault("confidence", "medium")
    data.setdefault("pinned", False)
    data.setdefault("tags", [])
    data.setdefault("supersedes", [])
    data.setdefault("supersedes_reason", None)
    data.setdefault("valid_from", None)
    data.setdefault("valid_until", None)
    data.setdefault("context_scope", None)
    data.setdefault("zone", "general")
    data.setdefault("always_active", False)
    data.setdefault("rank", 0)
    return data, body_part.strip()


def _load_frontmatter_yaml(yaml_part: str) -> Dict[str, Any]:
    """Load YAML frontmatter into a dict, preserving nested structures."""
    try:
        import msgspec
        decoded = msgspec.yaml.decode(yaml_part)
        if isinstance(decoded, dict):
            return dict(decoded)
    except Exception:
        pass
    try:
        import yaml
        data = yaml.safe_load(yaml_part) or {}
        if isinstance(data, dict):
            return dict(data)
    except Exception:
        pass
    # Dependency-free fallback: parse simple key-value YAML used by Hermes memories
    data: Dict[str, Any] = {}
    current_list_key: Optional[str] = None
    for line in yaml_part.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key is not None:
            item = stripped[2:].strip()
            lst = data.setdefault(current_list_key, [])
            if isinstance(lst, list):
                lst.append(item)
            continue
        if ":" in stripped:
            key, _, raw_val = stripped.partition(":")
            key = key.strip()
            val = raw_val.strip()
            current_list_key = None
            if val == "":
                # Could be the start of a list block
                current_list_key = key
                continue
            # Boolean and None parsing
            if val.lower() == "true":
                data[key] = True
            elif val.lower() == "false":
                data[key] = False
            elif val.lower() == "null" or val.lower() == "~":
                data[key] = None
            else:
                # Try number parsing
                try:
                    if "." in val:
                        data[key] = float(val)
                    else:
                        data[key] = int(val)
                except (ValueError, TypeError):
                    data[key] = val
    return data


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
    for key in ("supersedes_reason", "valid_from", "valid_until", "context_scope"):
        val = data.get(key)
        if val is not None:
            buf.append(f"{key}: {val}")
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
            supersedes_reason=data.get("supersedes_reason"),
            valid_from=data.get("valid_from"),
            valid_until=data.get("valid_until"),
            context_scope=data.get("context_scope"),
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

# Stat writer (synchronous path — background queue removed as dead code in beta3)
_stat_write_lock = threading.Lock()


def _stats_path() -> Path:
    return plugin_data_dir() / "memory-stats.jsonl"


def _append_stat_entries(entries: List[Tuple[str, str]]) -> None:
    """Append memory stat entries synchronously so cache refresh is immediate."""
    now = datetime.now(timezone.utc).isoformat()
    sp = _stats_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    with _stat_write_lock:
        with open(sp, "a", encoding="utf-8") as f:
            for memory_id, event in entries:
                f.write(json.dumps({
                    "memory_id": memory_id,
                    "event": event,
                    "at": now,
                }, ensure_ascii=False) + "\n")


def record_memory_stat(memory_id: str, event: str) -> None:
    """Append a memory stat entry to stats.jsonl synchronously."""
    try:
        _append_stat_entries([(memory_id, event)])
    except Exception:
        logger.debug("Failed to record memory stat for %s", memory_id)


def batch_record_stats(entries: List[Tuple[str, str]]) -> None:
    """Append multiple stat entries synchronously."""
    try:
        _append_stat_entries(entries)
    except Exception:
        logger.debug("Stat sync write failed")


import atexit as _atexit

# P2-2: Async file writer
_write_queue: "queue.Queue[Tuple[Path, str, int]]" = queue.Queue(maxsize=500)
_pending_writes: Set[Path] = set()
_write_guard_lock = threading.Lock()
_write_path_locks: WeakValueDictionary = WeakValueDictionary()
_write_generations: Dict[str, int] = {}


def _write_path_key(path: Path) -> str:
    return str(path.resolve(strict=False))


def _write_path_lock(path: Path) -> threading.RLock:
    key = _write_path_key(path)
    with _write_guard_lock:
        lock = _write_path_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _write_path_locks[key] = lock
        return lock


def _reserve_write_generation(path: Path) -> int:
    key = _write_path_key(path)
    with _write_guard_lock:
        token = _write_generations.get(key, 0) + 1
        _write_generations[key] = token
        return token


def _is_current_write_generation(path: Path, token: int) -> bool:
    key = _write_path_key(path)
    with _write_guard_lock:
        return _write_generations.get(key, 0) == token


def _cancel_pending_write(path: Path) -> None:
    key = _write_path_key(path)
    with _write_guard_lock:
        _write_generations[key] = _write_generations.get(key, 0) + 1
        _pending_writes.discard(path)


def _safe_write(path: Path, content: str) -> None:
    """Atomically write content to path via temp-file + os.replace.

    P1: MemoryStore.put() previously used Path.write_text() which does NOT
    call os.fsync(), leaving data in kernel page cache for up to 30 seconds.
    This helper ensures the data reaches the storage device before returning.
    P0-beta3: Uses temp-file + os.replace to avoid truncated files on crash.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _frontmatter_to_data(fm: MemoryFrontmatter) -> Dict[str, Any]:
    """Convert frontmatter to a serializable dict without dropping optional fields."""
    return {
        "id": fm.id,
        "created": fm.created,
        "source": fm.source,
        "confidence": fm.confidence,
        "pinned": fm.pinned,
        "tags": fm.tags,
        "supersedes": fm.supersedes,
        "supersedes_reason": fm.supersedes_reason,
        "valid_from": fm.valid_from,
        "valid_until": fm.valid_until,
        "context_scope": fm.context_scope,
        "zone": fm.zone,
        "rank": fm.rank,
    }


def _write_memory(path: Path, fm: MemoryFrontmatter, body: str) -> None:
    """Synchronous memory write (used by update/reorder)."""
    content = serialize_frontmatter(_frontmatter_to_data(fm), body)
    _safe_write(path, content)


def _file_flush_worker() -> None:
    """Drain write queue in background, writing files to disk."""
    while True:
        try:
            item = _write_queue.get(timeout=1)
        except Exception:
            continue
        if item is None:
            break
        path, content, token = item
        try:
            with _write_path_lock(path):
                if not _is_current_write_generation(path, token):
                    continue
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
    content = serialize_frontmatter(_frontmatter_to_data(fm), body)
    token = _reserve_write_generation(path)
    _pending_writes.add(path)
    try:
        _write_queue.put_nowait((path, content, token))
    except queue.Full:
        _pending_writes.discard(path)
        try:
            with _write_path_lock(path):
                if _is_current_write_generation(path, token):
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
            i = 0
            while i < len(part) - 1:
                bigram = part[i:i+2]
                if all(is_cjk(c) for c in bigram):
                    tokens.append(bigram)
                    i += 2  # advance by 2 for overlapping bigrams (was 1, fixed for correct overlap)
                else:
                    i += 1
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
    return _tokenise(memory.body + " " + " ".join(memory.frontmatter.tags or []))


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
    q_tokens = _tokenise(query)
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
        idf_cache[t] = math.log((n - df_t + 0.5) / (df_t + 0.5) + 1.0)

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


# ---------------------------------------------------------------------------
# Lineage helpers (WS-1 / WS-2)
# ---------------------------------------------------------------------------

def _lineage_latest(store, mem_id: str) -> Optional[str]:
    """Return the latest memory ID in the supersedes chain starting at mem_id.

    Walks forward through supersedes links (memories that supersede the given
    one) until no successor is found.
    """
    current = mem_id
    visited: Set[str] = set()
    while current not in visited:
        visited.add(current)
        successor = None
        for m in store.list():
            if current in (m.frontmatter.supersedes or []):
                successor = m.id()
                break
        if successor is None:
            break
        current = successor
    return current if current != mem_id else None


def _lineage_root(store, mem_id: str) -> str:
    """Return the root (first) memory ID in the supersedes chain.

    Walks backward through supersedes links. If no parent, mem_id is its own root.
    """
    current = mem_id
    visited: Set[str] = set()
    while current not in visited:
        visited.add(current)
        m = store.get(current)
        if m is None or not m.frontmatter.supersedes:
            break
        current = m.frontmatter.supersedes[0]
    return current


def _lineage_depth(store, mem_id: str, visited: Optional[Set[str]] = None) -> int:
    """Depth of mem_id in its supersedes chain (0 = root, never superseded)."""
    if visited is None:
        visited = set()
    if mem_id in visited:
        return 0
    visited.add(mem_id)
    m = store.get(mem_id)
    if m is None or not m.frontmatter.supersedes:
        return 0
    parent = m.frontmatter.supersedes[0]
    return 1 + _lineage_depth(store, parent, visited)


def _lineage_cycle_check(store, mem_id: str) -> Optional[List[str]]:
    """Detect a cycle in the supersedes chain starting at mem_id.

    Returns the cycle path (list of IDs) if a cycle exists, otherwise None.
    """
    path: List[str] = []
    current = mem_id
    while current is not None:
        if current in path:
            cycle_start = path.index(current)
            return path[cycle_start:] + [current]
        path.append(current)
        m = store.get(current)
        if m is None or not m.frontmatter.supersedes:
            break
        current = m.frontmatter.supersedes[0]
    return None


def _classify_update_intent(old_body: str, new_body: str) -> str:
    """Classify the semantic relationship between an old and new memory.

    Returns one of: replacement, correction, elaboration, scoped_exception,
    historical_episode.

    This is a lightweight heuristic; the reflection pipeline can override it
    with explicit supersedes_reason.
    """
    old_lower = old_body.lower()
    new_lower = new_body.lower()

    # Correction markers
    correction_markers = ["actually", "wrong", "incorrect", "not anymore", "no longer", "changed", "updated"]
    if any(m in new_lower for m in correction_markers):
        return "correction"

    # Scoped exception markers
    if "except" in new_lower or "unless" in new_lower or "but for" in new_lower:
        return "scoped_exception"

    # Historical episode markers
    if any(m in new_lower for m in ["on monday", "on tuesday", "yesterday", "last week", "then"]):
        if any(m in old_lower for m in ["on monday", "on tuesday", "yesterday", "last week", "then"]):
            return "historical_episode"

    # Elaboration: new significantly longer and contains old
    if len(new_body) > len(old_body) * 1.3 and old_lower in new_lower:
        return "elaboration"

    return "replacement"


def _is_expired(fm: MemoryFrontmatter, now: Optional[datetime] = None) -> bool:
    """Check if a memory's valid_until date has passed."""
    if fm.valid_until is None:
        return False
    try:
        until_dt = datetime.fromisoformat(fm.valid_until)
        now_dt = now or datetime.now(timezone.utc)
        return now_dt > until_dt
    except Exception:
        return False


def _is_context_mismatch(fm: MemoryFrontmatter, current_scope: Optional[str] = None) -> bool:
    """Check if a memory's context_scope does not match current scope.

    Returns False if context_scope is None or matches current_scope.
    """
    if fm.context_scope is None or current_scope is None:
        return False
    return fm.context_scope != current_scope
