"""Utility helpers for mem-reflection-hermes core.

Zone constants, fast hashing, and small string/file helpers used across
core modules. This module is intentionally dependency-light so it can be
imported by config, tokenization, models, and store without cycles.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional


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
