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

from .extraction import extract_refined_memory_candidates
from .supersedes_resolver import resolve_semantic_supersedes

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
    "extract_refined_memory_candidates",
    "resolve_semantic_supersedes",
    # Runtime exports
    "_run_full_reflection",
    "_run_micro_reflection",
    "_run_embedding_micro_reflection",
    "_append_reflect_log",
    "_compact_episode_zone",
]
