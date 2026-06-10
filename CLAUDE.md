# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mem-reflection-hermes** is a self-evolving memory & reflection system plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It provides structured memory persistence, semantic search, reflection pipelines, skill auto-matching, graph memory (Hebbian co-activation), and a dashboard UI. Ported from [small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes).

Current version: **v1.4-beta** (plugin.yaml version field).

### Architecture (functional packages)

The codebase is organized into five functional packages:

```
core/
  store.py          # MemoryStore, SkillStore, frontmatter I/O, config, paths, lineage, entity index
  search.py         # SearchIndex: BM25 + embedding + fusion + Hebbian boost, explain, CJK tokenizer
  graph.py          # GraphIndex: SQLite Hebbian graph, PageRank, spreading activation
  config.py         # Typed config models, diagnostics, validation (v1.4)
  backend.py        # SearchBackendLike protocol + capability flags (v1.4)

reflection/
  engine.py         # ReflectionEngine: raw_chunk default, fact extraction
  runtime.py        # _run_full_reflection, _run_micro_reflection, audit logging, compaction

memory/
  curator.py        # 5-phase curation: TTL/staleness, supersedes, similarity, orphan cleanup, cold storage
  bridge.py         # Bidirectional sync between plugin MemoryStore and host builtin memory
  context.py        # Context assembly: stable/dynamic split, token budget, skill matching, graded compression (v1.4)

runtime/
  tools.py          # 7 base SRH tool handlers (write, search, delete, palace, reflect, skill, compile)
  hooks.py          # Session hooks (start/end/pre_llm/post_tool/reset/api_error/subagent) and slash commands
  graph.py          # 5 graph/health tools + graph manager singleton
  checkpoint.py     # Atomic session checkpoint, pending-stage recovery, corrupt backup (v1.4)

web/
  api.py            # FastAPI dashboard (15 endpoints)

__init__.py         # Plugin registration, 12 tool schemas, runtime singletons
```

Full architecture documentation: `docs/ARCHITECTURE.md`
Changelog: `docs/CHANGELOG.md`
Tool reference: `docs/TOOLS.md`
Dashboard docs: `docs/DASHBOARD.md`
Test coverage: `docs/testing/test-coverage.md`
Data safety: `docs/DATA_SAFETY.md`

**Deprecated compatibility entrypoints (thin forwarders only):**
```
reflection/engine.py  # forwards to reflection.engine (self)
hooks/lifecycle.py    # forwards to runtime.hooks
tools/handlers.py     # forwards to runtime.tools
graph/compat.py       # forwards to runtime.graph
```

## Commands

```bash
# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/test_store.py -v
pytest tests/test_search.py -v
pytest tests/test_graph.py -v
pytest tests/test_reflect.py -v
pytest tests/test_context.py -v
pytest tests/test_dashboard.py -v
pytest tests/test_e2e.py -v
pytest tests/test_memory_curator.py -v
pytest tests/test_bridge.py -v
pytest tests/test_fusion_rerank.py -v
pytest tests/test_wave3_retrieval.py -v
pytest tests/test_host_contract_smoke.py -v
pytest tests/test_checkpoint.py -v      # v1.4: checkpoint persistence/recovery
pytest tests/test_config.py -v          # v1.4: typed config diagnostics
pytest tests/test_backend.py -v         # v1.4: backend capability abstraction
pytest tests/test_bm25.py -v            # v1.4: CJK tokenizer + BM25 scoring

# Run a specific test class or test
pytest tests/test_store.py::TestRebuildIndex -v
pytest tests/test_store.py::TestRebuildIndex::test_rebuild_empty_store -v

# Run with coverage
pytest tests/ --cov=. --cov-report=term-missing

# Run host-contract smoke script
python scripts/smoke_host_contract.py

# Performance benchmark
python scripts/bench_latency.py

# One-time memory index migration
python scripts/migrate_memory_index.py
```

## Architecture

### Module Dependency DAG

```
core/store.py              ← leaf module, no project imports
core/search.py             ← imports core.store
core/graph.py              ← imports core.store (cross_zone only)
core/config.py             ← imports core.store
core/backend.py            ← no project imports

reflection/engine.py       ← imports core.store + core.search
reflection/runtime.py      ← imports core.store + core.search + reflection.engine

memory/curator.py          ← imports core.store + memory.bridge
memory/bridge.py           ← imports core.store only
memory/context.py          ← imports core.store + core.search + core.config

runtime/tools.py           ← imports core.store + core.search + reflection.engine + runtime.hooks
runtime/hooks.py           ← imports core.store + reflection.* + core.search + memory.* + runtime.checkpoint
runtime/graph.py           ← imports core.graph + core.store
runtime/checkpoint.py      ← imports core.store

web/api.py                 ← imports package runtime services via sys.modules fallback

__init__.py                ← explicit imports from all packages, registers 12 tools
```

No circular imports. All deprecated compat files are thin forwarders and must not regain implementation logic.

### Import Order Rules

Respect the layer boundaries when adding new functionality:

1. `core/store.py` — data models, store logic, config, no Hermes dependencies
2. `core/search.py` — search and embedding helpers, imports core.store only
3. `core/graph.py` — GraphIndex, imports core.store only where cross-zone needs memory metadata
4. `reflection/engine.py`, `reflection/runtime.py` — reflection pipelines, import core.store + core.search
5. `memory/curator.py`, `memory/bridge.py` — curation and host sync, import core.store (+ memory.bridge for curator)
6. `memory/context.py` — context assembly, imports core.store + core.search
7. `runtime/tools.py`, `runtime/hooks.py`, `runtime/graph.py` — host-facing runtime features
8. `__init__.py` — registration and runtime singletons

### Thread Safety

| Resource | Protection |
|----------|-----------|
| `MemoryStore` mutations | `RLock` on all public mutation methods |
| `_session_messages` dict | `threading.Lock` in lifecycle hooks |
| `_turns_since_reflect` counter | `threading.Lock` (micro-reflection cadence) |
| `_reflect_log_lock` | Covers both read and write paths |
| Embedding cache | `threading.Lock` on all cache operations |
| GraphIndex / graph compat | SQLite WAL plus runtime graph locks where singletons are shared |
| `get_cache()` singleton | Double-checked locking |
| Cold store writes | `threading.Lock` (`_cold_store_lock`) guards JSONL append/rewrite |
| `_build_adjacency` | mtime check + DB query + cache update inside `self._lock` |

### Session Hook Lifecycle

```
on_session_start hook   --> Reset turn counter, clear session exclusion set,
                             recover pending session-end work from checkpoint (v1.4)
pre_llm_call hook        --> Inject layered context (stable/dynamic split, v1.4),
                             trigger micro-reflection (every 3 turns or explicit intent)
post_tool_call hook      --> Bridge Dir A, record effectiveness, update graph associations
on_session_end hook      --> Full reflection pipeline, skill candidates, session summary,
                             episode compaction, memory curator (stale/similar/archive/orphan),
                             graph decay, write session checkpoint (v1.4)
```

Context injection priority (subject to `max_context_token_preference`):

**Stable section** (preserves prompt cache across turns):
1. Pinned memories (always included)
2. Always-active skills (user-configured)

**Dynamic section** (varies per turn; graded compression under pressure):
3. Active index (zone-based relevance)
4. Triggered skills (per-turn token-overlap matching)
5. Compacted episode summaries (when enabled)

### Tool Split

- 7 base tools in `runtime/tools.py` (write, search, delete, palace, reflect, skill, compile)
- 5 graph/health tools in `runtime/graph.py` (`srh_associate`, `srh_graph_retrieve`, `srh_graph_stats`, `srh_graph_viz`, `srh_memory_health`)
- All 12 registered through `__init__.py::register(ctx)`

## Key Patterns

### Test Fixtures (conftest.py)

Tests use `pytest` with shared fixtures:
- `temp_dir` — temp directory with Windows-compatible retry cleanup
- `temp_store` — MemoryStore with temp root (closes SQLite conn on teardown)
- `temp_graph` — GraphStore backed by temp SQLite (with Windows cleanup retry)
- `seeded_store` — MemoryStore with 5 pre-loaded memories for ranking/retrieval tests

The conftest sets `basetemp` to a plugin-specific directory under `tempfile.gettempdir()` to avoid Windows ACL issues with pytest's default `pytest-of-<user>` naming.

### Test Loading Pattern (Runtime Modules)

Tests for runtime modules (`core/*.py`, `reflection/*.py`, `memory/*.py`) use `importlib.util.spec_from_file_location` to avoid package-relative import issues when loading standalone files. Each test file has a block like:

```python
import importlib.util
_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("_mod_name", str(_REPO / "core" / "store.py"))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["_mod_name"] = _mod
_spec.loader.exec_module(_mod)
```

Production code includes `try/except ImportError` fallback blocks for both package-relative and direct loading. Preserve these when editing.

### Windows Cleanup

Temp directory fixtures include retry loops with `shutil.rmtree` to handle SQLite file locking on Windows:

```python
for attempt in range(retries):
    try:
        shutil.rmtree(str(path))
        return
    except PermissionError:
        if attempt < retries - 1:
            time.sleep(delay * (attempt + 1))
        else:
            shutil.rmtree(str(path), ignore_errors=True)
```

### Search Cache Invalidation

`SearchIndex` has four layers of lazy caching that must all be cleared after mutations:
- `_bm25_retriever` — bm25s index (set to `None` to rebuild)
- `_embed_array` / `_embed_ids` — numpy embedding matrix (set to `None` to rebuild)
- `_cache` — `cachetools.TTLCache` for search results (call `invalidate_cache()`)
- `_embed_single` — `functools.lru_cache` for individual embeddings (call `cache_clear()`)

E2E tests that mutate memories and then search must clear all four:

```python
search._bm25_retriever = None
search._embed_array = None
search.invalidate_cache()
_embed_single.cache_clear()  # module-level lru_cache
```

### Dashboard Mock Isolation

`test_dashboard.py` mocks `mem_reflection_hermes` via `sys.modules` before loading `web/api.py`. `web/api.py` checks `sys.modules` first to avoid overwriting the mock:

```python
# In web/api.py
try:
    from .. import __dict__ as _srh_dict
except ImportError:
    if "mem_reflection_hermes" in sys.modules:
        srh = sys.modules["mem_reflection_hermes"]
    else:
        # ... load from file
```

### Memory Format

Memories are Markdown files with YAML frontmatter. Key frontmatter fields:
`id`, `created`, `source`, `confidence`, `pinned`, `tags`, `zone`, `rank`, `supersedes`, `supersedes_reason`, `version`, `valid_from`, `valid_until`, `context_scope`

Files stored in `~/.hermes/memories/` (user) or `./.hermes/memories/` (project).

### Graph Semantics

The runtime graph layer is an **associative co-activation graph** (Hebbian), not an entity-relation knowledge graph. Edges mean "these memories were used together", not typed factual relationships. The graph tracks co-occurrence strength, PageRank, spreading activation, and cross-zone relationships through `GraphIndex`/`runtime/graph.py`.

### CJK Awareness

Token estimation is CJK-aware (3 bytes/token for CJK, 4 bytes/token for Latin). Conflict threshold adapts: 0.55 for CJK-heavy content, 0.65 for Latin-heavy content.

### Reflection Session Exclusion

Newly created memory IDs during reflection are tracked in a session-local set (`_current_session_memory_ids`). This prevents the feedback loop where reflection sees its own just-written output as a duplicate or conflict. The set is cleared on session start and session end.

### Memory Curator (v1.2 → v1.3)

The 5-phase curation pipeline runs automatically at `on_session_end`:
1. **TTL + Staleness** — archive expired (`valid_until` past) and stale (>90 days no access) memories
2. **Supersedes Archiving** — deep supersedes chains (depth >= 2) with no recent access
3. **Similarity Detection** — BM25 token-overlap pair scoring, flags candidates above 0.6 threshold
4. **Orphan Graph Edge Cleanup** — remove dangling edges after memory deletion (v1.3)
5. **Cold Storage** — JSONL with 10MB cap and oldest-entry pruning; restore via `_restore_from_cold()`

Body refinement (`_refine_body`) strips fenced code blocks, `[Tool:xxx]` markers, tool-result prefixes, and collapses excess whitespace before bridge writes and cold-storage archive.

### v1.4 New Features

- **`ContextBundle`**: Internal structured context with `stable`/`dynamic` split. `build_context_bundle()` preserves the stable section across turns (prompt-cache-friendly).
- **Graded compression**: `none/mild/aggressive/emergency` levels applied to dynamic content under token pressure.
- **Timeout-protected `pre_llm_call`**: 8-second cap with stable-only fallback on timeout or failure.
- **Session checkpoint**: `runtime/checkpoint.py` writes atomic JSON at session end; recovers pending work on session start.
- **Explainable search**: `SearchIndex.search_explain()` returns per-hit score breakdown (BM25, embedding, recency, effectiveness, supersedes, entity, Hebbian).
- **Entity index**: SQLite-backed `entities` and `entity_links` tables with lifecycle hooks on write, delete, rebuild.
- **Backend capability abstraction**: `core/backend.py` exposes `SearchBackendLike` protocol and `SearchBackendCapabilities`.
- **Typed config diagnostics**: `core/config.py` validates types, warns on unknown keys, falls back to safe defaults.

### v0.16.0 Enhanced Hooks

Hermes Agent v0.16.0 adds richer observer-style hooks consumed by `runtime/hooks.py`:
- `subagent_start` / `subagent_stop` — track concurrent subagent count and lifecycle
- Enhanced `post_tool_call` kwargs: `status`, `duration_ms`, `turn_id`, `session_id`
- `api_request_error` — track API error count per session
- `on_session_reset` — session rotation with `reason`, `old_session_id`, `new_session_id`

All new hooks are gated by `has_hook()` — zero overhead when no subscriber is active.

## Key Conventions

- Timestamps: always `datetime.now(timezone.utc)` — never bare `datetime.now()`
- Hashing: SHA-256 only
- SQLite concurrent writes: WAL mode + `INSERT OR REPLACE`
- Supersedes chains for version lineage (not for mere related memories)
- `@lru_cache` on file-backed config: avoid — use mtime-aware cache instead (see `core/store.py` `load_config`)
- Config writes in autotrigger manage scripts: only on `start`/`bootstrap`, never on `status`/`stop`
- Silent error swallowing: use `logger.warning` (not `logger.debug`) for all failure paths that could indicate data loss or degraded functionality
- Cold storage safety: write-then-swap via `.tmp` + `os.replace()` for JSONL rewrites
