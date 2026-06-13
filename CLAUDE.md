# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mem-reflection-hermes** is a self-evolving memory & reflection system plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It provides structured memory persistence, semantic search, reflection pipelines, skill auto-matching, graph memory (Hebbian co-activation), and a dashboard UI. Ported from [small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes).

Current version: **v1.7** (plugin.yaml version field).

### Architecture (functional packages)

The codebase is organized into five functional packages:

```
core/
  store.py          # MemoryStore, SkillStore, config, paths, BM25 helpers, memory_events ledger, scope helpers
  scope.py         # Shared scope_from_context() helper for user_id/agent_id/run_id resolution
  store_methods.py  # Thin method bodies extracted from MemoryStore (entity boosts, etc.)
  models.py         # MemoryFrontmatter, LoadedMemory, SkillFrontmatter, LoadedSkill, parse/serialize
  search.py         # SearchIndex: BM25 + embedding + fusion + Hebbian boost, explain, CJK tokenizer
  graph.py          # GraphIndex: SQLite Hebbian graph, PageRank, spreading activation, typed fact sidecar
  config.py         # Typed config models, diagnostics, validation
  backend.py        # SearchBackendLike protocol + capability flags
  entities.py       # Entity extraction pipeline (regex-first + optional spaCy)
  tokenization.py   # CJK-aware tokenizer, token estimation
  utils.py          # normalize_zone, sanitize_zone_filename, is_cjk, etc.
  async_writer.py   # Background file I/O thread for memory writes
  skill_store.py    # SkillStore implementation
  store_health.py   # Store health checks
  lineage.py        # Supersedes chain helpers (_lineage_latest, _lineage_depth, etc.)
  intent.py         # Intent classification helpers
  reranker.py       # Optional second-stage reranker (cross_encoder / cohere)

reflection/
  engine.py         # ReflectionEngine: raw_chunk default, fact extraction
  runtime.py        # _run_full_reflection, _run_micro_reflection, scope-aware reflection, audit logging, compaction
  extraction.py     # Shared refined candidate extraction (kind/priority metadata)
  supersedes_resolver.py  # Semantic correction/merge/store/scope-split resolver

memory/
  curator/          # Composable action pipeline (v1.3 refactor)
    __init__.py     # _run_curator() entrypoint, backward-compat wrappers
    actions.py      # CuratorAction base + ArchiveStale, CompactChains, ArchiveSuperseded, MergeSimilar, CleanOrphanEdges, GenerateReport
    helpers.py      # is_protected, build_cold_entry, archive_and_delete, load_last_access, config
    cold_store.py   # JSONL cold storage: append, load, prune, restore
    report.py       # Report generation and persistence
  bridge.py         # Bidirectional sync between plugin MemoryStore and host builtin memory
  context.py        # Context assembly: stable/dynamic split, token budget, skill matching, graded compression (v1.4)

runtime/
  tools.py          # 8 base SRH tool handlers (write, search, delete, history, palace, reflect, skill, compile)
  hooks.py          # Session hooks (start/end/pre_llm/post_tool/reset/api_error/subagent) and slash commands
  graph.py          # 5 graph/health tools + graph manager singleton
  checkpoint.py     # Atomic session checkpoint, pending-stage recovery, corrupt backup (v1.4)
  registration.py   # register(ctx): wires hooks, commands, tools, post-delete callbacks
  schemas.py        # 13 Hermes tool JSON schemas
  state.py          # Singleton getters (_get_mem_store, _get_search_index, _get_graph_mgr, etc.)
  helpers.py        # _build_context_block, _build_context_bundle, _estimate_tokens, match_skills
  _lb.py            # Late-binding helper: resolves modules/symbols without circular imports

web/
  api.py            # FastAPI dashboard (15 endpoints)

__init__.py         # Exports public API, backward-compat aliases, delegates register() to runtime.registration
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
# Run all tests (expected baseline: 615 passed)
pytest tests/ -v

# Run quietly when validating release baseline
pytest tests/ -q

# Run by functional marker (see docs/testing/test-coverage.md)
pytest -m "search and retrieval" -v
pytest -m "runtime and not e2e" -v
pytest -m "v14_runtime or v14_entity" -v
pytest -m "contract or smoke" -v

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
pytest tests/test_checkpoint.py -v
pytest tests/test_config.py -v
pytest tests/test_backend.py -v
pytest tests/test_bm25.py -v
pytest tests/test_entity_extraction.py -v
pytest tests/test_curator_pipeline.py -v
pytest tests/test_store_module_split.py -v
pytest tests/test_async_writer.py -v
pytest tests/test_lb.py -v
pytest tests/test_schema_module.py -v
pytest tests/test_memory_events.py -v
pytest tests/test_scope_filters.py -v
pytest tests/test_reflection_scope.py -v
pytest tests/test_reflection_refinement.py -v
pytest tests/test_semantic_supersedes.py -v
pytest tests/test_typed_fact_sidecar.py -v
pytest tests/test_compaction.py -v

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
core/scope.py              ← imports core.store (TYPE_CHECKING) only
core/models.py             ← imports core.utils only
core/utils.py              ← leaf module
core/search.py             ← imports core.store + core.tokenization + core.models + core.scope
core/graph.py              ← imports core.store (cross_zone only)
core/config.py             ← imports core.store
core/backend.py            ← no project imports
core/entities.py           ← imports core.utils + core.tokenization
core/store_methods.py      ← imports core.entities + core.tokenization + core.models + core.store (TYPE_CHECKING)
core/skill_store.py        ← imports core.store
core/lineage.py            ← imports core.store (TYPE_CHECKING)
core/intent.py             ← imports core.store (TYPE_CHECKING)

reflection/engine.py       ← imports core.store + core.search + reflection.extraction
reflection/runtime.py      ← imports core.store + core.search + reflection.engine + reflection.extraction + reflection.supersedes_resolver
reflection/extraction.py   ← imports core.store only
reflection/supersedes_resolver.py  ← imports core.store + reflection.extraction

memory/curator/*           ← imports core.store + memory/bridge (helpers)
memory/bridge.py           ← imports core.store only
memory/context.py          ← imports core.store + core.search + core.config

runtime/tools.py           ← imports core.store + core.search + reflection.engine + runtime.hooks
runtime/hooks.py           ← imports core.store + reflection.* + core.search + memory.* + runtime.checkpoint
runtime/graph.py           ← imports core.graph + core.store
runtime/checkpoint.py      ← imports core.store
runtime/registration.py    ← imports runtime.hooks + runtime.schemas + runtime.tools + runtime.graph + memory.bridge
runtime/state.py           ← imports core.store + core.search + core.graph + runtime.graph
runtime/helpers.py         ← imports memory.context + core.store
runtime/schemas.py         ← no project imports
runtime/_lb.py             ← no project imports (stdlib only)

web/api.py                 ← imports package runtime services via sys.modules fallback

__init__.py                ← explicit imports from all packages, delegates register() to runtime.registration
```

No circular imports. All deprecated compat files are thin forwarders and must not regain implementation logic.

### Import Order Rules

Respect the layer boundaries when adding new functionality:

1. `core/store.py` — data models, store logic, config, paths — no Hermes dependencies
2. `core/scope.py` — shared scope helper; resolves `user_id` / `agent_id` / `run_id` from host context
3. `core/models.py`, `core/utils.py`, `core/tokenization.py`, `core/entities.py` — leaf modules (models import utils; entities import utils + tokenization)
4. `core/search.py` — search and embedding helpers — imports core.store + core.tokenization + core.models + core.scope
5. `core/graph.py` — GraphIndex — imports core.store only where cross-zone needs memory metadata
6. `core/config.py`, `core/backend.py` — typed config and backend abstraction — import core.store
7. `reflection/extraction.py` / `reflection/supersedes_resolver.py` / `reflection/engine.py` / `reflection/runtime.py` — reflection pipelines — import core.store + core.search
8. `memory/curator/` — composable action pipeline — imports core.store + memory.bridge (helpers); `memory/bridge.py` imports core.store only
9. `memory/context.py` — context assembly — imports core.store + core.search + core.config
10. `runtime/tools.py`, `runtime/hooks.py`, `runtime/graph.py`, `runtime/checkpoint.py` — host-facing runtime features — depend on canonical services
11. `runtime/registration.py`, `runtime/schemas.py`, `runtime/state.py`, `runtime/helpers.py`, `runtime/_lb.py` — registration, schemas, singletons, late-binding
12. `web/api.py` — dashboard — imports package runtime services via sys.modules fallback
13. `__init__.py` — exports public API, backward-compat aliases, delegates register() to runtime.registration

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
                             recover pending session-end work from checkpoint
pre_llm_call hook        --> Inject layered context (stable/dynamic split),
                             trigger micro-reflection (every 3 turns or explicit intent);
                             scope filters propagate into context + micro-reflection
post_tool_call hook      --> Bridge Dir A, record effectiveness, update graph associations;
                             slow/error calls logged with turn_id
on_session_end hook      --> Full reflection pipeline, skill candidates, session summary,
                             episode compaction, memory curator (stale/similar/archive/orphan;
                             scoped by default; explicit admin_global for full-store runs),
                             graph decay, write session checkpoint
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

- 8 base tools in `runtime/tools.py` (write, search, delete, history, palace, reflect, skill, compile)
- 5 graph/health tools in `runtime/graph.py` (`srh_associate`, `srh_graph_retrieve`, `srh_graph_stats`, `srh_graph_viz`, `srh_memory_health`)
- All 13 schemas in `runtime/schemas.py`
- All 13 registered through `runtime/registration.py::register(ctx)` (called from `__init__.py`)

### Late Binding (`runtime/_lb.py`)

Runtime modules use `_lb(name)` to resolve modules or symbols without hard imports that would create circular dependencies:
- Dotted module names (e.g. `"core.store"`) → `importlib.import_module` with caching
- Bare symbols (no dots) → looked up from `mem_reflection_hermes` package `__dict__`
- Returns `None` on failure so callers remain fail-open

Used by `runtime/tools.py` and `runtime/hooks.py` for cross-module resolution at call time rather than import time.

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
`id`, `created`, `source`, `confidence`, `pinned`, `tags`, `zone`, `rank`, `supersedes`, `supersedes_reason`, `version`, `valid_from`, `valid_until`, `context_scope`, `user_id`, `agent_id`, `run_id`

Files stored in `~/.hermes/memories/` (user) or `./.hermes/memories/` (project).

### Graph Semantics

The runtime graph layer is an **associative co-activation graph** (Hebbian), not an entity-relation knowledge graph. Edges mean "these memories were used together", not typed factual relationships. The graph tracks co-occurrence strength, PageRank, spreading activation, and cross-zone relationships through `GraphIndex`/`runtime/graph.py`.

### CJK Awareness

Token estimation is CJK-aware (3 bytes/token for CJK, 4 bytes/token for Latin). Conflict threshold adapts: 0.55 for CJK-heavy content, 0.65 for Latin-heavy content.

### Reflection Session Exclusion

Newly created memory IDs during reflection are tracked in a session-local set (`_current_session_memory_ids`). This prevents the feedback loop where reflection sees its own just-written output as a duplicate or conflict. The set is cleared on session start and session end.

### Memory Curator (v1.2 → v1.3 → subpackage → v1.7 scope-aware)

The 5-phase curation pipeline was refactored from `memory/curator.py` into `memory/curator/` (composable action pipeline):
1. **ArchiveStale** — TTL expiry + staleness detection
2. **CompactChains** — compact intermediates before archiving
3. **ArchiveSuperseded** — remaining deep chains (depth >= 2)
4. **MergeSimilar** — BM25 overlap detection + optional merge
5. **CleanOrphanEdges** — remove dangling graph edges
6. **GenerateReport** — persist human-readable summary

The pipeline runs automatically at `on_session_end`. All actions implement `CuratorAction(name, should_run(ctx), execute(ctx))`. Results are aggregated in `_run_curator()` and a report is persisted to cold storage. Fail-open: exceptions in any action are caught and logged.

v1.7 propagation: curator accepts `filters` so maintenance runs per scope by default; pass `admin_global=True` explicitly for deliberate full-store maintenance. Local no-filter mode remains global for single-user compatibility.

Body refinement (`_refine_body`) strips fenced code blocks, `[Tool:xxx]` markers, tool-result prefixes, and collapses excess whitespace before bridge writes and cold-storage archive.

**Legacy API preserved**: `scan_for_stale`, `archive_expired`, `archive_superseded`, `compact_superseded_chains`, `scan_for_similar`, `merge_similar`, `clean_orphan_edges` remain as thin wrappers in `memory/curator/__init__.py`.

### v1.7 New Features

- **Scope-Aware Reflection**: `core/scope.py` provides `scope_from_context()`; hooks, reflection, compaction, and curator propagate `user_id`/`agent_id`/`run_id` filters. New memories are stamped with scope; supersedes rejects cross-scope targets.
- **Refined Extraction**: `reflection/extraction.py` centralizes candidate extraction with typed `kind`/`priority` metadata (intent, correction, decision, todo, preference, policy, procedure).
- **Semantic Supersedes**: `reflection/supersedes_resolver.py` separates correction, merge, store, skip, and scope-split decisions so generic intent no longer auto-promotes to replacement edges.
- **Typed Fact Sidecar**: `core/graph.py` stores typed-fact rows with source, target, relation, kind, episode lineage, and invalidation metadata; reflection and `GraphIndex.distill()` populate it.
- **Compaction Quality**: `_compact_episode_zone()` scores fragments and prefers concise conclusion-like summaries; verbose LLM summaries that score worse are rejected for the fallback.

### v1.6 New Features

- **Memory Event Ledger**: SQLite `memory_events` table tracks ADD/UPDATE/DELETE/SUPERSEDE/PIN/UNPIN events with old/new body, old/new frontmatter, session_id, actor_id. Transaction-aware: events are written atomically within the same SQLite connection as the memory mutation.
- **Scoped Filters**: `user_id`/`agent_id`/`run_id` columns on `memories` table. NULL = universally visible. AND logic for combined filters. `filters` parameter on `list()`, `search()`, `search_explain()`, and `delete_by_filters()`.
- **`srh_memory_history` tool**: Traces supersedes chain with optional audit events (`include_events`, `event_types`, `session_id`).
- **Batch delete**: `srh_memory_delete` supports `filters`-only batch deletion.
- **Schema consolidation**: Canonical schemas live in `runtime/schemas.py`; imported by `runtime/registration.py` for actual Hermes tool registration.

### v1.4 New Features

- **`ContextBundle`**: Internal structured context with `stable`/`dynamic` split. `build_context_bundle()` preserves the stable section across turns (prompt-cache-friendly).
- **Graded compression**: `none/mild/aggressive/emergency` levels applied to dynamic content under token pressure.
- **Timeout-protected `pre_llm_call`**: 8-second cap with stable-only fallback on timeout or failure.
- **Session checkpoint**: `runtime/checkpoint.py` writes atomic JSON at session end; recovers pending work on session start.
- **Explainable search**: `SearchIndex.search_explain()` returns per-hit score breakdown (BM25, embedding, recency, effectiveness, supersedes, entity, Hebbian).
- **Entity index**: SQLite-backed `entities` and `entity_links` tables with lifecycle hooks on write, delete, rebuild.
- **Backend capability abstraction**: `core/backend.py` exposes `SearchBackendLike` protocol and `SearchBackendCapabilities`.
- **Typed config diagnostics**: `core/config.py` validates types, warns on unknown keys, falls back to safe defaults.

### Reranker Integration (v1.2)

Optional second-stage reranking (`core/reranker.py`) inserted after Hebbian boost and before MMR in `SearchIndex.search()`:
- **Providers**: `cross_encoder` (local, default) or `cohere` (API-based).
- **Lazy loading**: Model/client initialized on first `rerank()` call.
- **Graceful fallback**: On any failure returns original order with `logger.warning`.
- **Config**: `reranker.*` under plugin config — disabled when absent.
- Wired into `_get_search_index()` in `runtime/state.py`.

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

## Development Methodology: Spec -> Test -> Code

Every feature (new module, refactor, API change) must follow this sequence:

### 1. SDD (Software Design Document)

Create `docs/design/<version>/<feature>-sdd.md` with:

- Purpose / Problem / Design Goals / Non-Goals
- Proposed Design with interface contracts and data flow
- Files Affected
- Acceptance Criteria (verifiable, testable)

### 2. Test Suite (RED phase)

Write `tests/test_<feature>.py` covering:

- **Functional intent**: every behavior described in the SDD has a test
- **Boundary conditions**: empty inputs, single-item, max-size, timing edge cases
- **Error paths**: every failure mode in the SDD has a test
- **Integration seams**: import paths, re-export compatibility, public API contracts

All tests must **fail** at this stage (no production code exists yet). Verify coverage of the SDD acceptance criteria.

### 3. Freeze Tests

Lock the test file. Do not modify test assertions to fit implementation. Tests define truth.

### 4. Production Code (GREEN phase)

Implement the SDD design. Run `pytest tests/test_<feature>.py` iteratively until all tests pass.

### 5. Verify Full Suite

Run `pytest tests/ -v` — all 615 tests must pass. No regressions.

### Exceptions

Single-file patches, doc edits, and config changes do not require the full methodology. Use judgment.
