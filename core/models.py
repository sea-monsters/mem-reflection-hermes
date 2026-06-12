"""Data models and frontmatter I/O for mem-reflection-hermes.

This module owns MemoryFrontmatter, LoadedMemory, SkillFrontmatter, LoadedSkill,
plus the parse/serialize helpers used by MemoryStore and SkillStore.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .utils import normalize_zone


try:
    import frontmatter as _frontmatter
    _HAS_FRONTMATTER = True
except ImportError:
    _HAS_FRONTMATTER = False


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
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None

    @classmethod
    def new(cls, source: str, confidence: str = "medium",
            tags: Optional[List[str]] = None,
            zone: Optional[str] = None,
            pinned: bool = False,
            supersedes: Optional[List[str]] = None,
            supersedes_reason: Optional[str] = None,
            user_id: Optional[str] = None,
            agent_id: Optional[str] = None,
            run_id: Optional[str] = None) -> "MemoryFrontmatter":
        """Factory: create a new frontmatter with auto-generated id and timestamp."""
        return cls(
            id=str(uuid.uuid4()),
            created=datetime.now(timezone.utc).isoformat(),
            source=source,
            confidence=confidence,
            pinned=pinned,
            tags=list(tags or []),
            supersedes=list(supersedes or []),
            supersedes_reason=supersedes_reason,
            zone=normalize_zone(zone),
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dict suitable for YAML frontmatter.

        Omits fields that are at their default values to keep the file clean,
        but always includes id, created, and source.
        """
        d: Dict[str, Any] = {}
        for f in ("id", "created", "source", "confidence", "pinned", "tags",
                   "supersedes", "supersedes_reason", "valid_from", "valid_until",
                   "context_scope", "zone", "rank", "version",
                   "user_id", "agent_id", "run_id"):
            v = getattr(self, f)
            if v == "" and f in ("user_id", "agent_id", "run_id"):
                v = None
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
    if s.startswith("﻿"):
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
    data.setdefault("user_id", None)
    data.setdefault("agent_id", None)
    data.setdefault("run_id", None)
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
    for key in ("supersedes_reason", "valid_from", "valid_until", "context_scope",
                "user_id", "agent_id", "run_id"):
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


def read_memory(path: Path, scope: str = "user") -> Optional[LoadedMemory]:
    """Read a single memory Markdown file into a LoadedMemory."""
    try:
        metadata, body = _load_frontmatter_file(path)
    except Exception as e:
        logger.warning("Failed to load memory file %s: %s", path, e)
        return None
    try:
        fm = MemoryFrontmatter.from_dict(metadata)
    except Exception as e:
        logger.warning("Failed to parse frontmatter for %s: %s", path, e)
        return None
    return LoadedMemory(frontmatter=fm, body=body, source_path=path, scope=scope)


def write_memory_atomic(path: Path, fm: MemoryFrontmatter, body: str) -> None:
    """Atomically write a memory Markdown file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(serialize_frontmatter(fm.to_dict(), body), encoding="utf-8")
    os.replace(tmp, path)
