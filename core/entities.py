"""Entity extraction for mem-reflection-hermes.

Best-effort named-entity and code-entity extraction using regex heuristics
plus optional spaCy. Depends on core.config for entity config subtree.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

from .config import CONFIG_KEY_ENTITY, plugin_config


_ENTITY_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/)?(?:[\w.\-]+[\\/])+[\w.\-]+\.\w+")
_ENTITY_CODE_RE = re.compile(r"`([^`]{2,120})`")
_ENTITY_QUOTED_RE = re.compile(r"\"([^\"]{2,120})\"|'([^']{2,120})'")
_ENTITY_PACKAGE_RE = re.compile(r"\b(?:[A-Za-z_]\w*\.){1,}[A-Za-z_]\w*\b")
_ENTITY_CAMEL_RE = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_ENTITY_COMPOUND_RE = re.compile(r"\b[a-z0-9]+(?:[-_/][a-z0-9]+){1,}\b", re.IGNORECASE)


def _plugin_config_hook() -> Dict[str, Any]:
    """Return plugin_config(), preferring a monkeypatch on core.store if present."""
    try:
        import sys
        store_mod = sys.modules.get("core.store")
        if store_mod is not None:
            fn = getattr(store_mod, "plugin_config", None)
            if fn is not None and fn is not plugin_config:
                return fn()
    except Exception:
        pass
    return plugin_config()


def _entity_config() -> Dict[str, Any]:
    cfg = _plugin_config_hook().get(CONFIG_KEY_ENTITY, {})
    return cfg if isinstance(cfg, dict) else {}


def entity_enabled() -> bool:
    return bool(_entity_config().get("enabled", True))


def entity_weight() -> float:
    raw = _entity_config().get("weight", 0.08)
    try:
        return float(raw)
    except Exception:
        return 0.08


def _normalize_entity_text(text: str) -> str:
    value = re.sub(r"\s+", " ", text.strip())
    return value.lower()


def _extract_entities_spacy(text: str) -> List[Tuple[str, str]]:
    try:
        import spacy  # type: ignore
        try:
            nlp = spacy.load("en_core_web_sm")
        except Exception:
            return []
        doc = nlp(text)
        out: List[Tuple[str, str]] = []
        for ent in doc.ents:
            label = (ent.label_ or "spacy").lower()
            candidate = ent.text.strip()
            if len(candidate) >= 3:
                out.append((candidate, label))
        return out
    except Exception:
        return []


def extract_entities(text: str) -> List[Dict[str, Any]]:
    """Best-effort entity extraction using regex plus optional spaCy."""
    if not text or not text.strip():
        return []

    seen: Set[Tuple[str, str]] = set()
    entities: List[Dict[str, Any]] = []

    def _add(candidate: str, kind: str, weight: float = 1.0) -> None:
        cleaned = candidate.strip().strip("`\"'")
        if len(cleaned) < 3:
            return
        normalized = _normalize_entity_text(cleaned)
        key = (normalized, kind)
        if not normalized or key in seen:
            return
        seen.add(key)
        entities.append({
            "text": cleaned,
            "normalized": normalized,
            "type": kind,
            "weight": weight,
        })

    for match in _ENTITY_PATH_RE.finditer(text):
        _add(match.group(0), "file_path", 1.0)
    for match in _ENTITY_CODE_RE.finditer(text):
        _add(match.group(1), "code", 0.9)
    for match in _ENTITY_QUOTED_RE.finditer(text):
        candidate = match.group(1) or match.group(2)
        _add(candidate, "quoted", 0.8)
    for match in _ENTITY_PACKAGE_RE.finditer(text):
        _add(match.group(0), "package", 0.75)
    for match in _ENTITY_CAMEL_RE.finditer(text):
        _add(match.group(0), "proper", 0.7)
    for match in _ENTITY_COMPOUND_RE.finditer(text):
        _add(match.group(0), "compound", 0.65)
    for candidate, kind in _extract_entities_spacy(text):
        _add(candidate, kind, 0.6)

    return entities
