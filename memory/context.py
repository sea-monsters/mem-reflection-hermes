"""Memory Palace context assembly for Hermes Agent injection."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from ..core.store import LoadedMemory, LoadedSkill, _tokenise, _memory_tokens, plugin_config
except ImportError:
    import sys
    from pathlib import Path
    _repo = Path(__file__).resolve().parent.parent
    import importlib.util
    _spec = importlib.util.spec_from_file_location("store", str(_repo / "core" / "store.py"))
    _store_mod = importlib.util.module_from_spec(_spec)
    sys.modules["store"] = _store_mod
    _spec.loader.exec_module(_store_mod)
    LoadedMemory = _store_mod.LoadedMemory
    LoadedSkill = _store_mod.LoadedSkill
    _tokenise = _store_mod._tokenise
    _memory_tokens = _store_mod._memory_tokens
    plugin_config = _store_mod.plugin_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path helpers (for built-in memory)
# ---------------------------------------------------------------------------

ENTRY_DELIMITER = "\n\u00a7\n"  # Section sign delimiter, matching memory_tool.py


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


def build_context(store, search, skills, query: str = "", max_tokens: int = 4000) -> str:
    """Build context block for LLM injection.

    Priority:
    1. Pinned memories (always included)
    2. Active memories (zone-based relevance via search)
    3. Triggered skills (per-turn token overlap)
    4. Always-active skills (user-configured)
    5. (v1.1) Compacted episode summaries
    """
    parts: List[str] = []
    token_budget = max_tokens
    used_tokens = 0

    # 1. Pinned memories
    pinned = store.list_pinned()
    if pinned:
        block = "## Pinned Memories\n" + "\n".join(_format_memory(m) for m in pinned)
        t = _estimate_block_tokens(block)
        if used_tokens + t <= token_budget:
            parts.append(block)
            used_tokens += t

    # 2. Active memories (search if query provided, else top by rank)
    active: List[LoadedMemory] = []
    if query and query.strip():
        try:
            active = search.search(query, k=10)
        except Exception:
            active = store.list_active()[:10]
    else:
        active = store.list_active()[:10]

    if active:
        block = "## Relevant Memories\n" + "\n".join(_format_memory(m) for m in active)
        t = _estimate_block_tokens(block)
        if used_tokens + t <= token_budget:
            parts.append(block)
            used_tokens += t

    # 3. Triggered skills
    triggered = _match_triggered_skills(skills, query)
    if triggered:
        block = "## Triggered Skills\n" + "\n".join(_format_skill(s) for s in triggered)
        t = _estimate_block_tokens(block)
        if used_tokens + t <= token_budget:
            parts.append(block)
            used_tokens += t

    # 4. Always-active skills
    always = [s for s in skills.list() if getattr(s.frontmatter, "always_active", False)]
    if always:
        block = "## Active Skills\n" + "\n".join(_format_skill(s) for s in always)
        t = _estimate_block_tokens(block)
        if used_tokens + t <= token_budget:
            parts.append(block)
            used_tokens += t

    # 5. (v1.1) Compacted episode summaries
    try:
        cfg = plugin_config()
        if cfg.get("context_compacted_episode", True):
            episode_block = _build_compacted_episode_block(store)
            if episode_block:
                t = _estimate_block_tokens(episode_block)
                if used_tokens + t <= token_budget:
                    parts.append(episode_block)
                    used_tokens += t
    except Exception:
        pass

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# v1.1 context blocks
# ---------------------------------------------------------------------------


def _build_compacted_episode_block(store) -> str:
    """Load compacted episode summaries from the episode zone.

    Searches for entries tagged 'compacted' and formats as a digest.
    """
    try:
        compacted = store.search_by_tags(["compacted"], zone="episode", limit=20)
    except Exception:
        # Fallback: scan episode zone for compacted entries
        try:
            all_ep = store.list_by_zone("episode")
            compacted = [m for m in all_ep if "compacted" in (m.frontmatter.tags or [])]
        except Exception:
            return ""

    if not compacted:
        return ""

    lines = ["## Episode Summaries"]
    for m in compacted[:10]:
        body = m.body.strip()[:200]
        if len(m.body) > 200:
            body += "..."
        lines.append(f"- {body}")
    if len(compacted) > 10:
        lines.append(f"- ... ({len(compacted) - 10} more)")
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

    lines = [f"- [{mem.frontmatter.zone or 'general'}] {body}"]
    tags = getattr(mem.frontmatter, "tags", None)
    if tags:
        lines.append(f"  tags: {', '.join(tags)}")
    return "\n".join(lines)


def _format_skill(skill: LoadedSkill) -> str:
    """Format a skill for context injection."""
    fm = skill.frontmatter
    lines = [f"### {fm.name}", fm.description.strip()[:300]]
    triggers = getattr(fm, "triggers", None)
    if triggers:
        lines.append(f"triggers: {', '.join(triggers)}")
    return "\n".join(lines)


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
