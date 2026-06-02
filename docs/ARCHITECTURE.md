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
│    └── Build ahe_graph associations                   │
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

For the follow-up implementation plan targeting the identified design gaps,
see [PLAN_0_9_2_BETA2.md](PLAN_0_9_2_BETA2.md).

## Module Layout (v1.0-beta)

| Module | Lines | Responsibility | Imports From |
|--------|-------|----------------|-------------|
| `core.py` | ~1,078 | MemoryStore, SkillStore, LoadedMemory, LoadedSkill, config, paths, frontmatter fallback parser, BM25 search | — |
| `search/embed.py` | ~504 | ONNX embedding engine, cosine similarity, intent classification, LRU embed cache | core |
| `reflection/engine.py` | ~1,692 | Micro/full/embedding/raw-chunk reflection, auto-rebalance, profile compilation, audit logging, session exclusion | core, embed |
| `hooks/lifecycle.py` | ~423 | Session hooks (start/end/pre_llm_call/post_tool_call), slash commands, graph manager, micro-reflection cadence | core, embed, reflection |
| `tools/handlers.py` | ~966 | 12 core SRH tool handlers exposed to Hermes Agent | core, embed, reflection, hooks |
| `graph/ahe_graph.py` | ~1,024 | SQLite-backed Hebbian graph memory, Ebbinghaus decay, spread activation, association engine | core |
| `graph/cluqi.py` | ~296 | Cross-layer unified query orchestration (Memory + Graph + Supersedes) | core, ahe_graph |
| `query/cache.py` | ~213 | Query templates and TTL-based result cache with LRU eviction | core |
| `graph/cross_zone.py` | ~133 | Cross-zone graph analysis (bridges, centrality, recommendations) | core, ahe_graph |
| `graph/pagerank.py` | ~116 | PageRank centrality for graph nodes | ahe_graph |
| `dashboard/plugin_api.py` | ~646 | FastAPI dashboard (14 endpoints) | core, ahe_graph, cluqi, query_cache, cross_zone, pagerank |
| `late_binding.py` | ~38 | Shared late-binding symbol resolution with thread-safe cache | — |
| `__init__.py` | ~1,870 | Registration, 5 graph/health tools, exports, backward compat, standalone bootstrap | all above |

**Tool split**: 12 core tools live in `tools/handlers.py`; 5 graph/health tools (`srh_associate`, `srh_graph_retrieve`, `srh_graph_stats`, `srh_graph_viz`, `srh_memory_health`) are registered in `__init__.py` because they require graph-manager initialization at plugin load time.

### Import Order Rules

When adding new functionality, respect the module boundaries:

1. **core.py**: Data models, store logic, config — no Hermes dependencies
2. **search/embed.py**: Embedding engine — imports from core only
3. **reflection/engine.py**: Reflection pipelines — imports from core + embed
4. **hooks/lifecycle.py**: Session hooks — imports from core + embed + reflection
5. **tools/handlers.py**: Tool handlers — imports from all above modules
6. **__init__.py**: Registration, graph tools, and exports — imports from all modules

Avoid circular dependencies. Use function-level late-binding if cross-module
references are needed at import time.

### Thread Safety (v1.0-beta)

Key concurrency protections added in v1.0-beta:

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
| Late-binding cache | `threading.Lock` in `late_binding.py` |

## Slash Commands

Registered in `hooks/lifecycle.py` via `_register_slash_commands`:

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
