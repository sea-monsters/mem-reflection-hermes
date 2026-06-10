"""Typed configuration helpers for mem-reflection-hermes."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

try:
    from .store import plugin_config as _plugin_config
except Exception:  # pragma: no cover - fallback for direct loading
    def _plugin_config() -> Dict[str, Any]:
        return {}


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
    source = _as_mapping(_plugin_config() if raw is None else raw)
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
