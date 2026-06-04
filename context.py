"""Memory Palace context assembly for Hermes Agent injection."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from .store import LoadedMemory, LoadedSkill, _tokenise, estimate_tokens
except ImportError:
    import store as _store_mod
    LoadedMemory = _store_mod.LoadedMemory
    LoadedSkill = _store_mod.LoadedSkill
    _tokenise = _store_mod._tokenise
    estimate_tokens = _store_mod.estimate_tokens

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def build_context(store, search, skills, query: str = "", max_tokens: int = 4000) -> str:
    """Build context block for LLM injection.

    Priority:
    1. Pinned memories (always included)
    2. Active memories (zone-based relevance via search)
    3. Triggered skills (per-turn token overlap)
    4. Always-active skills (user-configured)
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

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_memory(mem: LoadedMemory, max_tokens: int = 100) -> str:
    """Format a memory for context injection with token-aware truncation."""
    body = mem.body.strip()
    total = estimate_tokens(body)
    if total > max_tokens:
        ratio = len(body) / max(total, 1)
        char_limit = int(max_tokens * ratio)
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
