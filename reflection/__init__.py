"""Reflection system for memory synthesis and evolution.

This package contains:
- engine.py: Core reflection engine with fact extraction
- runtime.py: Reflection runtime helpers and episode compaction
"""

from .engine import (
    ReflectionEngine,
    _is_memorable_content,
    _is_explicit_memory_intent,
)

from .runtime import (
    _run_full_reflection,
    _run_micro_reflection,
    _run_embedding_micro_reflection,
    _append_reflect_log,
    _compact_episode_zone,
)

__all__ = [
    # Engine exports
    "ReflectionEngine",
    "_is_memorable_content",
    "_is_explicit_memory_intent",
    # Runtime exports
    "_run_full_reflection",
    "_run_micro_reflection",
    "_run_embedding_micro_reflection",
    "_append_reflect_log",
    "_compact_episode_zone",
]
