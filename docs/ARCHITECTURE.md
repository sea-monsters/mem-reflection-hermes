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

## Module Layout (v0.8.0)

| Module | Lines | Responsibility | Imports From |
|--------|-------|----------------|-------------|
| `core.py` | ~791 | MemoryStore, SkillStore, LoadedMemory, LoadedSkill, config, paths | — |
| `embed.py` | ~484 | ONNX embedding engine, cosine similarity, intent classification | core |
| `reflection.py` | ~1,248 | Micro/full reflection, auto-rebalance, profile compilation | core, embed |
| `hooks.py` | ~335 | Session hooks (on_session_start/end, pre_llm_call, post_tool_call) | core, embed, reflection |
| `tools.py` | ~945 | 17 SRH tool handlers exposed to Hermes Agent | core, embed, reflection, hooks |
| `__init__.py` | ~1,588 | Registration, exports, backward compat, standalone bootstrap | all above |

### Import Order Rules

When adding new functionality, respect the module boundaries:

1. **core.py**: Data models, store logic, config — no Hermes dependencies
2. **embed.py**: Embedding engine — imports from core only
3. **reflection.py**: Reflection pipelines — imports from core + embed
4. **hooks.py**: Session hooks — imports from core + embed + reflection
5. **tools.py**: Tool handlers — imports from all above modules
6. **__init__.py**: Registration and exports — imports from all modules

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
