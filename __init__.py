"""mem-reflection-hermes plugin -- Self-evolving memory and reflection system.

v1.0-beta2 Runtime Architecture (~3,200 LOC across 6 modules + dashboard):
- store.py: SQLite-backed MemoryStore, Markdown cold storage, token estimation, CJK tokenizer
- search.py: Three-layer retrieval (Recall → RRF/Weighted Fusion → Rerank), embedding engine
- graph.py: GraphIndex -- Hebbian edges, spreading activation, PageRank, cross-zone analysis
- reflect.py: ReflectionEngine -- raw_chunk default, heuristic/LLM optional modes
- context.py: Context assembly -- Palace mode, skill matching, token-aware truncation
- __init__.py: Plugin registration, backward compat, runtime singletons
- dashboard/: FastAPI CRUD + graph visualization endpoints

Legacy modules (deprecated, scheduled for removal in beta3):
- core.py, late_binding.py, search/embed.py, reflection/engine.py, hooks/lifecycle.py,
  tools/handlers.py, graph/ahe_graph.py, graph/cluqi.py, graph/pagerank.py,
  graph/cross_zone.py, query/cache.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import queue
import re
import sys
import threading
import time
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Import from submodules: models, constants, BM25, frontmatter, IO
from .core import (  # noqa: F401
    hermes_home, load_config, plugin_config, plugin_data_dir,
    user_memories_dir, project_memories_dir, user_skills_dir, project_skills_dir,
    embeddings_enabled, micro_reflection_enabled, palace_mode_enabled, profile_mode_enabled,
    normalize_zone, is_valid_zone, fast_hash,
    palace_index_path, zone_cache_dir, sanitize_zone_filename,
    MemoryFrontmatter, LoadedMemory, MemoryStatEntry, MemoryEffectiveness,
    SkillFrontmatter, LoadedSkill,
    parse_frontmatter, serialize_frontmatter, read_memory,
    async_write_memory, record_memory_stat, batch_record_stats, load_effectiveness,
    is_cjk, cjk_ratio, adaptive_conflict_threshold,
    _tokenise, _memory_tokens, _bm25_search, _bm25_search_scored, _cosine_similarity,
    _ZONE_CORE, _ZONE_WORK, _ZONE_EPISODE, _ZONE_GENERAL,
    _VALID_ZONES, _PROJECT_ZONE_PREFIX,
    _ZONE_SPLIT_THRESHOLD, _ZONE_MERGE_THRESHOLD,
    _write_queue, _pending_writes, _write_path_lock, _cancel_pending_write, _write_memory,
    _lineage_latest, _lineage_root, _lineage_depth, _lineage_cycle_check,
    _classify_update_intent, _is_expired, _is_context_mismatch,
)

# Backward-compat aliases (old underscore names used by remaining __init__.py code)
_hermes_home = hermes_home
_load_config = load_config
_get_config = plugin_config
_plugin_data_dir = plugin_data_dir
_user_memories_dir = user_memories_dir
_project_memories_dir = project_memories_dir
_user_skills_dir = user_skills_dir
_project_skills_dir = project_skills_dir
_embeddings_enabled = embeddings_enabled
_micro_reflection_enabled = micro_reflection_enabled
_palace_mode_enabled = palace_mode_enabled
_profile_mode_enabled = profile_mode_enabled
_palace_index_path = palace_index_path
_zone_cache_dir = zone_cache_dir
_sanitize_zone_filename = sanitize_zone_filename
_read_memory = read_memory
_normalize_zone = normalize_zone
_fast_hash = fast_hash
_async_write_memory = async_write_memory
_batch_record_stats = batch_record_stats
_stats_path = lambda: plugin_data_dir() / "memory-stats.jsonl"

def _palace_instructions_enabled() -> bool:
    return bool(plugin_config().get("palace_instructions", True))

def _active_memory_cap() -> int:
    return int(plugin_config().get("active_memory_index_cap", 20))

def _skill_index_cap() -> int:
    return int(plugin_config().get("skill_index_cap", 20))

def _relevant_memory_cap() -> int:
    return int(plugin_config().get("relevant_memory_cap", 5))

def _triggered_skill_cap() -> int:
    return int(plugin_config().get("triggered_skill_cap", 3))

logger = logging.getLogger(__name__)

# Register module in sys.modules early to avoid dataclass resolution failure
# when loaded via importlib.util (Python 3.11 bug workaround).
if __name__ != "__main__" and __name__ not in sys.modules:
    import types
    # Fallback chain: __spec__.name → __name__ → create fresh module
    mod_name = getattr(__spec__, "name", None) if "__spec__" in globals() else None
    if mod_name is None:
        mod_name = __name__
    # Register the current executing module object, not a placeholder
    sys.modules[mod_name] = sys.modules.get(mod_name) or sys.modules.get(__name__) or types.ModuleType(mod_name)
else:
    mod_name = __name__

# Alias bare module name so importlib.import_module("mem_reflection_hermes.*")
# works when Hermes loaded us as "hermes_plugins.mem_reflection_hermes".
_bare_name = "mem_reflection_hermes"
if _bare_name not in sys.modules and mod_name != _bare_name:
    sys.modules[_bare_name] = sys.modules[mod_name]
    # Copy __path__ so submodule imports (mem_reflection_hermes.graph.*) resolve correctly
    _real_path = getattr(sys.modules[mod_name], "__path__", None)
    if _real_path is not None:
        sys.modules[_bare_name].__path__ = _real_path

# ---------------------------------------------------------------------------
# AI instruction docstring (injected into register() palace_instructions)
# ---------------------------------------------------------------------------
# CJK-aware token estimation (mirrors small-rust-hermes compaction.rs)
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Estimate token count with CJK awareness (fast bytes-based, P1-1).

    CJK/Unicode text → ~3 bytes per token.
    ASCII text → ~4 bytes per token.
    The hybrid byte-count approach is ~600x faster than char-by-char CJK range checks
    while staying within ±15% of tiktoken cl100k_base for mixed CJK+English text.
    """
    if not text:
        return 0
    encoded = text.encode("utf-8")
    n_bytes = len(encoded)
    # Fast path: mostly ASCII text
    if n_bytes <= len(text) * 1.2:
        return (n_bytes + 3) // 4
    # Mixed CJK: UTF-8 multi-byte characters use 3 bytes each → ~1.5 chars/token
    return (n_bytes + 2) // 3


_PALACE_USAGE_INSTRUCTIONS = """## Memory Palace
Your persistent memory is organized in a Memory Palace with zones.
- Use `srh_palace_zones` to see available zones and their counts
- Use `srh_palace_read_zone` to load all memories from a specific zone
- Use `srh_palace_recall` to search by topic, optionally scoped to a zone
- Use `srh_memory_write` (with zone parameter) to persist new learnings
- Use `srh_memory_delete` to remove outdated memories
Do NOT save task progress, session outcomes, or temporary TODO state here.
Session summaries go to `episode` zone. User identity/preferences go to `core`.
Current work focus goes to `work`. Everything else goes to `general`.
Don't guess about preferences or conventions — load the relevant zone first."""

# ---------------------------------------------------------------------------
# Palace Index & Zone Cache
# ---------------------------------------------------------------------------

def build_palace_index(memories: List[LoadedMemory]) -> str:
    """Generate a code-based palace index (no LLM needed).

    Groups memories by zone, shows counts and first-line previews.
    If ahe_graph is active, appends intra-zone graph cluster density.
    Typically ~200-400 tokens.
    """
    groups: Dict[str, List[LoadedMemory]] = {}
    for m in memories:
        groups.setdefault(m.frontmatter.zone, []).append(m)

    if not groups:
        return "## Memory Palace\nEmpty — no memories yet."

    total = len(memories)
    buf = f"## Memory Palace\n{total} memories across {len(groups)} zones. Use srh_palace_read_zone to load details.\n"

    # ── Scheme B: Query graph density per zone ────────────────
    intra_zone_edges: Dict[str, int] = {}
    try:
        gm = _get_graph_mgr()
        if gm is not None:
            for zone, mems in groups.items():
                mids = set(m.id() for m in mems)
                edge_count = 0
                for m in mems:
                    neighbors = gm.store.get_neighbors(m.id(), min_weight=0.1, limit=50)
                    for n in neighbors:
                        if n["memory_id"] in mids:
                            edge_count += 1
                # Each edge counted twice (once per direction), divide by 2
                intra_zone_edges[zone] = edge_count // 2
    except Exception as e:
        logger.warning("build_palace_index graph annotation failed: %s", e)

    # Zones in consistent order: core > work > episode > general > custom
    zone_order = ["core", "work", "episode", "general"]
    sorted_zones = sorted(groups.keys(), key=lambda z: (
        (zone_order.index(z), z) if z in zone_order else (99, z)
    ))

    for zone in sorted_zones:
        mems = groups[zone]
        # Re-sort: core/work zone zones by predefined order
        if zone in zone_order:
            idx = zone_order.index(zone)
        else:
            idx = 99
        # Append graph cluster annotation if dense
        edge_count = intra_zone_edges.get(zone, 0)
        cluster_tag = ""
        if edge_count >= 2:
            cluster_tag = f"  [关联簇: {edge_count}个连接]"
        buf += f"\n### {zone} ({len(mems)}){cluster_tag}\n"
        for m in mems[:5]:
            line = m.body.split("\n")[0].strip()[:80]
            buf += f"- {line}\n"
        if len(mems) > 5:
            buf += f"- ... ({len(mems) - 5} more)\n"
    return buf


def load_zone_summary(zone: str) -> Optional[str]:
    """Load a cached zone summary if available."""
    safe = _sanitize_zone_filename(zone)
    path = _zone_cache_dir() / f"{safe}.md"
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    return content or None


def save_zone_summary(zone: str, content: str) -> Path:
    """Save a zone summary atomically (tmp + rename)."""
    safe = _sanitize_zone_filename(zone)
    d = _zone_cache_dir()
    path = d / f"{safe}.md"
    tmp = d / f".{safe}.md.tmp"
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return path


def _user_memories_dir() -> Path:
    """User-level memories directory."""
    return _hermes_home() / "memories"


def _project_memories_dir() -> Optional[Path]:
    """Project-level memories directory (only if .hermes/ exists in cwd)."""
    p = Path.cwd() / ".hermes" / "memories"
    if not p.exists():
        return None
    # Guard: if project memories dir resolves to the same path as user memories,
    # return None to avoid duplicate scanning (happens when cwd is ~).
    user = _user_memories_dir()
    if p.resolve() == user.resolve():
        return None
    return p


# ---------------------------------------------------------------------------

def _read_skill(path: Path, scope: str) -> Optional[LoadedSkill]:
    try:
        raw = path.read_text(encoding="utf-8")
        data, body = parse_frontmatter(raw)
        fm = SkillFrontmatter(
            name=data.get("name", path.parent.name),
            description=data.get("description", ""),
            triggers=data.get("triggers", []),
            version=data.get("version"),
            license=data.get("license"),
            always_active=bool(data.get("always_active", False)),
        )
        return LoadedSkill(frontmatter=fm, body=body.strip(), source_path=path, scope=scope)
    except Exception as e:
        logger.warning("Failed to read skill %s: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# Skill matcher (token overlap + optional embedding)
# ---------------------------------------------------------------------------

_MIN_SKILL_TOKEN = 3
_TOKEN_WEIGHT = 0.4
_EMBED_WEIGHT = 0.6


def _skill_tokenise(s: str) -> Set[str]:
    return {
        t for t in re.split(r"[^a-z0-9]+", s.lower())
        if len(t) >= _MIN_SKILL_TOKEN
    }


def _skill_bag(s: LoadedSkill) -> Set[str]:
    bag: Set[str] = set()
    for t in s.frontmatter.triggers:
        bag.update(_skill_tokenise(t))
    bag.update(_skill_tokenise(s.frontmatter.name))
    bag.update(_skill_tokenise(s.frontmatter.description))
    return bag


def match_skills(skills: List[LoadedSkill], query: str, k: int = 3) -> List[LoadedSkill]:
    q = _skill_tokenise(query)
    if not q:
        return []
    scored: List[Tuple[float, int, LoadedSkill]] = []
    for s in skills:
        bag = _skill_bag(s)
        raw_token = len(q & bag)
        if raw_token == 0:
            continue
        score = raw_token
        scored.append((score, len(s.frontmatter.triggers), s))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [s for _, _, s in scored[:k]]

# ---------------------------------------------------------------------------
# Reflection runner
# ---------------------------------------------------------------------------
# Plugin state
# ---------------------------------------------------------------------------

_mem_store: Optional[MemoryStore] = None
_skill_store: Optional[SkillStore] = None
_turns_since_reflect: int = 0
_micro_reflect_queue: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Context assembly (Pinned → Active Index → Triggered Skills)
# ---------------------------------------------------------------------------

def _build_context_block(query: str = "") -> str:
    """Build the memory context block injected into the user message.

    Three modes (checked in priority order):
    1. Palace mode: inject palace index (zone map), agent uses tools for retrieval
    2. Profile mode: inject compiled profile.md if available, no per-turn injection
    3. Legacy mode: pinned + active index + per-turn TF-IDF relevance injection

    Note (P2-22): Entire function is wrapped in try/except so a failure in any
    stage degrades gracefully to empty context rather than failing the hook.
    """
    try:
        return _build_context_block_inner(query)
    except Exception as e:
        logger.warning("Context block build failed: %s", e, exc_info=True)
        return ""


def _build_context_block_inner(query: str = "") -> str:
    mem_store = _get_mem_store()
    skill_store = _get_skill_store()
    parts: List[str] = []
    stat_entries: List[Tuple[str, str]] = []  # Batch collect (id, event)

    # Determine mode — cache config lookups
    palace_mode = _palace_mode_enabled()
    profile_mode = _profile_mode_enabled()

    # Pre-load skills once (used in palace index, triggered, always-active)
    all_skills = skill_store.list()

    # ---- Mode 1: Palace (zone-based, tool-driven retrieval) ----
    if palace_mode:
        active = mem_store.list_active()
        if active:
            # P0-1+P2-1: write-on-change + event-driven rebuild
            if mem_store._index_dirty:
                index = build_palace_index(active)
                h = _fast_hash(index)
                if h != mem_store._last_index_hash:
                    _palace_index_path().parent.mkdir(parents=True, exist_ok=True)
                    _palace_index_path().write_text(index, encoding="utf-8")
                    mem_store._last_index_hash = h
                mem_store._index_dirty = False
                mem_store._cached_index = index  # Cache built string
            else:
                # Reuse cached index (don't rebuild, don't write)
                index = mem_store._cached_index
            parts.append(index)
            for m in active:
                stat_entries.append((m.id(), "loaded"))
        else:
            parts.append("## Memory Palace\nEmpty — no memories yet.")

        if _palace_instructions_enabled():
            parts.append(_PALACE_USAGE_INSTRUCTIONS)

        cap = _skill_index_cap()
        if all_skills:
            parts.append("\n## Available skills")
            for s in all_skills[:cap]:
                parts.append(f"- {s.frontmatter.name}: {s.frontmatter.description}")
            if len(all_skills) > cap:
                parts.append(f"- ... ({len(all_skills) - cap} more)")

    # ---- Mode 2: Compiled Profile (LLM-compiled, all-in-one) ----
    elif profile_mode:
        profile_path = _plugin_data_dir() / "profile.md"
        if profile_path.exists():
            profile = profile_path.read_text(encoding="utf-8").strip()
            if profile:
                parts.append("## User Profile\n")
                parts.append(profile)

        if not parts:
            pinned = mem_store.list_pinned()
            if pinned:
                parts.append("=== Pinned memories (always relevant) ===")
                for m in pinned:
                    parts.append(f"- [{m.id()}] {m.body[:200]}")
                    stat_entries.append((m.id(), "loaded"))
                parts.append("")

    # ---- Mode 3: Legacy (pinned + active index + per-turn TF-IDF) ----
    else:
        pinned = mem_store.list_pinned()
        if pinned:
            parts.append("=== Pinned memories (always relevant) ===")
            for m in pinned:
                parts.append(f"- [{m.id()}] {m.body[:200]}")
                stat_entries.append((m.id(), "loaded"))
            parts.append("")

        if query:
            active = mem_store.search(query, k=_relevant_memory_cap())
        else:
            active = mem_store.list_active()[:_active_memory_cap()]
        if active:
            parts.append("=== Relevant memories ===")
            for m in active:
                if m not in pinned:
                    parts.append(f"- [{m.id()}] {m.body[:200]}")
                    stat_entries.append((m.id(), "loaded"))
            parts.append("")

    # Triggered skills (legacy/profile fallback)
    if not palace_mode or (palace_mode and not parts):
        if query:
            skills = match_skills(all_skills, query, k=_triggered_skill_cap())
        else:
            skills = []
        if skills:
            parts.append("=== Triggered skills ===")
            for s in skills:
                parts.append(f"- {s.frontmatter.name}: {s.frontmatter.description}")
            parts.append("")

    # Always-active skills (all modes)
    always_active = [s for s in all_skills if s.frontmatter.always_active]
    if always_active:
        parts.append("\n## Always-Active Skills\n")
        for s in always_active:
            parts.append(f"### {s.frontmatter.name}\n{s.body.strip()}\n")

    # Flush all stat entries in one file open
    if stat_entries:
        _batch_record_stats(stat_entries)

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Register slash commands
# ---------------------------------------------------------------------------
def _register_slash_commands(ctx):
    """Register all slash commands with the Hermes context."""
    ctx.register_command(
        name="reflect",
        handler=lambda raw: _slash_reflect(raw),
        description="Trigger a full reflection on the current session",
        args_hint="",
    )
    ctx.register_command(
        name="pending-skills",
        handler=lambda raw: _slash_pending_skills(raw),
        description="Show pending skill candidates awaiting approval",
        args_hint="",
    )
    ctx.register_command(
        name="approve-skill",
        handler=lambda raw: _slash_approve_skill(raw),
        description="Approve a pending skill candidate by ID",
        args_hint="<pending_id>",
    )
    ctx.register_command(
        name="reject-skill",
        handler=lambda raw: _slash_reject_skill(raw),
        description="Reject a pending skill candidate by ID",
        args_hint="<pending_id> [reason]",
    )
    ctx.register_command(
        name="memories",
        handler=lambda raw: _slash_memories(raw),
        description="List active memories",
        args_hint="[query]",
    )
    ctx.register_command(
        name="skills",
        handler=lambda raw: _slash_skills(raw),
        description="List or search skills",
        args_hint="[query]",
    )
    ctx.register_command(
        name="compile-profile",
        handler=lambda raw: _slash_compile_profile(raw),
        description="Compile all memories into a structured profile via LLM",
        args_hint="[profile|palace_index|zone]",
    )

    logger.info("mem-reflection-hermes plugin registered")

    # ── ahe_graph integration (v0.6.1+) ───────────────────────
    try:
        try:
            from .ahe_graph import get_graph_manager as _get_gm
        except ImportError:
            logger.debug("Relative import of ahe_graph failed (expected for standalone plugin load), trying importlib fallback")
            # Standalone plugin load: __package__ may be empty, so relative
            # import fails. Fallback to importlib-based absolute load.
            import importlib.util as _iutil
            import sys as _sys
            _ahe_path = str(Path(__file__).parent / "ahe_graph" / "__init__.py")
            if not Path(_ahe_path).exists():
                logger.warning("ahe_graph module not found at %s — graph features disabled", _ahe_path)
                _get_gm = None  # type: ignore
            else:
                _ahe_spec = _iutil.spec_from_file_location(
                    "mem_reflection_hermes.ahe_graph", _ahe_path
                )
                if _ahe_spec is None or _ahe_spec.loader is None:
                    logger.warning("ahe_graph spec could not be loaded — graph features disabled")
                    _get_gm = None  # type: ignore
                else:
                    try:
                        _ahe_mod = _iutil.module_from_spec(_ahe_spec)
                        _sys.modules["mem_reflection_hermes.ahe_graph"] = _ahe_mod
                        _ahe_spec.loader.exec_module(_ahe_mod)  # type: ignore
                        _get_gm = _ahe_mod.get_graph_manager
                        logger.info("ahe_graph loaded successfully via importlib fallback")
                    except Exception as _ahe_err:
                        logger.warning(
                            "ahe_graph loaded but raised %s: %s — graph features disabled",
                            type(_ahe_err).__name__, _ahe_err,
                        )
                        _get_gm = None  # type: ignore

        # ── Common graph setup (runs regardless of import path) ──
        # Lazy-init on first use
        _graph_db_dir = hermes_home() / "plugins" / "mem-reflection-hermes"
        # Set module-level globals so _on_session_end and _get_graph_neighbors
        # use the same singleton (P1-2, P2-1)
        global _gm_getter_func, _gm_getter_path
        _gm_getter_func = _get_gm
        _gm_getter_path = _graph_db_dir
        _gm_ref = {"instance": None}
        _gm_lock = threading.Lock()

        def _ensure_gm():
            if _gm_ref["instance"] is None:
                with _gm_lock:
                    if _gm_ref["instance"] is None:
                        _gm_ref["instance"] = _get_gm(_graph_db_dir)
            return _gm_ref["instance"]

        # --- tool: srh_associate ---
        MAX_ASSOCIATION_IDS = 20

        def _graph_associate_h(args: dict, **kwargs) -> str:
            gm = _ensure_gm()
            mids = args.get("memory_ids", [])[:MAX_ASSOCIATION_IDS]
            # HIGH-10: validate memory IDs exist before creating graph edges
            mem_store = _get_mem_store()
            valid_mids = [mid for mid in mids if mem_store.get(mid) is not None]
            if len(valid_mids) < 2:
                return json.dumps({"error": "At least 2 valid memory IDs required", "valid_ids": valid_mids})
            ctx_str = args.get("context", "")
            rel = args.get("relation", "co_occurs")
            result = gm.associate_memories(valid_mids, ctx_str, rel)
            return json.dumps({**result, "validated_ids": valid_mids})

        ctx.register_tool(
            name="srh_associate",
            toolset="mem_reflection_hermes",
            schema={
                "name": "srh_associate",
                "description": "Create graph associations between memories. Records Hebbian co-occurrence edges so related memories activate each other during retrieval.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of memory IDs to associate (max 20)",
                            "minItems": 2,
                            "maxItems": 20,
                        },
                        "relation": {
                            "type": "string",
                            "enum": ["co_occurs", "co_used_in_task"],
                            "description": "Relation type: co_occurs (stronger) or co_used_in_task (weaker)",
                            "default": "co_occurs",
                        },
                    },
                    "required": ["memory_ids"],
                },
            },
            handler=_graph_associate_h,
            description="Associate memories via graph edges",
            emoji="🔗",
        )

        # --- tool: srh_graph_retrieve ---
        def _graph_retrieve_h(args: dict, **kwargs) -> str:
            gm = _ensure_gm()
            mids = args.get("memory_ids", [])[:20]
            task_type = args.get("task_type", "reasoning")
            max_res = min(args.get("max_results", 10), 100)
            tier = args.get("tier", "list")
            # Auto-detect strategy from seed memory zones when task_type is default
            if task_type == "reasoning" and mids and gm.store:
                try:
                    zones = set()
                    for mid in mids:
                        meta = gm.store.get_meta(mid)
                        if meta and meta.get("zone"):
                            zones.add(meta.get("zone"))
                    # Map seed zones to best strategy
                    zone_strategy_map = {
                        "core": "factual",
                        "work": "reasoning",
                        "episode": "recent",
                        "general": "exploration",
                    }
                    for z in zones:
                        inferred = zone_strategy_map.get(z)
                        if inferred:
                            task_type = inferred
                            break
                except Exception:
                    pass  # fallback to "reasoning"
            results = gm.retrieve_related(mids, task_type, max_res, tier=tier)
            return json.dumps({"results": results, "count": len(results), "seed_ids": mids, "tier": tier, "strategy": task_type})

        ctx.register_tool(
            name="srh_graph_retrieve",
            toolset="mem_reflection_hermes",
            schema={
                "name": "srh_graph_retrieve",
                "description": "Retrieve associative memories via co-activation propagation. Given seed memory IDs, finds related memories through associative (Hebbian co-occurrence) graph edges. Edges indicate 'used together', not factual entity relationships. Progressive tiers: tier='count' (minimal), 'list' (summary, default), 'detail' (full with depth).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Seed memory IDs to start graph traversal from (max 20)",
                            "minItems": 1,
                            "maxItems": 20,
                        },
                        "task_type": {
                            "type": "string",
                            "enum": ["factual", "reasoning", "skill", "recent", "exploration", "personalized"],
                            "description": "Retrieval strategy type",
                            "default": "reasoning",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max results to return",
                            "default": 10,
                            "minimum": 1,
                            "maximum": 100,
                        },
                        "tier": {
                            "type": "string",
                            "enum": ["count", "list", "detail"],
                            "description": "Progressive disclosure tier: 'count' = minimal, 'list' = summary (default), 'detail' = full propagation info",
                            "default": "list",
                        },
                    },
                    "required": ["memory_ids"],
                },
            },
            handler=_graph_retrieve_h,
            description="Retrieve graph-related memories",
            emoji="🕸️",
        )

        # --- tool: srh_graph_stats ---
        def _graph_stats_h(args: dict, **kwargs) -> str:
            gm = _ensure_gm()
            stats = gm.get_stats()
            stats["graph_semantics"] = "associative_coactivation"
            return json.dumps(stats)

        ctx.register_tool(
            name="srh_graph_stats",
            toolset="mem_reflection_hermes",
            schema={
                "name": "srh_graph_stats",
                "description": "Get associative graph statistics: node count, co-activation edge count, average edge weight, database path. The graph represents Hebbian co-occurrence (memories used together), not factual entity relationships.",
                "parameters": {"type": "object", "properties": {}},
            },
            handler=_graph_stats_h,
            description="Get graph memory statistics",
            emoji="📊",
        )

        # ── P2-3: srh_graph_viz — graph visualization data ──
        def _graph_viz_h(args: dict, **kwargs) -> str:
            """Return full graph data for dashboard visualization."""
            gm = _ensure_gm()
            tier = args.get("tier", "summary")
            stats = gm.get_stats(tier="detail")
            if stats.get("node_count", 0) == 0:
                return json.dumps({"nodes": [], "edges": [], "stats": stats})
            try:
                with gm.store._connect() as conn:
                    nodes = conn.execute(
                        "SELECT id, zone, importance, strength, status, access_count FROM graph_memory_meta "
                        "WHERE strength > 0 ORDER BY importance DESC LIMIT 200"
                    ).fetchall()
                    edges = conn.execute(
                        "SELECT source_id, target_id, relation, weight FROM graph_edges "
                        "WHERE weight >= 0.1 ORDER BY weight DESC LIMIT 500"
                    ).fetchall()
                    return json.dumps({
                        "nodes": [dict(r) for r in nodes],
                        "edges": [dict(r) for r in edges],
                        "stats": {**stats, "graph_semantics": "associative_coactivation"},
                    })
            except Exception as e:
                return json.dumps({"error": str(e), "stats": stats})

        ctx.register_tool(
            name="srh_graph_viz",
            toolset="mem_reflection_hermes",
            schema={
                "name": "srh_graph_viz",
                "description": "Get full associative graph visualization data (nodes + edges) for dashboard rendering. tier='summary' returns counts only; 'detail' returns full node/edge lists. Graph semantics: associative_coactivation (Hebbian co-occurrence edges, not factual entity relations).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tier": {"type": "string", "enum": ["summary", "detail"], "default": "summary"},
                    },
                },
            },
            handler=_graph_viz_h,
            description="Graph viz data for dashboard",
            emoji="🕸️",
        )

        # --- tool: srh_memory_health (WS-5) ---
        def _memory_health_h(args: dict, **kwargs) -> str:
            store = _get_mem_store()
            metrics = store.health_metrics()
            return json.dumps({"health": metrics, "recommendations": _health_recommendations(metrics)})

        def _health_recommendations(metrics: Dict[str, Any]) -> List[str]:
            recs = []
            if metrics.get("duplicate_clusters", 0) > 0:
                recs.append(f"Review {metrics['duplicate_clusters']} duplicate memory cluster(s).")
            if metrics.get("longest_supersedes_chain", 0) > 5:
                recs.append(f"Longest supersedes chain ({metrics['longest_supersedes_chain']}) is deep; consider consolidation.")
            if metrics.get("supersedes_cycle_count", 0) > 0:
                recs.append(f"Found {metrics['supersedes_cycle_count']} cycle(s) in supersedes chains — fix immediately.")
            if metrics.get("stale_high_rank_count", 0) > 0:
                recs.append(f"{metrics['stale_high_rank_count']} superseded memories still have high rank; consider re-ranking.")
            if metrics.get("expired_count", 0) > 0:
                recs.append(f"{metrics['expired_count']} memories have passed their valid_until date.")
            if not recs:
                recs.append("Memory store looks healthy.")
            return recs

        ctx.register_tool(
            name="srh_memory_health",
            toolset="mem_reflection_hermes",
            schema={
                "name": "srh_memory_health",
                "description": "Get memory health metrics: duplicate clusters, longest supersedes chain, cycle count, stale high-rank memories, expired memories, and reflection acceptance rate. Returns actionable recommendations.",
                "parameters": {"type": "object", "properties": {}},
            },
            handler=_memory_health_h,
            description="Get memory health metrics and recommendations",
            emoji="🏥",
        )

        # --- hook: auto-associate on memory write ---
        def _post_tool_associate(**kwargs) -> None:
            try:
                tool_name = kwargs.get("tool_name", "")
                if tool_name not in ("srh_memory_write", "srh_memory_delete"):
                    return None

                gm = _ensure_gm()
                args = kwargs.get("args", {})
                result = kwargs.get("result", {})

                if tool_name == "srh_memory_write":
                    # result may be raw JSON string (srh_memory_write returns json.dumps)
                    if isinstance(result, str):
                        try:
                            result = json.loads(result)
                        except json.JSONDecodeError:
                            result = {}
                    # Guard: only create graph metadata for successful writes
                    if not result.get("success") or not result.get("id"):
                        return None
                    memory_id = result.get("id")
                    if not memory_id:
                        return None
                    zone = args.get("zone", "general")
                    gm.store.ensure_meta(memory_id, zone=zone)
                    gm.store.record_access(memory_id)

                    # ── Scheme A-1: Supersedes-aware edge migration ──────────
                    supersedes_ids = args.get("supersedes", [])
                    if supersedes_ids and isinstance(supersedes_ids, list):
                        for old_id in supersedes_ids:
                            # Mark old memory as inactive in graph
                            gm.store.update_importance(old_id, delta=-0.9)
                            conn = gm.store._connect()
                            conn.execute(
                                "UPDATE graph_memory_meta SET strength=0, status='superseded' WHERE id=?",
                                (old_id,)
                            )
                            # Migrate old edges to new memory (weight * 0.3)
                            old_edges = gm.store.get_edges(old_id)
                            for edge in old_edges:
                                src, tgt = edge["source_id"], edge["target_id"]
                                neigh = tgt if src == old_id else src
                                rel = edge.get("relation", "co_occurs")
                                old_w = edge.get("weight", 0.5)
                                # Copy edge from new memory to neighbor with decayed weight
                                gm.store.set_edge_weight(memory_id, neigh, relation=rel,
                                                     weight=old_w * 0.3)
                            conn.commit()
                elif tool_name == "srh_memory_delete":
                    # Clean up graph metadata and edges for this memory
                    mem_id = args.get("id", "")
                    if mem_id:
                        # ── Scheme A-2: Soft-delete (mark inactive) instead of hard delete ──
                        gm.store.update_importance(mem_id, delta=-0.9)
                        conn = gm.store._connect()
                        conn.execute(
                            "UPDATE graph_memory_meta SET strength=0, status='deleted' WHERE id=?",
                            (mem_id,)
                        )
                        conn.commit()
                        # Decay connected edges heavily
                        gm.store.decay_edges(decay_rate=0.9)
            except Exception as e:
                logger.debug("ahe_graph auto-associate: %s", e)
            return None

        ctx.register_hook("post_tool_call", _post_tool_associate)

        # --- slash command ---
        def _slash_graph(raw_args: str) -> str:
            gm = _ensure_gm()
            parts = raw_args.strip().split()
            cmd = parts[0] if parts else "stats"

            if cmd == "stats":
                s = gm.get_stats(tier="detail")
                return (
                    f"📊 **Graph Memory Stats**\n"
                    f"- Nodes: {s['node_count']}\n"
                    f"- Edges: {s['edge_count']}\n"
                    f"- Avg Weight: {s['avg_weight']}\n"
                    f"- DB: {s['db_path']}"
                )
            elif cmd == "decay":
                gm.run_decay()
                return "🧹 Decay cycle completed on all graph edges and memory strengths."
            elif cmd == "associate" and len(parts) >= 3:
                mids = parts[1:]
                r = gm.associate_memories(mids)
                return f"🔗 Associated {len(mids)} memories ({r['edges_created']} edges created/updated)"
            else:
                return "Usage: /graph [stats|decay|associate <id1> <id2> ...]"

        ctx.register_command(
            name="graph",
            handler=_slash_graph,
            description="Graph memory operations: stats, decay, associate",
            args_hint="[stats|decay|associate <id1> <id2> ...]",
        )

        logger.info("ahe_graph integration registered (v0.6.1+)")
    except ImportError as e:
        logger.warning("ahe_graph not available (skip integration): %s", e)
        # HIGH-7: ensure post_tool_call hook is always registered
        ctx.register_hook("post_tool_call", lambda **kwargs: None)
    except Exception as e:
        logger.warning("ahe_graph integration error: %s", e)
        # HIGH-7: ensure post_tool_call hook is always registered
        ctx.register_hook("post_tool_call", lambda **kwargs: None)


def _get_mem_store() -> MemoryStore:
    """Return the package-level memory store; keep this before star imports."""
    global _mem_store
    if _mem_store is None:
        _mem_store = MemoryStore(_user_memories_dir(), _project_memories_dir())
    return _mem_store


def _get_skill_store() -> SkillStore:
    """Return the package-level skill store; keep this before star imports."""
    global _skill_store
    if _skill_store is None:
        _skill_store = SkillStore(_user_skills_dir(), _project_skills_dir())
    return _skill_store


# Keep package-native helpers before star imports from submodules add wrappers
# with the same names.
_package_get_mem_store = _get_mem_store
_package_get_skill_store = _get_skill_store
_package_build_context_block = _build_context_block
_package_normalize_zone = _normalize_zone
_package_micro_reflection_enabled = _micro_reflection_enabled
_package_estimate_tokens = _estimate_tokens

# Initialize globals set by _register_slash_commands (H1)
_gm_getter_func = None
_gm_getter_path = None


# Tool handlers extracted to tools/handlers.py

# Sub-module imports
from .reflection.engine import *  # noqa: F401, F403
from .tools.handlers import *  # noqa: F401, F403
from .hooks.lifecycle import *  # noqa: F401, F403
from .search.embed import _embed_single, _cosine_sim  # noqa: F401

# engine.py's __all__ exports _get_mem_store/_get_skill_store, so import *
# overwrites the root-native versions. Restore them here to prevent recursive
# late-binding (engine._get_mem_store → from mem_reflection_hermes import → engine._get_mem_store).
_get_mem_store = _package_get_mem_store
_get_skill_store = _package_get_skill_store
_build_context_block = _package_build_context_block
_normalize_zone = _package_normalize_zone
_micro_reflection_enabled = _package_micro_reflection_enabled
_estimate_tokens = _package_estimate_tokens

def _reflection_mode() -> str:
    return plugin_config().get("reflection_mode", "raw_chunk")  # W2: default to raw_chunk


def register(ctx) -> None:
    """Register all Hermes plugin tools, hooks, slash commands, and graph tools."""
    from .hooks import lifecycle as _hooks_mod
    from .tools import handlers as _tools_mod

    if hasattr(_hooks_mod, "_set_plugin_context"):
        _hooks_mod._set_plugin_context(ctx)
    _tools_mod.register(ctx)
    _register_slash_commands(ctx)

    # Forward graph manager config from __init__ to lifecycle module
    # so _get_graph_mgr sees the same singleton (P2)
    if _gm_getter_func is not None:
        _hooks_mod._gm_getter_func = _gm_getter_func
        _hooks_mod._gm_getter_path = _gm_getter_path


# ---------------------------------------------------------------------------
# Runtime services
# ---------------------------------------------------------------------------

from . import store as _storage_module      # noqa: E402
from . import search as _search_module      # noqa: E402
from . import graph as _graph_module        # noqa: E402
from . import reflect as _reflection_module # noqa: E402
from . import context as _context_module    # noqa: E402

_memory_store = None
_search_index = None
_graph_index = None
_reflection_engine = None


def _get_indexed_mem_store():
    """Get the SQLite-indexed memory store."""
    global _memory_store
    if _memory_store is None:
        _memory_store = _storage_module.MemoryStore(
            _storage_module.user_memories_dir(),
            _storage_module.project_memories_dir(),
        )
        _memory_store.set_graph(_get_graph_index())
    return _memory_store


def _get_search_index():
    """Get the memory retrieval index."""
    global _search_index
    if _search_index is None:
        _search_index = _search_module.SearchIndex(
            _get_indexed_mem_store(),
            graph=_get_graph_index(),
        )
    return _search_index


def _get_graph_index():
    """Get the associative memory graph."""
    global _graph_index
    if _graph_index is None:
        _graph_index = _graph_module.GraphIndex(
            _storage_module.plugin_data_dir() / "graph.db"
        )
    return _graph_index


def _get_reflection_engine():
    """Get the reflection engine."""
    global _reflection_engine
    if _reflection_engine is None:
        _reflection_engine = _reflection_module.ReflectionEngine(
            _get_indexed_mem_store(),
            _get_search_index(),
            _get_graph_index(),
            log_path=_storage_module.plugin_data_dir() / "reflect-log.jsonl",
        )
    return _reflection_engine


def _get_memory_context(query: str = "", max_tokens: int = 4000) -> str:
    """Build context from memory, search, and skills."""
    from .store import SkillStore
    store = _get_indexed_mem_store()
    search = _get_search_index()
    skills = SkillStore(_storage_module.user_skills_dir(), _storage_module.project_skills_dir())
    return _context_module.build_context(store, search, skills, query, max_tokens)


def _get_indexed_skill_store():
    """Get the skill store."""
    return _storage_module.SkillStore(
        _storage_module.user_skills_dir(),
        _storage_module.project_skills_dir(),
    )


# Route package-level late-binding consumers to the indexed persistence layer.
_get_mem_store = _get_indexed_mem_store
_get_skill_store = _get_indexed_skill_store
