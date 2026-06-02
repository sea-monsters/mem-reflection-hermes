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

## Module Layout (v0.9.2-beta2)

| Module | Lines | Responsibility | Imports From |
|--------|-------|----------------|-------------|
| `core.py` | ~1,051 | MemoryStore, SkillStore, LoadedMemory, LoadedSkill, config, paths, frontmatter fallback parser | — |
| `search/embed.py` | ~501 | ONNX embedding engine, cosine similarity, intent classification | core |
| `reflection/engine.py` | ~1,518 | Micro/full reflection, auto-rebalance, profile compilation, audit logging | core, embed |
| `hooks/lifecycle.py` | ~368 | Session hooks (on_session_start/end, pre_llm_call, post_tool_call), slash commands | core, embed, reflection |
| `tools/handlers.py` | ~1,019 | 12 core SRH tool handlers exposed to Hermes Agent | core, embed, reflection, hooks |
| `graph/ahe_graph/__init__.py` | ~687 | SQLite-backed Hebbian graph memory layer | core |
| `graph/cluqi.py` | ~287 | Cross-layer unified query orchestration | core, ahe_graph |
| `query/cache.py` | ~211 | Query template cache with TTL result caching | core |
| `graph/cross_zone.py` | ~132 | Cross-zone graph analysis helpers | core, ahe_graph |
| `graph/pagerank.py` | ~101 | PageRank centrality scoring for graph nodes | ahe_graph |
| `dashboard/plugin_api.py` | ~638 | FastAPI dashboard routes (14 endpoints) | core, ahe_graph, cluqi, query_cache, cross_zone, pagerank |
| `__init__.py` | ~1,792 | Registration, 5 graph/health tools, exports, backward compat, standalone bootstrap | all above |

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
