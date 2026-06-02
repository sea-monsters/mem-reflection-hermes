# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mem-reflection-hermes** is a self-evolving memory & reflection system plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It provides structured memory persistence, semantic search, reflection pipelines, skill auto-matching, graph memory (Hebbian co-activation), and a dashboard UI. Ported from [small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes).

Current version: **v1.0-beta** (plugin.yaml version field).

## Commands

```bash
# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/test_core_data.py -v

# Run a specific test class or test
pytest tests/test_core_data.py::TestFrontmatter -v
pytest tests/test_core_data.py::TestFrontmatter::test_roundtrip -v

# Run with coverage
pytest tests/ --cov=. --cov-report=term-missing

# Run v0.9.2 feature verification script
python scripts/check_v092.py

# Run a specific test with warnings shown
pytest tests/test_core_data.py -v -W default

# Performance benchmark
python bench_latency.py
```

## Architecture

### Module Layout (13 modules + dashboard, ~8,000 lines)

```
__init__.py           # Package registration, 5 graph/health tools, bootstrap (~1,870 lines)
core.py               # MemoryStore, SkillStore, models, config, paths, BM25 (~1,078 lines)
late_binding.py       # Shared late-binding symbol resolution with thread-safe cache (~38 lines)
search/embed.py       # ONNX embedding engine, cosine similarity, intent classification, LRU cache (~504 lines)
reflection/engine.py  # Micro/full/raw-chunk reflection, auto-rebalance, profile compilation (~1,692 lines)
hooks/lifecycle.py    # Session hooks, slash commands, graph manager, micro-reflection cadence (~423 lines)
tools/handlers.py     # 12 SRH tool handlers exposed to Hermes Agent (~966 lines)
graph/ahe_graph.py    # SQLite-backed Hebbian graph memory, association engine, Ebbinghaus decay (~1,024 lines)
graph/cluqi.py        # Cross-Layer Unified Query Interface (Memory + Graph + Supersedes) (~296 lines)
graph/pagerank.py     # PageRank centrality for graph nodes (~116 lines)
graph/cross_zone.py   # Cross-zone analysis (bridges, centrality, recommendations) (~133 lines)
query/cache.py        # Query templates and TTL-based result cache with LRU eviction (~213 lines)
dashboard/plugin_api.py # FastAPI dashboard (14 endpoints) (~646 lines)
```

### Import Order Rules

This is the **most critical architectural constraint** — modules form a strict dependency chain:

1. `core.py` — no imports from other project modules (leaf module)
2. `search/embed.py` — imports from core only
3. `reflection/engine.py` — imports from core + embed
4. `hooks/lifecycle.py` — imports from core + embed + reflection
5. `tools/handlers.py` — imports from all above
6. `__init__.py` — imports from all modules, registers graph tools that need graph-manager init

Graph modules (`graph/ahe_graph.py`, `cluqi.py`, `pagerank.py`, `cross_zone.py`) import from `core` only. `query/cache.py` imports from `core` only. `late_binding.py` has no project imports.

Avoid circular imports. Use `late_binding.py` for cross-module symbol resolution at runtime.

### Thread Safety

Key concurrency protections:

| Resource | Protection |
|----------|-----------|
| `MemoryStore` mutations | `RLock` on all public mutation methods |
| `_session_messages` dict | `threading.Lock` in lifecycle hooks |
| `_turns_since_reflect` counter | `threading.Lock` (micro-reflection cadence) |
| `_reflect_log_lock` | Covers both read and write paths |
| Embedding cache | `threading.Lock` on all cache operations |
| `_build_adjacency` | mtime + DB query + cache update inside `self._lock` |
| `get_cache()` singleton | Double-checked locking |

### Session Hook Lifecycle

```
on_session_start hook   --> Reset turn counter, clear session exclusion set
pre_llm_call hook        --> Inject layered context, trigger micro-reflection (every 3 turns or explicit intent)
post_tool_call hook      --> Record effectiveness, update graph associations
on_session_end hook      --> Full reflection pipeline, skill candidates, session summary, graph decay
```

Context injection priority (subject to `max_context_token_preference`):
1. Pinned memories (always included)
2. Active index (zone-based relevance)
3. Triggered skills (per-turn token-overlap matching)
4. Always-active skills (user-configured)

### Tool Split

- 12 core tools in `tools/handlers.py` (CRUD, palace navigation, reflection, skills, profile)
- 5 graph/health tools in `__init__.py` (`srh_associate`, `srh_graph_retrieve`, `srh_graph_stats`, `srh_graph_viz`, `srh_memory_health`) — registered in `__init__.py` because they require graph-manager initialization at plugin load time

## Key Patterns

### Test Fixtures (conftest.py)

Tests use `pytest` with shared fixtures:
- `temp_dir` — temp directory for isolated file operations
- `temp_store` — MemoryStore with temp root (auto-packages `__init__.py` into `sys.modules`)
- `temp_graph` — GraphStore backed by temp SQLite (with Windows cleanup retry)
- `seeded_store` — MemoryStore with 5 pre-loaded memories for ranking/retrieval tests

### Memory Format

Memories are Markdown files with YAML frontmatter. Key frontmatter fields:
`id`, `created`, `source`, `confidence`, `pinned`, `tags`, `zone`, `rank`, `supersedes`, `supersedes_reason`, `version`, `valid_from`, `valid_until`, `context_scope`

Files stored in `~/.hermes/memories/` (user) or `./.hermes/memories/` (project).

### Graph Semantics

The ahe_graph layer is an **associative co-activation graph** (Hebbian), not an entity-relation knowledge graph. Edges mean "these memories were used together", not typed factual relationships. The graph tracks co-occurrence strength with Ebbinghaus decay.

### CJK Awareness

Token estimation is CJK-aware (3 bytes/token for CJK, 4 bytes/token for Latin). Conflict threshold adapts: 0.55 for CJK-heavy content, 0.65 for Latin-heavy content.

### Reflection Session Exclusion

Newly created memory IDs during reflection are tracked in a session-local set (`_current_session_memory_ids`). This prevents the feedback loop where reflection sees its own just-written output as a duplicate or conflict. The set is cleared on session start and session end.

## Key Conventions

- Timestamps: always `datetime.now(timezone.utc)` — never bare `datetime.now()`
- Hashing: SHA-256 only
- SQLite concurrent writes: WAL mode + `INSERT OR REPLACE`
- Supersedes chains for version lineage (not for mere related memories)
- `@lru_cache` on file-backed config: avoid — use mtime-aware cache instead (see core.py `load_config`)
- Config writes in `autotrigger_manage.py`: only on `start`/`bootstrap`, never on `status`/`stop`
- Silent error swallowing: use `logger.warning` (not `logger.debug`) for all failure paths that could indicate data loss or degraded functionality
