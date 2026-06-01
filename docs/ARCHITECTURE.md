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

## Module Layout (v0.9.2-beta)

| Module | Lines | Responsibility | Imports From |
|--------|-------|----------------|-------------|
| `core.py` | ~652 | MemoryStore, SkillStore, LoadedMemory, LoadedSkill, config, paths | — |
| `search/embed.py` | ~411 | ONNX embedding engine, cosine similarity, intent classification | core |
| `reflection/engine.py` | ~1,085 | Micro/full reflection, auto-rebalance, profile compilation | core, embed |
| `hooks/lifecycle.py` | ~276 | Session hooks (on_session_start/end, pre_llm_call, post_tool_call) | core, embed, reflection |
| `tools/handlers.py` | ~830 | 12 SRH tool handlers exposed to Hermes Agent | core, embed, reflection, hooks |
| `graph/ahe_graph/__init__.py` | ~687 | SQLite-backed Hebbian graph memory layer | core |
| `graph/cluqi.py` | ~217 | Cross-layer unified query orchestration | core, ahe_graph |
| `query/cache.py` | ~163 | Query template cache with TTL result caching | core |
| `graph/cross_zone.py` | ~112 | Cross-zone graph analysis helpers | core, ahe_graph |
| `graph/pagerank.py` | ~81 | PageRank centrality scoring for graph nodes | ahe_graph |
| `dashboard/plugin_api.py` | ~487 | FastAPI dashboard routes (13 endpoints) | core, ahe_graph, cluqi, query_cache, cross_zone, pagerank |
| `__init__.py` | ~1,416 | Registration, exports, graph tools, backward compat, standalone bootstrap | all above |

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
