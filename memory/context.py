"""Memory Palace context assembly for Hermes Agent injection."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from ..core.store import LoadedMemory, LoadedSkill, _tokenise, _memory_tokens, plugin_config
    from ..core.config import get_plugin_config_model
    from ..core.scope import filter_memories_by_scope, normalize_scope_filters
except ImportError:
    import sys
    from pathlib import Path
    _repo = Path(__file__).resolve().parent.parent
    import importlib.util
    _store_pkg = "mem_reflection_hermes.core.store"
    if _store_pkg in sys.modules:
        _store_mod = sys.modules[_store_pkg]
    else:
        _spec = importlib.util.spec_from_file_location(_store_pkg, str(_repo / "core" / "store.py"))
        _store_mod = importlib.util.module_from_spec(_spec)
        _store_mod.__package__ = "mem_reflection_hermes.core"
        sys.modules[_store_pkg] = _store_mod
        _spec.loader.exec_module(_store_mod)
    LoadedMemory = _store_mod.LoadedMemory
    LoadedSkill = _store_mod.LoadedSkill
    _tokenise = _store_mod._tokenise
    _memory_tokens = _store_mod._memory_tokens
    plugin_config = _store_mod.plugin_config
    try:
        from core.scope import filter_memories_by_scope, normalize_scope_filters
    except ImportError:
        def normalize_scope_filters(filters):
            return filters
        def filter_memories_by_scope(memories, filters):
            if not filters:
                return list(memories)
            return [
                m for m in memories
                if all(getattr(getattr(m, "frontmatter", m), k, None) == v for k, v in filters.items())
            ]
    def get_plugin_config_model():
        class _DummyCompression:
            enabled = True
        class _DummyContext:
            compression = _DummyCompression()
            recall_timeout_ms = 1500
            token_budget = 2000
        class _Dummy:
            context = _DummyContext()
        return _Dummy()

logger = logging.getLogger(__name__)


@dataclass
class ContextBundle:
    """Structured context assembly result.

    `append_system_context` is for relatively stable content that changes
    infrequently. `prepend_context` is for per-turn dynamic recall.
    `debug` carries non-semantic metadata for tests and future diagnostics.
    """

    prepend_context: str = ""
    append_system_context: str = ""
    debug: Dict[str, Any] = field(default_factory=dict)

# ---------------------------------------------------------------------------
# Path helpers (for built-in memory)
# ---------------------------------------------------------------------------

ENTRY_DELIMITER = "\n\u00a7\n"  # Section sign delimiter, matching memory_tool.py

_COMPRESSION_LEVELS = ("none", "mild", "aggressive", "emergency")


def _hermes_home() -> Path:
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        return Path(env_home)
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except Exception:
        return Path.home() / ".hermes"


def _read_builtin_entries(target: str) -> List[str]:
    """Read entries from MEMORY.md or USER.md (matches memory_tool.py format)."""
    fname = "MEMORY.md" if target.lower() == "memory" else "USER.md"
    path = _hermes_home() / "memories" / fname
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        return [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]
    except OSError:
        return []


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def build_context_block(query: str = "") -> str:
    """Wrapper for build_context that uses global singletons.

    This is the public API exported by memory.__init__.py.
    """
    from .. import _get_mem_store, _get_search_index, _get_skill_store
    store = _get_mem_store()
    search = _get_search_index()
    skills = _get_skill_store()
    return build_context(store, search, skills, query)


def build_context_bundle(
    store,
    search,
    skills,
    query: str = "",
    max_tokens: int = 4000,
    stable_only: bool = False,
    filters: Optional[Dict[str, Any]] = None,
) -> ContextBundle:
    """Build a structured context bundle for host injection.

    The split is intentionally conservative for v1.4 phase 1:
    - Stable: pinned memories + always-active skills
    - Dynamic: relevant memories + triggered skills + compacted episodes

    `stable_only=True` is used by runtime fallback paths when dynamic recall is
    slow or unavailable.
    """
    stable_parts: List[str] = []
    token_budget = max_tokens
    used_tokens = 0
    debug: Dict[str, Any] = {
        "max_tokens": max_tokens,
        "stable_only": stable_only,
        "included_sections": [],
        "dropped_sections": [],
        "compression_level": "none",
    }

    def _try_add(target_parts: List[str], label: str, block: str) -> bool:
        nonlocal used_tokens
        if not block:
            return False
        t = _estimate_block_tokens(block)
        if used_tokens + t <= token_budget:
            target_parts.append(block)
            used_tokens += t
            debug["included_sections"].append(label)
            return True
        debug["dropped_sections"].append(label)
        return False

    filters = normalize_scope_filters(filters)

    def _list_pinned_scoped() -> List[LoadedMemory]:
        try:
            return store.list_pinned(filters=filters)
        except TypeError:
            return filter_memories_by_scope(store.list_pinned(), filters)

    def _list_active_scoped() -> List[LoadedMemory]:
        try:
            return store.list_active(filters=filters)
        except TypeError:
            return filter_memories_by_scope(store.list_active(), filters)

    def _search_scoped() -> List[LoadedMemory]:
        try:
            return search.search(query, k=10, filters=filters)
        except TypeError:
            return filter_memories_by_scope(search.search(query, k=10), filters)

    # 1. Stable: pinned memories
    pinned = _list_pinned_scoped()
    if pinned:
        block = "## Pinned Memories\n" + "\n".join(_format_memory(m) for m in pinned)
        _try_add(stable_parts, "pinned_memories", block)

    # 2. Dynamic: active memories (search if query provided)
    active: List[LoadedMemory] = []
    triggered: List[LoadedSkill] = []
    always = [s for s in skills.list() if getattr(s.frontmatter, "always_active", False)]
    episode_block = ""
    if not stable_only:
        if query and query.strip():
            try:
                active = _search_scoped()
            except Exception:
                logger.warning("Context search failed, falling back to list_active", exc_info=True)
                active = _list_active_scoped()[:10]
        else:
            active = _list_active_scoped()[:10]

        triggered = _match_triggered_skills(skills, query)

    # 4. Stable: always-active skills
    if always:
        block = "## Active Skills\n" + "\n".join(_format_skill(s, detail_level="mild") for s in always)
        _try_add(stable_parts, "always_active_skills", block)

    # 5. Dynamic: compacted episode summaries
    if not stable_only:
        try:
            cfg = plugin_config()
            if cfg.get("context_compacted_episode", True):
                episode_block = _build_compacted_episode_block(store, detail_level="mild", filters=filters)
        except Exception:
            debug["dropped_sections"].append("compacted_episode_summaries")

    remaining_budget = max(token_budget - used_tokens, 0)
    compression_enabled = True
    try:
        compression_enabled = bool(get_plugin_config_model().context.compression.enabled)
    except Exception:
        compression_enabled = True
    if stable_only:
        # Stable-only fallback contract: do not invoke the dynamic builder at
        # all, so compacted episodes and other dynamic sections are excluded.
        dynamic_parts: List[str] = []
        compression_level = "none"
        dropped_labels: List[str] = []
        included_labels: List[str] = []
    else:
        dynamic_parts, compression_level, dropped_labels, included_labels = _build_dynamic_context_parts(
            active=active,
            triggered=triggered,
            store=store,
            budget=remaining_budget,
            compression_enabled=compression_enabled,
        )
    debug["compression_level"] = compression_level
    for label in dropped_labels:
        if label not in debug["dropped_sections"]:
            debug["dropped_sections"].append(label)
    for label in included_labels:
        if label not in debug["included_sections"]:
            debug["included_sections"].append(label)

    used_tokens += sum(_estimate_block_tokens(block) for block in dynamic_parts)
    debug["used_tokens"] = used_tokens
    debug["stable_section_count"] = len(stable_parts)
    debug["dynamic_section_count"] = len(dynamic_parts)

    return ContextBundle(
        prepend_context="\n\n".join(dynamic_parts),
        append_system_context="\n\n".join(stable_parts),
        debug=debug,
    )


def build_context(store, search, skills, query: str = "", max_tokens: int = 4000, filters: Optional[Dict[str, Any]] = None) -> str:
    """Build a backward-compatible single-string context block for injection."""
    bundle = build_context_bundle(store, search, skills, query, max_tokens=max_tokens, filters=filters)
    parts = [bundle.append_system_context, bundle.prepend_context]
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# v1.1 context blocks
# ---------------------------------------------------------------------------


def _build_compacted_episode_block(store, detail_level: str = "mild", filters: Optional[Dict[str, Any]] = None) -> str:
    """Load compacted episode summaries from the episode zone.

    Searches for entries tagged 'compacted' and formats as a digest.
    """
    try:
        compacted = store.search_by_tags(["compacted"], zone="episode", limit=20)
        compacted = filter_memories_by_scope(compacted, filters)
    except Exception:
        # Fallback: scan episode zone for compacted entries
        try:
            all_ep = store.list_by_zone("episode")
            all_ep = filter_memories_by_scope(all_ep, filters)
            compacted = [m for m in all_ep if "compacted" in (m.frontmatter.tags or [])]
        except Exception:
            return ""

    if not compacted:
        return ""

    max_items = 10 if detail_level == "mild" else 6 if detail_level == "aggressive" else 4
    max_chars = 200 if detail_level == "mild" else 120 if detail_level == "aggressive" else 80
    lines = ["## Episode Summaries"]
    for m in compacted[:max_items]:
        body = m.body.strip()[:max_chars]
        if len(m.body) > max_chars:
            body += "..."
        lines.append(f"- {body}")
    if len(compacted) > max_items:
        lines.append(f"- ... ({len(compacted) - max_items} more)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_memory(mem: LoadedMemory, max_tokens: int = 100) -> str:
    """Format a memory for context injection with token-aware truncation.

    Uses a simple heuristic for token estimation when BM25 tokenization
    returns very few tokens (e.g., for repeated character sequences).
    """
    body = mem.body.strip()
    # Use BM25 tokenization first
    tokens = _tokenise(body)
    total_tokens = len(tokens)

    # If BM25 tokenization produces unexpectedly few tokens for long text,
    # use a character-based heuristic (assume ~4 chars per token for Latin)
    estimated_tokens = max(total_tokens, len(body) // 4)

    if estimated_tokens > max_tokens:
        # Simple truncation based on character limit
        char_limit = max_tokens * 4  # Approximate chars for max_tokens
        body = body[:char_limit]
        if len(mem.body.strip()) > len(body):
            body += "..."

    lines = [f"- [{mem.frontmatter.zone or 'general'}] {body}"]
    tags = getattr(mem.frontmatter, "tags", None)
    if tags:
        lines.append(f"  tags: {', '.join(tags)}")
    return "\n".join(lines)


def _format_skill(skill: LoadedSkill, detail_level: str = "mild") -> str:
    """Format a skill for context injection."""
    fm = skill.frontmatter
    if detail_level == "emergency":
        return f"### {fm.name}"
    max_chars = 300 if detail_level == "mild" else 120
    lines = [f"### {fm.name}", fm.description.strip()[:max_chars]]
    triggers = getattr(fm, "triggers", None)
    if triggers:
        lines.append(f"triggers: {', '.join(triggers)}")
    return "\n".join(lines)


def _build_dynamic_context_parts(
    *,
    active: List[LoadedMemory],
    triggered: List[LoadedSkill],
    store,
    budget: int,
    compression_enabled: bool = True,
) -> tuple[List[str], str, List[str], List[str]]:
    """Assemble dynamic context using compression levels instead of tail truncation."""
    if budget <= 0 and active:
        return [], "emergency", [
            "relevant_memories",
            "triggered_skills",
            "compacted_episode_summaries",
        ], []
    if budget <= 0:
        return [], "none", [], []

    section_order = ["relevant_memories", "triggered_skills", "compacted_episode_summaries"]
    configs = [
        ("none", {"memory_tokens": 100, "skill_detail": "mild", "episode_detail": "mild"}),
    ]
    if compression_enabled:
        configs.extend([
            ("mild", {"memory_tokens": 80, "skill_detail": "mild", "episode_detail": "mild"}),
            ("aggressive", {"memory_tokens": 40, "skill_detail": "aggressive", "episode_detail": "aggressive"}),
            ("emergency", {"memory_tokens": 18, "skill_detail": "emergency", "episode_detail": "emergency"}),
        ])

    best_parts: List[str] = []
    best_included: List[str] = []
    best_dropped = list(section_order)
    # Default to 'none' when there is no content to compress.
    # 'emergency' means reactive truncation under active token pressure.
    best_level = "none"

    for level, cfg in configs:
        parts: List[str] = []
        included: List[str] = []
        dropped: List[str] = []
        used = 0

        if active:
            lines = [_format_memory(m, max_tokens=cfg["memory_tokens"]) for m in active]
            block = "## Relevant Memories\n" + "\n".join(lines)
            cost = _estimate_block_tokens(block)
            if used + cost <= budget:
                parts.append(block)
                included.append("relevant_memories")
                used += cost
            else:
                fitted_lines: List[str] = []
                for line in lines:
                    candidate = "## Relevant Memories\n" + "\n".join(fitted_lines + [line])
                    if fitted_lines and used + _estimate_block_tokens(candidate) > budget:
                        break
                    if not fitted_lines and used + _estimate_block_tokens(candidate) > budget:
                        break
                    fitted_lines.append(line)
                if fitted_lines:
                    block = "## Relevant Memories\n" + "\n".join(fitted_lines)
                    parts.append(block)
                    included.append("relevant_memories")
                    used += _estimate_block_tokens(block)
                else:
                    dropped.append("relevant_memories")

        if triggered:
            block = "## Triggered Skills\n" + "\n".join(
                _format_skill(s, detail_level=cfg["skill_detail"]) for s in triggered
            )
            cost = _estimate_block_tokens(block)
            if used + cost <= budget:
                parts.append(block)
                included.append("triggered_skills")
                used += cost
            else:
                dropped.append("triggered_skills")

        episode_block = _build_compacted_episode_block(store, detail_level=cfg["episode_detail"])
        if episode_block:
            cost = _estimate_block_tokens(episode_block)
            if used + cost <= budget:
                parts.append(episode_block)
                included.append("compacted_episode_summaries")
                used += cost
            else:
                dropped.append("compacted_episode_summaries")

        missing = [label for label in section_order if label not in included]
        if len(included) > len(best_included):
            best_parts = parts
            best_included = included
            best_dropped = missing
            best_level = level
        if not missing:
            return parts, level, [], included

    return best_parts, best_level, best_dropped, best_included


# ---------------------------------------------------------------------------
# Skill matching
# ---------------------------------------------------------------------------

def _match_triggered_skills(skills, query: str, cap: int = 3) -> List[LoadedSkill]:
    """Match skills by token overlap with query."""
    if not query or not query.strip():
        return []
    q_tokens = set(_tokenise(query))
    if not q_tokens:
        return []

    scored = []
    for skill in skills.list():
        fm = skill.frontmatter
        # Collect trigger text
        texts = [fm.name, fm.description]
        triggers = getattr(fm, "triggers", [])
        if triggers:
            texts.extend(triggers)
        skill_tokens = set()
        for t in texts:
            skill_tokens.update(_tokenise(t))
        if not skill_tokens:
            continue
        overlap = len(q_tokens & skill_tokens)
        if overlap == 0:
            continue
        score = overlap / max(len(q_tokens), len(skill_tokens))
        scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:cap]]


# ---------------------------------------------------------------------------
# Token estimation for context blocks
# ---------------------------------------------------------------------------

def _estimate_block_tokens(text: str) -> int:
    """Rough token estimate for context budgeting."""
    return len(text.encode("utf-8")) // 3
