# Architecture

## System Overview

```
┌──────────────────────────────────────────────────────┐
│                 Hermes Agent Session                  │
├──────────────────────────────────────────────────────┤
│  pre_llm_call hook                                    │
│    ├── Inject palace index (zone map)                 │
│    ├── Inject compiled profile                        │
│    ├── Inject triggered/always skills                 │
│    └── Inject pinned memories                         │
├──────────────────────────────────────────────────────┤
│  post_tool_call hook                                  │
│    ├── Record tool effectiveness                      │
│    ├── Update memory stats                            │
│    └── Build runtime graph associations               │
├──────────────────────────────────────────────────────┤
│  on_session_end hook                                  │
│    ├── Full reflection pipeline                       │
│    ├── Generate skill candidates                      │
│    └── Write session summary                          │
├──────────────────────────────────────────────────────┤
│  Background Tasks                                     │
│    ├── Micro-reflection queue (backpressure)          │
│    ├── Async memory write thread                      │
│    └── Async stat flush thread                        │
└──────────────────────────────────────────────────────┘
```

For a design-level judgment of the supersedes chain + graph memory model,
see [DESIGN_EVALUATION.md](DESIGN_EVALUATION.md).

For the historical follow-up implementation plan targeting the v0.9.2 design gaps,
see [PLAN_0_9_2_BETA2.md](PLAN_0_9_2_BETA2.md).

## Module Layout (v1.0-beta3)

| Module | Lines | Responsibility | Imports From |
|--------|-------|----------------|-------------|
| `store.py` | canonical | MemoryStore, SkillStore, frontmatter, config, paths, lineage, BM25 helpers | — |
| `search.py` | canonical | SearchIndex, BM25/embedding fusion, query templates, result cache, intent helpers | store |
| `graph.py` | canonical | GraphIndex, Hebbian edges, PageRank, cross-zone analysis, spreading activation | store |
| `reflect.py` / `runtime_reflection.py` | canonical | ReflectionEngine, micro/full/raw-chunk reflection, audit logging, skill approval helpers | store, search |
| `runtime_hooks.py` | canonical | Session hooks and slash command registration | store, reflect, search |
| `runtime_tools.py` | canonical | 12 base SRH tool handlers and hook registration | store, search, reflect, runtime_hooks |
| `runtime_graph.py` | canonical | Graph compatibility surface plus 5 graph/health tool registrations | graph, store |
| `dashboard/plugin_api.py` | canonical | FastAPI dashboard routes backed by store/search/runtime graph APIs | package runtime services |
| `tools/handlers.py`, `hooks/lifecycle.py`, `graph/compat.py`, `reflection/engine.py` | deprecated compat | Explicit old import paths forwarding to runtime modules | runtime_* |
| `__init__.py` | canonical entry | Plugin registration, runtime singletons, package bootstrap | store, search, graph, reflect, runtime_* |

**Tool split**: 12 base tools live in `runtime_tools.py`; 5 graph/health tools (`srh_associate`, `srh_graph_retrieve`, `srh_graph_stats`, `srh_graph_viz`, `srh_memory_health`) are registered by `runtime_graph.py` through the package `register(ctx)` path.

### Import Order Rules

When adding new functionality, respect the module boundaries:

1. **store.py**: Data models, store logic, config — no Hermes dependencies
2. **search.py**: Search and embedding helpers — imports from store only
3. **graph.py**: GraphIndex — imports store only where cross-zone analysis needs memory metadata
4. **reflect.py / runtime_reflection.py**: Reflection pipelines — import store + search
5. **runtime_hooks.py / runtime_tools.py / runtime_graph.py**: Host-facing runtime features — depend on canonical services
6. **__init__.py**: Registration and runtime singletons — imports canonical modules explicitly

Avoid circular dependencies. Deprecated compatibility files should forward to runtime modules and not regain implementation logic.

### Thread Safety (v1.0-beta3)

Key concurrency protections present in v1.0-beta3:

| Resource | Protection |
|----------|-----------|
| `MemoryStore` mutations | `RLock` on all public mutation methods |
| `_session_messages` dict | `threading.Lock` |
| `_turns_since_reflect` counter | `threading.Lock` |
| `_reflect_log_lock` | Covers both read and write paths |
| Embedding cache | `threading.Lock` on all cache operations |
| `_classify_intent_stats` | `threading.Lock` via `_bump_classify_intent_stat` |
| `_build_adjacency` | mtime check + DB query + cache update inside `self._lock` |
| `get_cache()` singleton | Double-checked locking |
| Runtime late binding | Package-level explicit runtime delegates; legacy `late_binding.py` is retired |

## Slash Commands

Registered in `runtime_hooks.py` via `register_commands(ctx)`:

| Command | Purpose |
|---------|---------|
| `/reflect` | Trigger full reflection manually |
| `/skills` | List skills; with query, search by token overlap |
| `/pending` | Show pending skill candidates awaiting approval |
| `/approve` | Approve a pending skill candidate |
| `/reject` | Reject a pending skill candidate |
| `/memories` | List recent memories with zone/confidence filter |
| `/compile` | Compile profile from current memory set |

## Context Layering

The `pre_llm_call` hook injects context in this priority order:

```
1. Pinned memories (always included)
2. Active index (zone-based relevance)
3. Triggered skills (per-turn matching)
4. Always-active skills (user-configured)
```

Each layer respects the `max_context_token_preference` budget. Token estimation
uses CJK-aware heuristics (3 bytes/token for CJK, 4 bytes/token for Latin).
