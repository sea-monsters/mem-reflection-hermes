# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mem-reflection-hermes** is a self-evolving memory & reflection system plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It provides structured memory persistence, semantic search, reflection pipelines, skill auto-matching, graph memory (Hebbian co-activation), and a dashboard UI. Ported from [small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes).

Current version — see `plugin.yaml` for the canonical version.

### Architecture (runtime modules + beta3 compatibility)

The active runtime uses the beta3 module set plus v1.1 enhancements (episode compaction, v0.16.0 telemetry hooks). Pre-beta2 implementation files have been retired; only a few explicit old import paths remain as deprecated compatibility entrypoints.

```
store.py              # SQLite-backed MemoryStore, frontmatter I/O, config, paths (~1024 lines)
search.py             # SearchIndex: BM25 + embedding + fusion + Hebbian boost (~562 lines)
graph.py              # GraphIndex: SQLite Hebbian graph, PageRank, spreading activation (~324 lines)
reflect.py            # ReflectionEngine: raw_chunk default, dependency injection (~403 lines)
context.py            # Context assembly: Palace mode only (~145 lines)
runtime_tools.py      # Canonical 12 base SRH tool handlers
runtime_hooks.py      # Canonical hooks and slash commands
runtime_graph.py      # Canonical graph/health tool registration and graph compat surface
runtime_reflection.py # Canonical reflection runtime helpers
dashboard/plugin_api.py # FastAPI dashboard (14 endpoints) (~646 lines)
```

Full architecture documentation: `docs/research/beta2-architecture.md`
Code review & fixes: `docs/research/beta2-code-review.md`
Test coverage docs: `docs/testing/test-coverage.md`

**Retired pre-beta2 implementation files:**
```
core.py
late_binding.py
search/embed.py
query/cache.py
ahe_graph/__init__.py
graph/ahe_graph.py
graph/cluqi.py
graph/pagerank.py
graph/cross_zone.py
```

**Deprecated compatibility entrypoints kept for explicit old imports only:**
```
reflection/engine.py  # forwards to runtime_reflection.py
hooks/lifecycle.py    # forwards to runtime_hooks.py
tools/handlers.py     # forwards to runtime_tools.py
graph/compat.py       # forwards to runtime_graph.py
```

**Migration:**
```bash
# One-time memory index migration
python scripts/migrate_memory_index.py
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

# Run a specific test class or test
pytest tests/test_store.py::TestRebuildIndex -v
pytest tests/test_store.py::TestRebuildIndex::test_rebuild_empty_store -v

# Run with coverage
pytest tests/ --cov=. --cov-report=term-missing

# Run host-contract smoke script
python scripts/smoke_host_contract.py

# Run a specific test with warnings shown
pytest tests/test_store.py -v -W default

# Performance benchmark
python bench_latency.py
```

## Architecture

> **Beta2 architecture docs**: `docs/research/beta2-architecture.md` (definitive)
> **Original redesign**: `docs/research/1.0-beta2-redesign.md`
> **Code review**: `docs/research/beta2-code-review.md`
> **Test coverage**: `docs/testing/test-coverage.md`

### Module Dependency DAG

```
store.py              ← leaf module, no project imports
  └── python-frontmatter, tiktoken, sqlite3

search.py             ← imports store.py
  └── numpy, bm25s, cachetools, functools.lru_cache

graph.py              ← imports store.py (cross_zone only)
  └── sqlite3 (independent graph.db)

reflect.py            ← imports store.py + search.py + graph.py
  └── json (reflect-log.jsonl)

context.py            ← imports store.py + search.py
  └── pure functions, no state

runtime_reflection.py ← imports store.py + search.py

runtime_hooks.py      ← imports store.py + reflect.py + search.py

runtime_tools.py      ← imports store.py + search.py + reflect.py + runtime_hooks.py

runtime_graph.py      ← imports graph.py + store.py

__init__.py           ← explicit runtime registration, registers 17 tools
```

No circular imports. `late_binding.py` has been retired; runtime modules use explicit package delegates where old consumers need late-bound package state.

### Import Order Rules

Use the canonical runtime order:

1. `store.py` — no imports from other project modules (leaf module)
2. `search.py` — imports from store only
3. `graph.py` — imports store only where memory metadata is needed
4. `reflect.py` / `runtime_reflection.py` — imports store + search
5. `runtime_hooks.py`, `runtime_tools.py`, `runtime_graph.py` — host-facing runtime features
6. `__init__.py` — explicit runtime registration

Deprecated compatibility entrypoints must remain thin forwarders and must not regain implementation logic.

### Thread Safety

Key concurrency protections:

| Resource | Protection |
|----------|-----------|
| `MemoryStore` mutations | `RLock` on all public mutation methods |
| `_session_messages` dict | `threading.Lock` in lifecycle hooks |
| `_turns_since_reflect` counter | `threading.Lock` (micro-reflection cadence) |
| `_reflect_log_lock` | Covers both read and write paths |
| Embedding cache | `threading.Lock` on all cache operations |
| GraphIndex / graph compat operations | SQLite WAL plus runtime graph locks where singletons are shared |
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

- 12 base tools in `runtime_tools.py` (CRUD, palace navigation, reflection, skills, profile)
- 5 graph/health tools in `runtime_graph.py` (`srh_associate`, `srh_graph_retrieve`, `srh_graph_stats`, `srh_graph_viz`, `srh_memory_health`) — registered through `__init__.py` because they require graph-manager initialization at plugin load time

## Key Patterns

### Test Fixtures (conftest.py)

Tests use `pytest` with shared fixtures:
- `temp_dir` — temp directory for isolated file operations
- `temp_store` — MemoryStore with temp root (auto-packages `__init__.py` into `sys.modules`)
- `temp_graph` — GraphStore backed by temp SQLite (with Windows cleanup retry)
- `seeded_store` — MemoryStore with 5 pre-loaded memories for ranking/retrieval tests

### Test Loading Pattern (Runtime Modules)

Tests for runtime modules (`store.py`, `search.py`, `graph.py`, `reflect.py`, `context.py`) use `importlib.util.spec_from_file_location` to avoid package-relative import issues when loading standalone files. Each test file has a block like:

```python
import importlib.util
_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("_mod_name", str(_REPO / "store.py"))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["_mod_name"] = _mod
_spec.loader.exec_module(_mod)
```

Production code includes `try/except ImportError` fallback blocks for both package-relative and direct loading. `reflect.py` and `context.py` have this pattern; `search.py` and `graph.py` also have it. When editing these files, preserve the fallback.

### Windows Cleanup

Temp directory fixtures include retry loops with `shutil.rmtree` to handle SQLite file locking on Windows:

```python
for _attempt in range(5):
    try:
        shutil.rmtree(tmpdir)
        break
    except PermissionError:
        time.sleep(0.1)
shutil.rmtree(tmpdir, ignore_errors=True)
```

### Search Cache Invalidation

`SearchIndex` has three layers of lazy caching that must all be cleared after mutations:
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

`test_dashboard.py` mocks `mem_reflection_hermes` via `sys.modules` before loading `plugin_api.py`. `plugin_api.py` checks `sys.modules` first to avoid overwriting the mock:

```python
# In plugin_api.py
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

The runtime graph layer is an **associative co-activation graph** (Hebbian), not an entity-relation knowledge graph. Edges mean "these memories were used together", not typed factual relationships. The graph tracks co-occurrence strength, PageRank, spreading activation, and cross-zone relationships through `GraphIndex`/`runtime_graph.py`.

### CJK Awareness

Token estimation is CJK-aware (3 bytes/token for CJK, 4 bytes/token for Latin). Conflict threshold adapts: 0.55 for CJK-heavy content, 0.65 for Latin-heavy content.

### Reflection Session Exclusion

Newly created memory IDs during reflection are tracked in a session-local set (`_current_session_memory_ids`). This prevents the feedback loop where reflection sees its own just-written output as a duplicate or conflict. The set is cleared on session start and session end.

## Key Conventions

- Timestamps: always `datetime.now(timezone.utc)` — never bare `datetime.now()`
- Hashing: SHA-256 only
- SQLite concurrent writes: WAL mode + `INSERT OR REPLACE`
- Supersedes chains for version lineage (not for mere related memories)
- `@lru_cache` on file-backed config: avoid — use mtime-aware cache instead (see `store.py` `load_config`)
- Config writes in `autotrigger_manage.py`: only on `start`/`bootstrap`, never on `status`/`stop`
- Silent error swallowing: use `logger.warning` (not `logger.debug`) for all failure paths that could indicate data loss or degraded functionality
