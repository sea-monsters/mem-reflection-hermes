"""Configuration helpers for mem-reflection-hermes.

This module owns Hermes config discovery, plugin config access, path helpers,
and feature flags. It sits above core.utils in the DAG so it can depend on
zone/path utilities without cycles.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
CONFIG_KEY_ENTITY = "entity"

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


def palace_index_path() -> Path:
    """Path to palace-index.md cache file."""
    return _plugin_data_dir_legacy() / "palace-index.md"


def zone_cache_dir() -> Path:
    """Path to zone-cache directory for per-zone summaries."""
    d = _plugin_data_dir_legacy() / "zone-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Typed configuration models
# ---------------------------------------------------------------------------
@dataclass
class SearchConfig:
    cjk_tokenizer: str = "auto"


@dataclass
class ContextCompressionConfig:
    enabled: bool = True
    mild_ratio: float = 0.85
    aggressive_ratio: float = 1.0
    emergency_ratio: float = 1.25


@dataclass
class ContextConfig:
    token_budget: int = 2000
    recall_timeout_ms: int = 1500
    split_stable_dynamic: bool = True
    compression: ContextCompressionConfig = field(default_factory=ContextCompressionConfig)


@dataclass
class CheckpointConfig:
    enabled: bool = True
    recover_on_session_start: bool = True
    max_pending_sessions: int = 20


@dataclass
class EntityConfig:
    enabled: bool = True
    weight: float = 0.08
    extractor: str = "auto"


@dataclass
class ReflectionConfig:
    mode: str = "auto"
    micro_reflection: bool = False


@dataclass
class CuratorConfig:
    enabled: bool = True
    stale_days: int = 90
    effectiveness_threshold: float = 0.1


@dataclass
class PluginConfigModel:
    search: SearchConfig = field(default_factory=SearchConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    checkpoint: CheckpointConfig = field(default_factory=CheckpointConfig)
    entity: EntityConfig = field(default_factory=EntityConfig)
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
    curator: CuratorConfig = field(default_factory=CuratorConfig)
    diagnostics: Dict[str, List[str]] = field(default_factory=lambda: {"warnings": [], "unknown_keys": []})


def _as_mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_bool(value: Any, default: bool, path: str, warnings: List[str]) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    warnings.append(f"{path}: expected bool, using default {default}")
    return default


def _as_int(value: Any, default: int, path: str, warnings: List[str]) -> int:
    if isinstance(value, bool):
        warnings.append(f"{path}: expected int, using default {default}")
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    if value is not None:
        warnings.append(f"{path}: expected int, using default {default}")
    return default


def _as_float(value: Any, default: float, path: str, warnings: List[str]) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    if value is not None:
        warnings.append(f"{path}: expected float, using default {default}")
    return default


def _as_choice(value: Any, default: str, path: str, warnings: List[str], allowed: set[str]) -> str:
    if isinstance(value, str) and value.strip().lower() in allowed:
        return value.strip().lower()
    if value is not None:
        warnings.append(f"{path}: expected one of {sorted(allowed)}, using default {default}")
    return default


def get_plugin_config_model(raw: Optional[Dict[str, Any]] = None) -> PluginConfigModel:
    """Return a typed config model with default fallback and diagnostics."""
    source = _as_mapping(plugin_config() if raw is None else raw)
    warnings: List[str] = []
    unknown: List[str] = []

    known_top = {"search", "context", "checkpoint", "entity", "reflection", "curator"}
    for key in source:
        if key not in known_top:
            unknown.append(key)

    search_raw = _as_mapping(source.get("search"))
    search = SearchConfig(
        cjk_tokenizer=_as_choice(
            search_raw.get("cjk_tokenizer"),
            "auto",
            "search.cjk_tokenizer",
            warnings,
            {"auto", "bigram", "jieba"},
        ),
    )
    for key in search_raw:
        if key not in {"cjk_tokenizer"}:
            unknown.append(f"search.{key}")

    compression_raw = _as_mapping(_as_mapping(source.get("context")).get("compression"))
    compression = ContextCompressionConfig(
        enabled=_as_bool(compression_raw.get("enabled"), True, "context.compression.enabled", warnings),
        mild_ratio=_as_float(compression_raw.get("mild_ratio"), 0.85, "context.compression.mild_ratio", warnings),
        aggressive_ratio=_as_float(compression_raw.get("aggressive_ratio"), 1.0, "context.compression.aggressive_ratio", warnings),
        emergency_ratio=_as_float(compression_raw.get("emergency_ratio"), 1.25, "context.compression.emergency_ratio", warnings),
    )
    for key in compression_raw:
        if key not in {"enabled", "mild_ratio", "aggressive_ratio", "emergency_ratio"}:
            unknown.append(f"context.compression.{key}")

    context_raw = _as_mapping(source.get("context"))
    context = ContextConfig(
        token_budget=_as_int(context_raw.get("token_budget"), 2000, "context.token_budget", warnings),
        recall_timeout_ms=_as_int(context_raw.get("recall_timeout_ms"), 1500, "context.recall_timeout_ms", warnings),
        split_stable_dynamic=_as_bool(context_raw.get("split_stable_dynamic"), True, "context.split_stable_dynamic", warnings),
        compression=compression,
    )
    for key in context_raw:
        if key not in {"token_budget", "recall_timeout_ms", "split_stable_dynamic", "compression"}:
            unknown.append(f"context.{key}")

    checkpoint_raw = _as_mapping(source.get("checkpoint"))
    checkpoint = CheckpointConfig(
        enabled=_as_bool(checkpoint_raw.get("enabled"), True, "checkpoint.enabled", warnings),
        recover_on_session_start=_as_bool(
            checkpoint_raw.get("recover_on_session_start"),
            True,
            "checkpoint.recover_on_session_start",
            warnings,
        ),
        max_pending_sessions=_as_int(
            checkpoint_raw.get("max_pending_sessions"),
            20,
            "checkpoint.max_pending_sessions",
            warnings,
        ),
    )
    for key in checkpoint_raw:
        if key not in {"enabled", "recover_on_session_start", "max_pending_sessions"}:
            unknown.append(f"checkpoint.{key}")

    entity_raw = _as_mapping(source.get("entity"))
    entity = EntityConfig(
        enabled=_as_bool(entity_raw.get("enabled"), True, "entity.enabled", warnings),
        weight=_as_float(entity_raw.get("weight"), 0.08, "entity.weight", warnings),
        extractor=_as_choice(
            entity_raw.get("extractor"),
            "auto",
            "entity.extractor",
            warnings,
            {"auto", "regex", "spacy"},
        ),
    )
    for key in entity_raw:
        if key not in {"enabled", "weight", "extractor"}:
            unknown.append(f"entity.{key}")

    reflection_raw = _as_mapping(source.get("reflection"))
    reflection = ReflectionConfig(
        mode=_as_choice(reflection_raw.get("mode"), "auto", "reflection.mode", warnings, {"auto", "llm", "embedding", "raw_chunk", "hybrid"}),
        micro_reflection=_as_bool(reflection_raw.get("micro_reflection"), False, "reflection.micro_reflection", warnings),
    )
    for key in reflection_raw:
        if key not in {"mode", "micro_reflection"}:
            unknown.append(f"reflection.{key}")

    curator_raw = _as_mapping(source.get("curator"))
    stale_raw = _as_mapping(curator_raw.get("stale"))
    curator = CuratorConfig(
        enabled=_as_bool(curator_raw.get("enabled"), True, "curator.enabled", warnings),
        stale_days=_as_int(stale_raw.get("days"), 90, "curator.stale.days", warnings),
        effectiveness_threshold=_as_float(
            stale_raw.get("effectiveness_threshold"),
            0.1,
            "curator.stale.effectiveness_threshold",
            warnings,
        ),
    )
    for key in stale_raw:
        if key not in {"days", "effectiveness_threshold"}:
            unknown.append(f"curator.stale.{key}")
    for key in curator_raw:
        if key not in {"enabled", "stale"}:
            unknown.append(f"curator.{key}")

    return PluginConfigModel(
        search=search,
        context=context,
        checkpoint=checkpoint,
        entity=entity,
        reflection=reflection,
        curator=curator,
        diagnostics={
            "warnings": warnings,
            "unknown_keys": unknown,
        },
    )


def get_config_diagnostics(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return defaults plus diagnostics for config inspection."""
    model = get_plugin_config_model(raw)
    data = asdict(model)
    data["diagnostics"] = model.diagnostics
    return data
