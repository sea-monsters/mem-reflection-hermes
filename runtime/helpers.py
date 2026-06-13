"""Helper functions wired into the package namespace for late-bound callers.

These helpers are used by runtime/tools.py and runtime/hooks.py via _lb()
and therefore must remain accessible as attributes of mem_reflection_hermes.
Keeping them in a dedicated module slims __init__.py.
"""
from __future__ import annotations


def _build_context_block(query: str = "") -> str:
    """Build context block using memory.context module."""
    from ..memory.context import build_context_block
    return build_context_block(query)


def _build_context_bundle(
    query: str = "",
    max_tokens: int = 4000,
    stable_only: bool = False,
    filters=None,
):
    """Build structured context bundle using memory.context module."""
    from ..memory.context import build_context_bundle
    # Import singleton getters lazily to avoid circular import during package init.
    from .. import _get_mem_store, _get_search_index, _get_skill_store
    return build_context_bundle(
        _get_mem_store(), _get_search_index(), _get_skill_store(),
        query, max_tokens=max_tokens, stable_only=stable_only, filters=filters,
    )


def _estimate_tokens(text: str) -> int:
    """Estimate token count using store module."""
    from ..core.store import _memory_tokens
    return _memory_tokens(text)


def load_zone_summary(zone: str):
    """Load cached zone summary from zone-cache directory."""
    try:
        from ..core.store import zone_cache_dir, sanitize_zone_filename
        path = zone_cache_dir() / f"{sanitize_zone_filename(zone)}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception:
        pass
    return None


def save_zone_summary(zone: str, content: str):
    """Save zone summary cache to zone-cache directory."""
    try:
        from ..core.store import zone_cache_dir, sanitize_zone_filename
        path = zone_cache_dir() / f"{sanitize_zone_filename(zone)}.md"
        path.write_text(content, encoding="utf-8")
    except Exception:
        pass


def match_skills(skills, query, k=10):
    """Match skills against query using token overlap (Jaccard-like)."""
    from ..core.store import _tokenise
    q_tokens = set(_tokenise(query))
    if not q_tokens:
        return skills[:k]
    scored = []
    for skill in skills:
        fm = skill.frontmatter
        texts = [fm.name, fm.description]
        triggers = getattr(fm, "triggers", [])
        if triggers:
            texts.extend(triggers)
        skill_tokens = set()
        for t in texts:
            skill_tokens.update(_tokenise(t))
        overlap = len(q_tokens & skill_tokens)
        if overlap == 0:
            continue
        score = overlap / max(len(q_tokens), len(skill_tokens))
        scored.append((score, skill))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:k]]
