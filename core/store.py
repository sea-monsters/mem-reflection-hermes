"""SQLite-backed memory store for mem-reflection-hermes.

Replaces the file-only MemoryStore with a hybrid approach: SQLite as the
primary index for fast queries, Markdown files as the durable truth layer
(human-readable, git-friendly).

This is a LEAF module -- no imports from other project modules.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import uuid
import math
import queue
import tempfile
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    import frontmatter as _frontmatter
    _HAS_FRONTMATTER = True
except ImportError:
    _HAS_FRONTMATTER = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration keys
# ---------------------------------------------------------------------------
CONFIG_SECTION = "mem_reflection_hermes"
CONFIG_KEY_EMBEDDINGS = "embeddings"
CONFIG_KEY_MICRO_REFLECTION = "micro_reflection"
CONFIG_KEY_PALACE_MODE = "palace_mode"
CONFIG_KEY_PROFILE_MODE = "profile_mode"
CONFIG_KEY_PALACE_INSTRUCTIONS = "palace_instructions"
CONFIG_KEY_ACTIVE_MEMORY_CAP = "active_memory_index_cap"
CONFIG_KEY_SKILL_INDEX_CAP = "skill_index_cap"
CONFIG_KEY_RELEVANT_MEMORY_CAP = "relevant_memory_cap"
CONFIG_KEY_TRIGGERED_SKILL_CAP = "triggered_skill_cap"
CONFIG_KEY_INTENT_PROTOTYPES = "intent_prototypes"
CONFIG_KEY_RERANKER = "reranker"

# ---------------------------------------------------------------------------
# Config & paths (with mtime-aware caching)
# ---------------------------------------------------------------------------
_cached_config: Optional[Dict[str, Any]] = None
_cached_config_mtime: float = 0.0


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
        import yaml
        with open(cfg_path, "r", encoding="utf-8") as fh:
            _cached_config = yaml.safe_load(fh) or {}
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
    """Legacy plugin data directory kept for compatibility."""
    return hermes_home() / "plugins" / "mem-reflection-hermes"


def embeddings_enabled() -> bool:
    """Check if embedding-based search is enabled in config."""
    return plugin_config().get(CONFIG_KEY_EMBEDDINGS, True)


def micro_reflection_enabled() -> bool:
    """Check if micro-reflection is enabled in config."""
    return bool(plugin_config().get(CONFIG_KEY_MICRO_REFLECTION, False))


def palace_mode_enabled() -> bool:
    """Check if palace mode is enabled in config."""
    return bool(plugin_config().get(CONFIG_KEY_PALACE_MODE, True))


def profile_mode_enabled() -> bool:
    """Check if profile mode is enabled in config."""
    return bool(plugin_config().get(CONFIG_KEY_PROFILE_MODE, False))

# ---------------------------------------------------------------------------
# Zone constants
# ---------------------------------------------------------------------------
_ZONE_CORE = "core"
_ZONE_WORK = "work"
_ZONE_EPISODE = "episode"
_ZONE_GENERAL = "general"
_ZONE_SEMANTIC = "semantic"
_VALID_ZONES = {_ZONE_CORE, _ZONE_WORK, _ZONE_EPISODE, _ZONE_GENERAL, _ZONE_SEMANTIC}
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
    """Check whether *zone* is a recognised zone identifier."""
    return zone in _VALID_ZONES or zone.startswith(_PROJECT_ZONE_PREFIX)


def sanitize_zone_filename(zone: str) -> str:
    """Produce a filesystem-safe filename from a zone string."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", zone)


def fast_hash(text: str) -> str:
    """Fast non-crypto hash for write-on-change comparison."""
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


def palace_index_path() -> Path:
    """Path to palace-index.md cache file."""
    return _plugin_data_dir_legacy() / "palace-index.md"


def zone_cache_dir() -> Path:
    """Path to zone-cache directory for per-zone summaries."""
    d = _plugin_data_dir_legacy() / "zone-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
            # CJK bigram tokenization: non-overlapping window of 2 chars
            i = 0
            while i < len(part) - 1:
                bigram = part[i:i + 2]
                if all(is_cjk(c) for c in bigram):
                    tokens.append(bigram)
                    i += 2  # non-overlapping stride
                else:
                    i += 1
            # Also include the whole part if it has non-CJK
            non_cjk = ''.join(c for c in part if not is_cjk(c))
            if len(non_cjk) >= _MIN_TOKEN_LEN and non_cjk not in _STOPWORDS:
                tokens.append(non_cjk)
            # Filter CJK bigrams against stopwords
            tokens = [t for t in tokens if t not in _CJK_STOPWORDS]
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


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class MemoryFrontmatter:
    """Structured metadata for a memory entry."""
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
    version: int = 1

    @classmethod
    def new(cls, source: str, confidence: str = "medium",
            tags: Optional[List[str]] = None,
            zone: Optional[str] = None,
            pinned: bool = False) -> "MemoryFrontmatter":
        """Factory: create a new frontmatter with auto-generated id and timestamp."""
        return cls(
            id=str(uuid.uuid4()),
            created=datetime.now(timezone.utc).isoformat(),
            source=source,
            confidence=confidence,
            pinned=pinned,
            tags=list(tags or []),
            zone=normalize_zone(zone),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dict suitable for YAML frontmatter.

        Omits fields that are at their default values to keep the file clean,
        but always includes id, created, and source.
        """
        d: Dict[str, Any] = {}
        for f in ("id", "created", "source", "confidence", "pinned", "tags",
                   "supersedes", "supersedes_reason", "valid_from", "valid_until",
                   "context_scope", "zone", "rank", "version"):
            v = getattr(self, f)
            if v is not None and v != [] and v is not False and v != 0 and v != "general" and v != "medium":
                d[f] = v
            elif f in ("id", "created", "source", "confidence"):
                d[f] = v
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryFrontmatter":
        """Deserialize from a dict, ignoring unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


@dataclass
class LoadedMemory:
    """A fully loaded memory: metadata + body + provenance."""
    frontmatter: MemoryFrontmatter
    body: str
    source_path: Path
    scope: str

    def id(self) -> str:
        return self.frontmatter.id


@dataclass
class MemoryStatEntry:
    memory_id: str
    event: str
    at: str


@dataclass
class MemoryEffectiveness:
    """Per-memory effectiveness tracking (loaded / referenced / accessed)."""
    loaded: int = 0
    referenced: int = 0
    accessed: int = 0
    last_event_at: Optional[str] = None

    def factor(self) -> float:
        if self.loaded == 0:
            return 1.0
        return 0.5 + 0.5 * (self.referenced / self.loaded)

    def decay_factor(self, now: Optional[datetime] = None) -> float:
        if self.last_event_at is None:
            return 1.0
        now_dt = now or datetime.now(timezone.utc)
        try:
            last_dt = datetime.fromisoformat(self.last_event_at.replace("Z", "+00:00"))
            days = max(0, (now_dt - last_dt).days)
            return max(0.3, 0.5 ** (days / 30.0))
        except Exception:
            return 1.0


@dataclass
class SkillFrontmatter:
    """Structured metadata for a skill entry."""
    name: str
    description: str
    triggers: List[str] = field(default_factory=list)
    version: Optional[str] = None
    license: Optional[str] = None
    always_active: bool = False


@dataclass
class LoadedSkill:
    """A fully loaded skill: metadata + body + provenance."""
    frontmatter: SkillFrontmatter
    body: str
    source_path: Path
    scope: str


# ---------------------------------------------------------------------------
# Frontmatter I/O
# ---------------------------------------------------------------------------

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
                current_list_key = key
                continue
            if val.lower() == "true":
                data[key] = True
            elif val.lower() == "false":
                data[key] = False
            elif val.lower() in ("null", "~"):
                data[key] = None
            else:
                try:
                    if "." in val:
                        data[key] = float(val)
                    else:
                        data[key] = int(val)
                except (ValueError, TypeError):
                    data[key] = val
    return data


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from a memory file."""
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


def serialize_frontmatter(data: Dict[str, Any], body: str) -> str:
    """Serialize a dict + body into YAML frontmatter format."""
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


def _load_frontmatter_file(path: Path) -> Tuple[Dict[str, Any], str]:
    """Read a file and parse its YAML frontmatter. Uses python-frontmatter
    if available (preserves YAML types), falls back to stdlib parse_frontmatter."""
    text = path.read_text(encoding="utf-8")
    if _HAS_FRONTMATTER:
        try:
            post = _frontmatter.loads(text)
            return dict(post.metadata), post.content.strip()
        except Exception:
            pass
    return parse_frontmatter(text)


# ---------------------------------------------------------------------------
# Skill I/O
# ---------------------------------------------------------------------------

def _read_skill_file(path: Path, scope: str) -> Optional[LoadedSkill]:
    """Read a SKILL.md file with YAML frontmatter."""
    try:
        metadata, body = _load_frontmatter_file(path)
        fm = SkillFrontmatter(
            name=metadata.get("name", path.parent.name),
            description=metadata.get("description", ""),
            triggers=metadata.get("triggers", []),
            version=metadata.get("version"),
            license=metadata.get("license"),
            always_active=bool(metadata.get("always_active", False)),
        )
        return LoadedSkill(frontmatter=fm, body=body,
                           source_path=path, scope=scope)
    except Exception as e:
        logger.warning("Failed to read skill %s: %s", path, e)
        return None


class SkillStore:
    """File-based skill store with lazy caching (skills are static per session)."""

    def __init__(self, user_root: Path, project_root: Optional[Path] = None):
        self.user_root = user_root
        self.project_root = project_root
        self._cache: Optional[List[LoadedSkill]] = None
        self._disabled: Set[str] = set()

    def invalidate_cache(self) -> None:
        self._cache = None

    def disable_project_skill(self, name: str) -> None:
        self._disabled.add(name)
        self.invalidate_cache()

    def enable_project_skill(self, name: str) -> None:
        self._disabled.discard(name)
        self.invalidate_cache()

    def list(self) -> List[LoadedSkill]:
        if self._cache is not None:
            return self._cache
        user_skills = self._scan(self.user_root, "user")
        project_skills = self._scan(self.project_root, "project") if self.project_root else []
        project_skills = [s for s in project_skills
                          if s.frontmatter.name not in self._disabled]
        project_names = {s.frontmatter.name for s in project_skills}
        user_skills = [s for s in user_skills
                       if s.frontmatter.name not in project_names]
        out = user_skills + project_skills
        out.sort(key=lambda s: s.frontmatter.name)
        self._cache = out
        return out

    def get(self, name: str) -> Optional[LoadedSkill]:
        if self.project_root:
            p = self.project_root / name / "SKILL.md"
            if p.exists():
                return _read_skill_file(p, "project")
        p = self.user_root / name / "SKILL.md"
        if p.exists():
            return _read_skill_file(p, "user")
        return None

    @staticmethod
    def _scan(root: Optional[Path], scope: str) -> List[LoadedSkill]:
        out: List[LoadedSkill] = []
        if root is None or not root.exists():
            return out
        for d in root.iterdir():
            if not d.is_dir():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            s = _read_skill_file(skill_md, scope)
            if s:
                out.append(s)
        return out


# ---------------------------------------------------------------------------
# Frontmatter IO (python-frontmatter)
# ---------------------------------------------------------------------------
def read_memory(path: Path, scope: str = "user") -> Optional[LoadedMemory]:
    """Read a Markdown memory file with YAML frontmatter."""
    try:
        metadata, body = _load_frontmatter_file(path)
        fm = MemoryFrontmatter.from_dict(metadata)
        return LoadedMemory(
            frontmatter=fm, body=body,
            source_path=path, scope=scope,
        )
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def write_memory_atomic(path: Path, fm: MemoryFrontmatter, body: str) -> None:
    """Write a memory file atomically (write-to-tmp + os.replace)."""
    try:
        import yaml
        _has_yaml = True
    except ImportError:
        _has_yaml = False
    fm_dict = fm.to_dict()
    if _has_yaml:
        yaml_part = yaml.dump(fm_dict, default_flow_style=False,
                              allow_unicode=True, sort_keys=False)
    else:
        # Fallback: simple YAML-like format using standard library
        yaml_part = _simple_yaml_dump(fm_dict)
    nl = chr(10)
    content = chr(45)*3 + nl + yaml_part + chr(45)*3 + nl + body + nl
    _safe_write(path, content)


def _simple_yaml_dump(d: dict) -> str:
    """Minimal YAML-like dump for frontmatter serialization.

    Handles flat key-value pairs and list values (tags, supersedes).
    No deep nesting support — frontmatter dicts are always flat.
    """
    lines = []
    for key, value in d.items():
        if isinstance(value, list):
            items = [f"  - {_yaml_str(v)}" for v in value]
            lines.append(f"{key}:")
            lines.extend(items)
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif value is None:
            continue
        else:
            lines.append(f"{key}: {_yaml_str(value)}")
    return "\n".join(lines) + "\n"


def _yaml_str(v: Any) -> str:
    """Format a value for YAML with proper quoting."""
    s = str(v)
    # Quote if contains special YAML characters
    if any(c in s for c in (":", "#", "'", '"', "!", "&", "*", "?", "[", "]", "{", "}", ",")):
        escaped = s.replace("'", "''")
        return f"'{escaped}'"
    return s


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
        logger.warning("Failed to record memory stat for %s", memory_id)


def batch_record_stats(entries: List[Tuple[str, str]]) -> None:
    """Append multiple stat entries synchronously."""
    try:
        _append_stat_entries(entries)
    except Exception:
        logger.warning("Stat sync write failed")


# P2-2: Async file writer
_write_queue: "queue.Queue[Tuple[Path, str, int]]" = queue.Queue(maxsize=500)
_pending_writes: Set[Path] = set()
_write_guard_lock = threading.Lock()
_write_path_locks: Dict[str, threading.RLock] = {}
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


def _cleanup_write_generations(path: Path) -> None:
    with _write_guard_lock:
        if path not in _pending_writes:
            key = _write_path_key(path)
            _write_generations.pop(key, None)
            _write_path_locks.pop(key, None)


def _cancel_pending_write(path: Path) -> None:
    key = _write_path_key(path)
    with _write_guard_lock:
        _write_generations[key] = _write_generations.get(key, 0) + 1
        _pending_writes.discard(path)
        _cleanup_write_generations(path)


def _safe_write(path: Path, content: str) -> None:
    """Atomically write content to path via unique-temp-file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8",
        dir=path.parent, suffix=".tmp", delete=False,
    )
    try:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    finally:
        f.close()
    for _ in range(5):
        try:
            os.replace(f.name, path)
            return
        except PermissionError:
            time.sleep(0.01)
    os.replace(f.name, path)


def _frontmatter_to_data(fm: MemoryFrontmatter) -> Dict[str, Any]:
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
    content = serialize_frontmatter(_frontmatter_to_data(fm), body)
    _safe_write(path, content)


def _file_flush_worker() -> None:
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
            logger.warning("Async write failed for %s", path)
        finally:
            _pending_writes.discard(path)
            _cleanup_write_generations(path)


_write_thread = threading.Thread(target=_file_flush_worker, daemon=True)
_write_thread.start()


def async_write_memory(path: Path, fm: MemoryFrontmatter, body: str) -> None:
    """Submit memory file write to background thread."""
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
    _write_queue.put(None)
    _write_thread.join(timeout=5)


import atexit as _atexit
_atexit.register(_shutdown_file_writer)


def load_effectiveness() -> Dict[str, MemoryEffectiveness]:
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
    except Exception as e:
        logger.warning("Failed to load effectiveness stats from %s: %s", sp, e)
    return eff


def _bm25_search_scored(
    memories: List[LoadedMemory],
    query: str,
    k: int = 5,
    effectiveness: Optional[Dict[str, MemoryEffectiveness]] = None,
    doc_tokens: Optional[List[Tuple[str, Counter]]] = None,
    query_tokens: Optional[List[str]] = None,
) -> List[Tuple[LoadedMemory, float]]:
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


def _bm25_search(
    memories: List[LoadedMemory],
    query: str,
    k: int,
    effectiveness: Optional[Dict[str, MemoryEffectiveness]] = None,
    doc_tokens: Optional[List[Tuple[str, List[str]]]] = None,
) -> List[LoadedMemory]:
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


def _lineage_latest(store, mem_id: str) -> Optional[str]:
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
    old_lower = old_body.lower()
    new_lower = new_body.lower()
    correction_markers = ["actually", "wrong", "incorrect", "not anymore", "no longer", "changed", "updated"]
    if any(m in new_lower for m in correction_markers):
        return "correction"
    if "except" in new_lower or "unless" in new_lower or "but for" in new_lower:
        return "scoped_exception"
    if any(m in new_lower for m in ["on monday", "on tuesday", "yesterday", "last week", "then"]):
        if any(m in old_lower for m in ["on monday", "on tuesday", "yesterday", "last week", "then"]):
            return "historical_episode"
    if len(new_body) > len(old_body) * 1.3 and old_lower in new_lower:
        return "elaboration"
    return "replacement"


def _is_expired(fm: MemoryFrontmatter, now: Optional[datetime] = None) -> bool:
    if fm.valid_until is None:
        return False
    try:
        until_dt = datetime.fromisoformat(fm.valid_until)
        now_dt = now or datetime.now(timezone.utc)
        return now_dt > until_dt
    except Exception:
        return False


def _is_context_mismatch(fm: MemoryFrontmatter, current_scope: Optional[str] = None) -> bool:
    if fm.context_scope is None or current_scope is None:
        return False
    return fm.context_scope != current_scope


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    zone TEXT NOT NULL DEFAULT 'general',
    confidence TEXT NOT NULL DEFAULT 'medium',
    pinned INTEGER NOT NULL DEFAULT 0,
    rank INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    valid_from TEXT,
    valid_until TEXT,
    context_scope TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    supersedes_reason TEXT,
    body_hash TEXT NOT NULL,
    path TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mem_zone ON memories(zone);
CREATE INDEX IF NOT EXISTS idx_mem_pinned ON memories(pinned) WHERE pinned = 1;
CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created);
CREATE TABLE IF NOT EXISTS tags (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (memory_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tag_name ON tags(tag);
CREATE TABLE IF NOT EXISTS supersedes (
    old_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    new_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    reason TEXT,
    PRIMARY KEY (old_id, new_id)
);
CREATE TABLE IF NOT EXISTS stats (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    event TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stats_mem ON stats(memory_id);
"""


# ---------------------------------------------------------------------------
# MemoryStore (SQLite-backed)
# ---------------------------------------------------------------------------
class MemoryStore:
    """SQLite-indexed memory store with Markdown file persistence.

    SQLite is the primary index for fast queries.  Markdown files remain
    the durable, human-readable truth layer.  On startup the SQLite index
    is synchronised from disk to stay consistent.
    """

    def __init__(
        self,
        user_root: Path,
        project_root: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ):
        self.user_root = user_root
        self.project_root = project_root
        self._lock = threading.RLock()
        self._local = threading.local()
        self._db_path = db_path if db_path is not None else plugin_data_dir() / "memories.db"
        self._search_index = None
        self._graph = None
        self._index_dirty = True
        self._cached_index = ""
        self._last_index_hash = ""
        self._post_delete_callbacks: List[Callable[[str], None]] = []  # P2a: post-delete hooks
        self._init_db()
        self._sync_from_disk()

    def _mark_changed(self) -> None:
        """Invalidate derived runtime views after a mutation."""
        self._index_dirty = True
        self._cached_index = ""
        if self._search_index is not None:
            self._search_index.invalidate_cache()

    # -- SQLite connection (thread-local) ------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """Return a thread-local SQLite connection with WAL mode."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.Error:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        """Create SQLite tables if they do not yet exist."""
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()
        self._ensure_body_column(conn)

    @staticmethod
    def _ensure_body_column(conn: sqlite3.Connection) -> None:
        """Add body column to memories table for existing databases (migration)."""
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "body" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN body TEXT NOT NULL DEFAULT ''")
            conn.commit()

    # -- disk sync -----------------------------------------------------------

    def _sync_from_disk(self) -> None:
        """Scan .md files and upsert into SQLite index.

        Ensures the SQLite index reflects the current state of the
        Markdown files on disk.  Rows whose files have been removed
        outside the store are cleaned up.
        """
        conn = self._get_conn()
        disk_ids: Set[str] = set()
        for scope, root in (("user", self.user_root), ("project", self.project_root)):
            if root is None or not root.exists():
                continue
            for f in root.rglob("*.md"):
                m = read_memory(f, scope)
                if m is None:
                    continue
                disk_ids.add(m.id())
                self._upsert_memory_row(conn, m)
        # Remove rows whose files no longer exist on disk
        existing = {r["id"] for r in conn.execute("SELECT id FROM memories").fetchall()}
        stale = existing - disk_ids
        for sid in stale:
            conn.execute("DELETE FROM memories WHERE id = ?", (sid,))
        conn.commit()

    def _upsert_memory_row(self, conn: sqlite3.Connection, m: LoadedMemory) -> None:
        """Insert or update a single memory row plus tags and supersedes."""
        fm = m.frontmatter
        body_hash = hashlib.sha256(m.body.encode("utf-8")).hexdigest()[:16]
        conn.execute(
            """INSERT OR REPLACE INTO memories
               (id, scope, zone, confidence, pinned, rank, created, source,
                valid_from, valid_until, context_scope, version,
                supersedes_reason, body_hash, path, body)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fm.id, m.scope, fm.zone, fm.confidence, int(fm.pinned), fm.rank,
             fm.created, fm.source, fm.valid_from, fm.valid_until,
             fm.context_scope, fm.version, fm.supersedes_reason, body_hash,
             str(m.source_path), m.body),
        )
        conn.execute("DELETE FROM tags WHERE memory_id = ?", (fm.id,))
        for tag in (fm.tags or []):
            conn.execute("INSERT OR IGNORE INTO tags (memory_id, tag) VALUES (?, ?)",
                         (fm.id, tag))
        conn.execute("DELETE FROM supersedes WHERE new_id = ?", (fm.id,))
        for old_id in (fm.supersedes or []):
            conn.execute(
                "INSERT OR IGNORE INTO supersedes (old_id, new_id, reason) VALUES (?, ?, ?)",
                (old_id, fm.id, fm.supersedes_reason),
            )

    # -- helpers -------------------------------------------------------------

    def _validate_supersedes_targets(self, conn: sqlite3.Connection, fm: MemoryFrontmatter) -> None:
        """Reject direct writes that point supersedes at missing memories."""
        missing = []
        for old_id in fm.supersedes or []:
            row = conn.execute(
                "SELECT 1 FROM memories WHERE id = ?", (old_id,)
            ).fetchone()
            if row is None:
                missing.append(old_id)
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Cannot supersede missing memory id(s): {joined}")

    def _row_to_loaded(self, row: sqlite3.Row) -> Optional[LoadedMemory]:
        """Convert a SQLite row to LoadedMemory (reads body from DB, not disk)."""
        body = row["body"] or ""
        if not body:
            return self._row_to_loaded_from_disk(row)
        try:
            tags = [
                r["tag"] for r in self._get_conn().execute(
                    "SELECT tag FROM tags WHERE memory_id = ?", (row["id"],)
                ).fetchall()
            ]
            supers = [
                r["old_id"] for r in self._get_conn().execute(
                    "SELECT old_id FROM supersedes WHERE new_id = ?", (row["id"],)
                ).fetchall()
            ]
            fm = MemoryFrontmatter(
                id=row["id"],
                created=row["created"],
                source=row["source"],
                confidence=row["confidence"],
                pinned=bool(row["pinned"]),
                tags=tags,
                supersedes=supers,
                zone=row["zone"],
                rank=row["rank"],
                version=row["version"],
                supersedes_reason=row["supersedes_reason"],
                valid_from=row["valid_from"],
                valid_until=row["valid_until"],
                context_scope=row["context_scope"],
            )
            return LoadedMemory(
                frontmatter=fm, body=body,
                source_path=Path(row["path"]), scope=row["scope"],
            )
        except Exception as exc:
            logger.warning("SQLite row parse failed for %s: %s", row.get("id", "?"), exc)
            return self._row_to_loaded_from_disk(row)

    def _row_to_loaded_from_disk(self, row: sqlite3.Row) -> Optional[LoadedMemory]:
        """Fallback: read memory from .md file (used when body cache is empty)."""
        path = Path(row["path"])
        if not path.exists():
            return None
        return read_memory(path, row["scope"])

    def _root_for(self, scope: str) -> Path:
        """Return the filesystem root for the given scope."""
        if scope == "user":
            return self.user_root
        if scope == "project":
            if self.project_root is None:
                raise ValueError("Project scope requested but no project root configured")
            return self.project_root
        raise ValueError(f"Unknown scope: {scope}")

    # -- CRUD ----------------------------------------------------------------

    def put(self, scope: str, fm: MemoryFrontmatter, body: str) -> Path:
        """Create a new memory.  Returns the path to the .md file."""
        with self._lock:
            conn = self._get_conn()
            existing = conn.execute(
                "SELECT id FROM memories WHERE id = ?", (fm.id,)
            ).fetchone()
            if existing:
                raise ValueError(f"Duplicate memory id: {fm.id}")
            self._validate_supersedes_targets(conn, fm)
            root = self._root_for(scope)
            raw_date = fm.created[:10] if fm.created else datetime.now(timezone.utc).strftime("%Y-%m-%d")
            date_prefix = re.sub(r'[\\/]', '_', raw_date)
            short = re.sub(r'[^a-zA-Z0-9_-]', '_', fm.id[:16])
            path = root / f"{date_prefix}-{short}.md"
            write_memory_atomic(path, fm, body)
            m = LoadedMemory(frontmatter=fm, body=body.strip(),
                             source_path=path, scope=scope)
            self._upsert_memory_row(conn, m)
            conn.commit()
            self._mark_changed()
            return path

    def get(self, mem_id: str) -> Optional[LoadedMemory]:
        """Retrieve a single memory by id."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (mem_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_loaded(row)

    def get_by_id(self, mem_id: str) -> Optional[LoadedMemory]:
        """Compatibility alias used by CLUQI and dashboard integration."""
        return self.get(mem_id)

    def delete(self, scope: str, mem_id: str) -> bool:
        """Delete a memory by id.  Returns True if the memory was found."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT path FROM memories WHERE id = ?", (mem_id,)
            ).fetchone()
            if row is None:
                return False
            path = Path(row["path"]).resolve()
            expected_root = self._root_for(scope).resolve()
            if not str(path).startswith(str(expected_root)):
                logger.warning("Rejecting delete of out-of-bounds path: %s", path)
                return False
            if path.exists():
                try:
                    path.unlink()
                except OSError as e:
                    logger.warning("Failed to delete memory file %s: %s", path, e)
                    return False
            conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
            conn.commit()
            self._mark_changed()
            # P2a: post-delete callbacks (graph cleanup, etc.)
            for cb in self._post_delete_callbacks:
                try:
                    cb(mem_id)
                except Exception:
                    logger.warning("Post-delete callback failed for %s", mem_id, exc_info=True)
            return True

    def update(self, mem_id: str, body: Optional[str] = None,
               zone: Optional[str] = None, confidence: Optional[str] = None,
               tags: Optional[List[str]] = None,
               pinned: Optional[bool] = None,
               supersedes: Optional[List[str]] = None) -> Optional[LoadedMemory]:
        """Atomically update a memory's content or metadata.

        Builds a new frontmatter from the old one, writes the file, then
        updates the SQLite row.  Data is preserved on write failure because
        the old file is only overwritten after a successful tmp write.
        """
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (mem_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Memory not found: {mem_id}")
            loaded = self._row_to_loaded(row)
            if loaded is None:
                raise ValueError(f"Memory file missing on disk: {mem_id}")
            fm = loaded.frontmatter
            new_fm = MemoryFrontmatter(
                id=mem_id,
                created=fm.created,
                source=fm.source,
                confidence=confidence if confidence is not None else fm.confidence,
                pinned=pinned if pinned is not None else fm.pinned,
                tags=tags if tags is not None else fm.tags,
                supersedes=supersedes if supersedes is not None else fm.supersedes,
                supersedes_reason=fm.supersedes_reason,
                valid_from=fm.valid_from,
                valid_until=fm.valid_until,
                context_scope=fm.context_scope,
                zone=zone if zone is not None else fm.zone,
                rank=fm.rank,
            )
            new_body = body if body is not None else loaded.body
            write_memory_atomic(loaded.source_path, new_fm, new_body)
            updated = LoadedMemory(
                frontmatter=new_fm, body=new_body.strip(),
                source_path=loaded.source_path, scope=loaded.scope,
            )
            self._upsert_memory_row(conn, updated)
            conn.commit()
            self._mark_changed()
            return updated

    # -- listing & queries ---------------------------------------------------

    # Allowlisted sort orders to prevent SQL injection via sort parameter
    _LIST_SORT_ORDERS: Dict[str, str] = {
        "rank": "rank DESC, created DESC",
        "created": "created DESC",
        "created_asc": "created ASC",
        "zone": "zone ASC, rank DESC",
    }

    def list(self, *, zone: Optional[str] = None,
             active_only: bool = False, sort: str = "rank",
             limit: Optional[int] = None) -> List[LoadedMemory]:
        """Query memories with optional zone filter and active-only mode."""
        conn = self._get_conn()
        clauses: List[str] = []
        params: List[Any] = []
        if zone:
            clauses.append("zone = ?")
            params.append(normalize_zone(zone))
        if active_only:
            clauses.append("id NOT IN (SELECT old_id FROM supersedes)")
        where = " AND ".join(clauses) if clauses else "1=1"
        order = self._LIST_SORT_ORDERS.get(sort, self._LIST_SORT_ORDERS["rank"])
        sql = f"SELECT * FROM memories WHERE {where} ORDER BY {order}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        rows = conn.execute(sql, params).fetchall()
        results: List[LoadedMemory] = []
        for row in rows:
            m = self._row_to_loaded(row)
            if m is not None:
                results.append(m)
        return results

    def list_active(self) -> List[LoadedMemory]:
        """Return all non-superseded memories."""
        return self.list(active_only=True)

    def list_pinned(self) -> List[LoadedMemory]:
        """Return pinned, non-superseded memories."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT m.* FROM memories m
               WHERE m.pinned = 1
               AND m.id NOT IN (SELECT old_id FROM supersedes)
               ORDER BY m.rank DESC, m.created DESC"""
        ).fetchall()
        results: List[LoadedMemory] = []
        for row in rows:
            m = self._row_to_loaded(row)
            if m is not None:
                results.append(m)
        return results

    def list_by_zone(self, zone: str) -> List[LoadedMemory]:
        """Return active memories for one Memory Palace zone."""
        return self.list(zone=zone, active_only=True)

    def group_by_zone(self) -> Dict[str, List[LoadedMemory]]:
        """Return active memories grouped by normalized zone."""
        groups: Dict[str, List[LoadedMemory]] = {}
        for memory in self.list_active():
            groups.setdefault(memory.frontmatter.zone, []).append(memory)
        return groups

    def _get_search_index(self):
        """Lazily create the retrieval index without making store import-heavy."""
        if self._search_index is None:
            try:
                from .search import SearchIndex
            except ImportError:
                mod = sys.modules.get("_search")
                if mod is None:
                    search_path = Path(__file__).resolve().with_name("search.py")
                    spec = importlib.util.spec_from_file_location("_memory_search_module", search_path)
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules["_memory_search_module"] = mod
                    spec.loader.exec_module(mod)
                SearchIndex = mod.SearchIndex
            try:
                from .reranker import _build_reranker
                reranker = _build_reranker(plugin_config().get(CONFIG_KEY_RERANKER, {}))
            except Exception:
                reranker = None
            self._search_index = SearchIndex(self, graph=self._graph, reranker=reranker)
        return self._search_index

    def set_graph(self, graph) -> None:
        """Attach the graph index used by search-time Hebbian boosting."""
        self._graph = graph
        if self._search_index is not None:
            self._search_index._graph = graph
            self._search_index.invalidate_cache()

    def search(self, query: str, k: int = 5, include_history: bool = False,
               zone: Optional[str] = None) -> List[LoadedMemory]:
        """Search memories through the retrieval index."""
        return self._get_search_index().search(query, k=k, zone=zone, include_history=include_history)

    def fusion_search(self, query: str, k: int = 5, zone: Optional[str] = None,
                      include_history: bool = False, **kwargs) -> List[LoadedMemory]:
        """Search memories with the full retrieval pipeline."""
        return self._get_search_index().search(
            query,
            k=k,
            zone=zone,
            include_history=include_history,
            **kwargs,
        )

    def check_conflict(self, body: str, threshold: Optional[float] = None,
                       exclude_ids: Optional[List[str]] = None) -> Optional[Tuple[str, float]]:
        """Detect whether a candidate body duplicates an active memory."""
        return self._get_search_index().check_conflict(body, threshold=threshold, exclude_ids=exclude_ids)

    def zone_counts(self) -> Dict[str, int]:
        """Return {zone: count} for all active memories."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT zone, COUNT(*) as cnt FROM memories
               WHERE id NOT IN (SELECT old_id FROM supersedes)
               GROUP BY zone"""
        ).fetchall()
        return {r["zone"]: r["cnt"] for r in rows}

    # -- lineage helpers -----------------------------------------------------

    def is_superseded(self, mem_id: str) -> bool:
        """Check if a memory has been superseded by another."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM supersedes WHERE old_id = ?", (mem_id,)
        ).fetchone()
        return row is not None

    def latest_for(self, mem_id: str) -> Optional[LoadedMemory]:
        """Return the latest (current) memory in the supersedes chain."""
        conn = self._get_conn()
        current = mem_id
        visited: Set[str] = set()
        # Walk forward along supersedes edges: old_id -> new_id
        while current and current not in visited:
            visited.add(current)
            row = conn.execute(
                """SELECT s.new_id
                   FROM supersedes s
                   JOIN memories m ON m.id = s.new_id
                   WHERE s.old_id = ?
                   ORDER BY m.created DESC, m.version DESC, m.rank DESC, s.new_id DESC
                   LIMIT 1""",
                (current,),
            ).fetchone()
            if row is None:
                break
            current = row["new_id"]
        if current == mem_id and self.is_superseded(mem_id):
            return None
        return self.get(current)

    def lineage_chain(self, mem_id: str, max_depth: int = 10) -> List[LoadedMemory]:
        """Return the full supersedes chain from root to current."""
        conn = self._get_conn()
        # Walk backward to find the root (oldest ancestor)
        backward: List[str] = [mem_id]
        cur = mem_id
        backward_visited: Set[str] = set()
        while cur not in backward_visited and len(backward) < max_depth:
            backward_visited.add(cur)
            # Find memory that *is superseded by* cur (i.e. cur is the new_id)
            row = conn.execute(
                "SELECT old_id FROM supersedes WHERE new_id = ?", (cur,)
            ).fetchone()
            if row is None:
                break
            cur = row["old_id"]
            backward.append(cur)
        backward.reverse()
        # Now walk forward from root via supersedes edges
        chain: List[LoadedMemory] = []
        forward_cur = backward[0]
        forward_visited: Set[str] = set()
        depth = 0
        while forward_cur not in forward_visited and depth < max_depth:
            forward_visited.add(forward_cur)
            m = self.get(forward_cur)
            if m is not None:
                chain.append(m)
            row = conn.execute(
                """SELECT s.new_id
                   FROM supersedes s
                   JOIN memories m ON m.id = s.new_id
                   WHERE s.old_id = ?
                   ORDER BY m.created DESC, m.version DESC, m.rank DESC, s.new_id DESC
                   LIMIT 1""",
                (forward_cur,),
            ).fetchone()
            if row is None:
                break
            forward_cur = row["new_id"]
            depth += 1
        return chain

    def _calc_supersedes_depth(self, mem_id: str, visited: Optional[Set[str]] = None,
                               max_depth: int = 10, depth: int = 0) -> int:
        """Follow supersedes chain recursively to compute depth.

        depth=0: never superseded
        depth=1: supersedes one other memory
        depth=N: chain of N supersedes
        Guarded against cycles via visited set and max_depth.
        """
        if visited is None:
            visited = set()
        if mem_id in visited or depth >= max_depth:
            return depth
        visited.add(mem_id)
        conn = self._get_conn()
        # Find the memory that this one supersedes (backward link)
        row = conn.execute(
            "SELECT old_id FROM supersedes WHERE new_id = ? LIMIT 1", (mem_id,)
        ).fetchone()
        if row is None:
            return depth
        return self._calc_supersedes_depth(row["old_id"], visited, max_depth, depth + 1)

    # -- stats & effectiveness -----------------------------------------------

    def record_stat(self, memory_id: str, event: str) -> None:
        """Record a stat event (loaded / referenced / accessed) for a memory."""
        with self._lock:
            conn = self._get_conn()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO stats (memory_id, event, at) VALUES (?, ?, ?)",
                (memory_id, event, now),
            )
            conn.commit()

    def effectiveness(self, memory_id: Optional[str] = None) -> Dict[str, MemoryEffectiveness]:
        """Aggregate effectiveness stats from the stats table.

        If *memory_id* is given, returns a single-entry dict for that memory.
        Otherwise returns stats for all memories that have recorded events.
        """
        conn = self._get_conn()
        if memory_id is not None:
            rows = conn.execute(
                "SELECT event, at FROM stats WHERE memory_id = ?", (memory_id,)
            ).fetchall()
            eff = MemoryEffectiveness()
            for r in rows:
                ev = r["event"]
                at = r["at"]
                if ev == "loaded":
                    eff.loaded += 1
                elif ev == "referenced":
                    eff.referenced += 1
                elif ev == "accessed":
                    eff.accessed += 1
                if at and (eff.last_event_at is None or at > eff.last_event_at):
                    eff.last_event_at = at
            return {memory_id: eff}
        # Aggregate for all memories
        rows = conn.execute("SELECT memory_id, event, at FROM stats").fetchall()
        result: Dict[str, MemoryEffectiveness] = {}
        for r in rows:
            mid = r["memory_id"]
            e = result.setdefault(mid, MemoryEffectiveness())
            ev = r["event"]
            at = r["at"]
            if ev == "loaded":
                e.loaded += 1
            elif ev == "referenced":
                e.referenced += 1
            elif ev == "accessed":
                e.accessed += 1
            if at and (e.last_event_at is None or at > e.last_event_at):
                e.last_event_at = at
        return result

    # -- health metrics ------------------------------------------------------

    def health_metrics(self) -> Dict[str, Any]:
        """Compute memory health metrics for operator review."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE id NOT IN (SELECT old_id FROM supersedes)"
        ).fetchone()[0]
        pinned = conn.execute(
            """SELECT COUNT(*) FROM memories
               WHERE pinned = 1 AND id NOT IN (SELECT old_id FROM supersedes)"""
        ).fetchone()[0]
        zones = self.zone_counts()
        # Duplicate cluster detection via MinHash LSH (datasketch)
        dup_clusters = 0
        try:
            from datasketch import MinHash, MinHashLSH
            active_mems = self.list_active()
            if len(active_mems) > 1:
                lsh = MinHashLSH(threshold=0.85, num_perm=128)
                minhashes: Dict[str, MinHash] = {}
                for m in active_mems:
                    mh = MinHash(num_perm=128)
                    for token in _tokenise(m.body):
                        mh.update(token.encode("utf-8"))
                    lsh.insert(m.id(), mh)
                    minhashes[m.id()] = mh
                seen_ids: Set[str] = set()
                for m in active_mems:
                    if m.id() in seen_ids:
                        continue
                    neighbors = lsh.query(minhashes[m.id()])
                    if len(neighbors) > 1:
                        dup_clusters += 1
                        seen_ids.update(neighbors)
        except Exception:
            # Fallback: bounded pairwise Jaccard (O(30n), no LSH)
            active_mems = self.list_active()
            seen_ids: Set[str] = set()
            token_sets: Dict[str, Set[str]] = {}
            for m in active_mems:
                token_sets[m.id()] = set(_tokenise(m.body))
            max_cmp_per = 30
            total_cmp_cap = 2000
            total_cmp = 0
            for i, m1 in enumerate(active_mems):
                if m1.id() in seen_ids:
                    continue
                cluster = [m1]
                candidates = active_mems[i + 1: i + 1 + max_cmp_per]
                for m2 in candidates:
                    if m2.id() in seen_ids:
                        continue
                    if total_cmp >= total_cmp_cap:
                        break
                    total_cmp += 1
                    s1 = token_sets.get(m1.id(), set())
                    s2 = token_sets.get(m2.id(), set())
                    if not s1 or not s2:
                        continue
                    inter = len(s1 & s2)
                    union = len(s1 | s2)
                    jaccard = inter / union if union else 0.0
                    if jaccard > 0.85:
                        cluster.append(m2)
                        seen_ids.add(m2.id())
                if len(cluster) > 1:
                    dup_clusters += 1
                    seen_ids.update(m.id() for m in cluster)
        superseded_count = conn.execute("SELECT COUNT(*) FROM supersedes").fetchone()[0]
        return {
            "total_memories": total,
            "active_memories": active,
            "pinned_memories": pinned,
            "superseded_count": superseded_count,
            "zone_counts": zones,
            "duplicate_clusters": dup_clusters,
        }

    # -- index maintenance ---------------------------------------------------

    def rebuild_index(self) -> Dict[str, Any]:
        """Drop and recreate the SQLite index from disk .md files.

        More aggressive than _sync_from_disk — drops all tables first,
        then rescans every .md file.  Safe to call at any time; the
        Markdown files are the source of truth.
        """
        with self._lock:
            conn = self._get_conn()
            conn.execute("DROP TABLE IF EXISTS stats")
            conn.execute("DROP TABLE IF EXISTS supersedes")
            conn.execute("DROP TABLE IF EXISTS tags")
            conn.execute("DROP TABLE IF EXISTS memories")
            conn.commit()
            self._init_db()
            self._sync_from_disk()
        return self.validate_index()

    def validate_index(self) -> Dict[str, Any]:
        """Check consistency between SQLite index and disk files.

        Returns a diagnostic report:
          - orphaned_rows: SQLite rows whose .md file is missing
          - orphaned_files: .md files on disk with no SQLite row
          - hash_mismatches: rows where body_hash doesn't match the file
          - total_rows, total_files
        """
        conn = self._get_conn()
        rows = conn.execute("SELECT id, path, body_hash FROM memories").fetchall()
        disk_ids: Set[str] = set()
        orphaned_rows: List[str] = []
        hash_mismatches: List[str] = []
        for row in rows:
            path = Path(row["path"])
            if not path.exists():
                orphaned_rows.append(row["id"])
                continue
            m = read_memory(path, row["id"])
            if m is not None:
                disk_ids.add(m.id())
                current_hash = hashlib.sha256(m.body.encode("utf-8")).hexdigest()[:16]
                if current_hash != row["body_hash"]:
                    hash_mismatches.append(row["id"])

        orphaned_files: List[str] = []
        for scope, root in (("user", self.user_root), ("project", self.project_root)):
            if root is None or not root.exists():
                continue
            for f in root.rglob("*.md"):
                m = read_memory(f, scope)
                if m is not None and m.id() not in {
                    r["id"] for r in rows
                }:
                    orphaned_files.append(str(f))

        return {
            "total_rows": len(rows),
            "total_disk_files": len(disk_ids) + len(orphaned_files),
            "orphaned_rows": orphaned_rows,
            "orphaned_row_count": len(orphaned_rows),
            "orphaned_files": orphaned_files,
            "orphaned_file_count": len(orphaned_files),
            "hash_mismatches": hash_mismatches,
            "hash_mismatch_count": len(hash_mismatches),
        }

    def prune_index(self) -> Dict[str, Any]:
        """Remove stale SQLite rows whose .md files no longer exist.

        Also cleans up orphaned tags, supersedes, and stats rows that
        may have been missed if foreign keys were ever disabled.
        """
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute("SELECT id, path FROM memories").fetchall()
            removed: List[str] = []
            for row in rows:
                if not Path(row["path"]).exists():
                    removed.append(row["id"])
            for mid in removed:
                conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
            # Clean any orphaned rows in dependent tables (safety net)
            conn.execute(
                "DELETE FROM tags WHERE memory_id NOT IN (SELECT id FROM memories)"
            )
            conn.execute(
                "DELETE FROM supersedes WHERE old_id NOT IN (SELECT id FROM memories)"
                " OR new_id NOT IN (SELECT id FROM memories)"
            )
            conn.execute(
                "DELETE FROM stats WHERE memory_id NOT IN (SELECT id FROM memories)"
            )
            conn.commit()
        return {"pruned": len(removed), "pruned_ids": removed}

    # -- reorder (dashboard API) ---------------------------------------------

    def reorder(self, memory_ids: List[str]) -> List[str]:
        """Re-rank memories in the order given (highest rank first)."""
        with self._lock:
            conn = self._get_conn()
            reordered: List[str] = []
            for idx, mid in enumerate(memory_ids):
                conn.execute(
                    "UPDATE memories SET rank = ? WHERE id = ?",
                    (len(memory_ids) - idx, mid),
                )
                reordered.append(mid)
            conn.commit()
            self._mark_changed()
            return reordered
