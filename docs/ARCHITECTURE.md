# Architecture

## System Overview

```
┌──────────────────────────────────────────────────────┐
│                 Hermes Agent Session                  │
├──────────────────────────────────────────────────────┤
│  pre_llm_call hook (v1.4: stable/dynamic split)       │
│    ├── Inject palace index (zone map)                 │
│    ├── Inject compiled profile                        │
│    ├── Inject stable context (pinned + always-active) │
│    ├── Inject dynamic context (relevant + triggered)  │
│    ├── Timeout-protected assembly (8s, stable-only FB)│
│    └── Graded compression under token pressure        │
├──────────────────────────────────────────────────────┤
│  post_tool_call hook (v0.16.0 enhanced)               │
│    ├── Bridge Dir A: memory tool → plugin mirror      │
│    ├── Record tool effectiveness (status/duration)    │
│    ├── Update memory stats                            │
│    └── Build runtime graph associations               │
├──────────────────────────────────────────────────────┤
│  api_request_error hook (v0.16.0 telemetry)           │
│    ├── Track API error count per session              │
│    └── Log threshold crossings (1/5/10/25/50)         │
├──────────────────────────────────────────────────────┤
│  subagent_start/stop hook (v0.16.0 telemetry)         │
│    ├── Track concurrent subagent count                │
│    └── Record subagent lifecycle for reflection       │
├──────────────────────────────────────────────────────┤
│  on_session_reset hook (v0.16.0 lifecycle)            │
│    ├── Log session rotation (old→new)                 │
│    └── Clean up per-session state                     │
├──────────────────────────────────────────────────────┤
│  on_session_end hook                                  │
│    ├── Full reflection pipeline                       │
│    ├── Graph decay                                    │
│    ├── Generate skill candidates                      │
│    ├── Write session summary + episode compaction     │
│    ├── Memory curator (v1.2) — stale/similar/archive  │
│    └── Session checkpoint (v1.4) — pending recovery   │
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

## Module Layout (v1.5)

| Module | Lines | Responsibility | Imports From |
|--------|-------|----------------|-------------|
| `core/store.py` | canonical | MemoryStore, SkillStore, frontmatter, config, paths, lineage, BM25 helpers | — |
| `core/search.py` | canonical | SearchIndex, BM25/embedding fusion, query templates, result cache, intent helpers, explain | store |
| `core/graph.py` | canonical | GraphIndex, Hebbian edges, PageRank, cross-zone analysis, spreading activation | store |
| `core/config.py` | canonical (v1.4) | Typed config models, diagnostics, validation | store |
| `core/backend.py` | canonical (v1.4) | Backend capability abstraction (SearchBackendLike) | — |
| `reflection/engine.py` | canonical | ReflectionEngine, raw_chunk default, fact extraction | store |
| `reflection/runtime.py` | canonical | _run_full_reflection, _run_micro_reflection, audit logging, compaction | store, search, reflection/engine |
| `memory/curator.py` | canonical (v1.2) | 5-phase curation: TTL/staleness, supersedes archive, similarity detection, orphan cleanup, cold storage | store, memory/bridge |
| `memory/bridge.py` | canonical (v1.1) | Bidirectional sync between plugin MemoryStore and host builtin memory | store |
| `memory/context.py` | canonical (v1.4) | Context assembly: stable/dynamic split, token budget, skill matching, graded compression | store, search |
| `runtime/tools.py` | canonical | 7 base SRH tool handlers (write, search, delete, palace, reflect, skill, compile) | store, search, reflection |
| `runtime/hooks.py` | canonical | Session hooks (start/end/pre_llm/post_tool/reset/api_error/subagent) and slash commands | store, reflection, search, memory |
| `runtime/graph.py` | canonical | 5 graph/health tools + graph manager singleton | core/graph, store |
| `runtime/checkpoint.py` | canonical (v1.4) | Atomic session checkpoint, pending-stage recovery, corrupt backup | store |
| `web/api.py` | canonical | FastAPI dashboard routes (15 endpoints) backed by store/search/runtime graph/curator APIs | package runtime services |
| `tools/handlers.py`, `hooks/lifecycle.py`, `graph/compat.py`, `reflection/engine.py` | deprecated compat | Explicit old import paths forwarding to runtime modules | runtime/* |

**Tool split**: 7 base tools live in `runtime/tools.py`; 5 graph/health tools (`srh_associate`, `srh_graph_retrieve`, `srh_graph_stats`, `srh_graph_viz`, `srh_memory_health`) are registered by `runtime/graph.py` through the package `register(ctx)` path. All 12 tools are declared in `plugin.yaml`.

### Import Order Rules (v1.4)

When adding new functionality, respect the module boundaries:

1. **`core/store.py`**: Data models, store logic, config, paths — no Hermes dependencies
2. **`core/search.py`**: Search and embedding helpers — imports `core/store` only
3. **`core/graph.py`**: GraphIndex — imports `core/store` only where cross-zone analysis needs memory metadata
4. **`core/config.py`** / **`core/backend.py`**: Typed config and backend abstraction — imports `core/store`
5. **`reflection/engine.py`** / **`reflection/runtime.py`**: Reflection pipelines — import `core/store` + `core/search`
6. **`memory/curator.py`** / **`memory/bridge.py`**: Curation and host sync — import `core/store` (`memory/curator` also imports `memory/bridge` for body refinement)
7. **`memory/context.py`**: Context assembly — imports `core/store` + `core/search`
8. **`runtime/tools.py`** / **`runtime/hooks.py`** / **`runtime/graph.py`** / **`runtime/checkpoint.py`**: Host-facing runtime features — depend on canonical services
9. **`web/api.py`**: Dashboard — imports package runtime services via `sys.modules` fallback
10. **`__init__.py`**: Registration and runtime singletons — imports all canonical modules explicitly

Avoid circular dependencies. Deprecated compatibility files should forward to runtime modules and not regain implementation logic.

### Thread Safety (v1.5)

Key concurrency protections present in v1.4-beta:

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
| Cold store writes | `threading.Lock` (`_cold_store_lock`) guards JSONL append/rewrite |
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

## Entity Recall Layer (v1.4)

The plugin implements entity-based recall to improve retrieval of memories
containing proper nouns, file paths, package names, and other identifiers
that may not match well via BM25 or embedding similarity alone.

### Extraction Pipeline (`core/store.py:extract_entities`)

Entity extraction uses a **regex-first + optional spaCy** architecture,
avoiding mandatory heavy dependencies while providing high-precision patterns:

| Pattern | Regex | Example | Weight |
|---------|-------|---------|--------|
| `file_path` | `(?:[A-Za-z]:\\|/)?(?:[\w.\-]+[\\/])+[\w.\-]+\.\w+` | `src/providers/http/index.test.ts` | 1.0 |
| `code` | `` `([^`]{2,120})` `` | `` `ToolRunner.execute` `` | 0.9 |
| `quoted` | `\"([^\"]{2,120})\"|'([^']{2,120})'` | `"config.yaml"` | 0.8 |
| `package` | `\b(?:[A-Za-z_]\w*\.){1,}[A-Za-z_]\w*\b` | `numpy.linalg.norm` | 0.75 |
| `proper` | `\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b` | `HttpRequestHandler` | 0.7 |
| `compound` | `\b[a-z0-9]+(?:[-_/][a-z0-9]+){1,}\b` | `auth-middleware` | 0.65 |
| `spacy` | Optional spaCy NER (if `en_core_web_sm` available) | Named entities | 0.6 |

**Weight hierarchy**: Higher weights reflect higher extraction confidence.
File paths (explicit filesystem references) are most reliable; compound
terms (loosely structured identifiers) are least specific.

### Normalization and Dedup

Extracted entities are normalized (lowercase + whitespace collapse) before
indexing. This prevents `"Config.yaml"` and `"config.yaml"` from being
double-counted while preserving the original text for display.

### Cross-Reference with mem0

mem0 uses **spaCy-only** NER with a generic-heads filter list (see
`mem0/utils/entity_extraction.py`). SRH's regex-first approach provides:

- **No mandatory dependency**: Works without spaCy; falls back gracefully.
- **Finer weight granularity**: Per-pattern confidence vs uniform NER output.
- **Code-aware patterns**: Backticks, file paths, and package names are
  first-class entities rather than generic proper nouns.

### Search Integration (`core/store.py:compute_entity_boosts`)

When `entity.enabled=true` (default), query entities are extracted and
matched against the `entity_links` table. Matching memories receive an
`entity_boost` proportional to `entity_weight * link_weight`. The boost
appears in `fusion_search_explain` output under the `entity_boost` and
`entity_hits` fields.

## Context Compression Tiers (v1.4)

When token budget is insufficient for full context, the plugin applies
graded compression to the **dynamic section** (relevant memories, triggered
skills, episode summaries). The **stable section** (pinned memories,
always-active skills) is never compressed.

### Compression Levels (`memory/context.py:_build_dynamic_context_parts`)

| Level | Trigger | Memory Token Budget | Skill Detail | Episode Detail |
|-------|---------|--------------------:|--------------|----------------|
| `none` | Budget sufficient | 100 tokens | `mild` | `mild` |
| `mild` | Budget < 85% of needed | 80 tokens | `mild` | `mild` |
| `aggressive` | Budget < 100% of needed | 40 tokens | `aggressive` | `aggressive` |
| `emergency` | Budget exhausted | 18 tokens | `emergency` | `emergency` |

**Design**: Higher levels progressively truncate content rather than drop
entire sections. Emergency mode preserves minimal previews for context
continuity.

### Episode Summary Compression (`memory/context.py:_build_compacted_episode_block`)

Compacted episode summaries have separate tier limits:

| Detail Level | Max Items | Max Chars per Item |
|--------------|----------:|-------------------:|
| `mild` | 10 | 200 |
| `aggressive` | 6 | 120 |
| `emergency` | 4 | 80 |

Episodes exceeding `max_chars` are truncated with `...` appended. If more
than `max_items` exist, a trailing `... (N more)` line indicates the count.

**Cross-reference**: hy-memory context offload uses similar mild/aggressive/emergency
tier semantics for L2→L3→L4 degradation. SRH adapts this pattern for episode summaries.

### Compression Control

Compression is controlled by:
- `context.compression.enabled` (default: `true`) — disables all compression when `false`
- `context.token_budget` (default: `2000`) — total context token budget
- `context.compression.mild_ratio` / `aggressive_ratio` / `emergency_ratio` — thresholds for level selection

## v0.16.0 Enhanced Telemetry Hooks

Starting with Hermes Agent v0.16.0 (Jun 5, 2026), the plugin system supports
richer observer-style hooks. The mem-reflection-hermes plugin leverages
all v0.16 hook points with zero-cost when no subscriber is active (`has_hook()` gate).

### Hook Registration (`runtime_hooks.py`)

| Hook Name | Handler | Purpose | v0.16+ kwargs Consumed |
|-----------|---------|---------|----------------------|
| `on_session_start` | `_on_session_start` | Reset turn counter, clear session exclusion set | — |
| `on_session_end` | `_on_session_end` | Full reflection, graph decay, episode compaction, **memory curator**, session cleanup | `session_id`, `reason` (v0.16: `"shutdown"` \| `"session_expired"` \| `"new_session"`) |
| `on_session_reset` | `_on_session_reset` | Log session rotation, clean per-session state (v0.16) | `reason`, `old_session_id`, `new_session_id` |
| `pre_llm_call` | `_pre_llm_call` | Inject layered memory context, trigger micro-reflection | `messages`, `user_message`, `session_id`, `ctx` |
| `post_tool_call` | `_post_tool_call` | Bridge Dir A, record effectiveness, build graph associations | `tool_name`, `args`, `result`, **`status`**, **`duration_ms`**, **`session_id`**, **`turn_id`** (v0.16 enhanced) |
| `api_request_error` | `_on_api_request_error` | Track API error count per session for reflection context (v0.16) | `session_id`, `error` |
| `subagent_start` | `_on_subagent_start` | Track concurrent subagent count, record start time (v0.16) | `session_id` |
| `subagent_stop` | `_on_subagent_stop` | Track total subagent count for reflection summary (v0.16) | `session_id` |

### Enhanced `post_tool_call` Behavior

In v0.16.0, ``_emit_post_tool_call_hook`` is called for **all** tool paths
including agent-runtime tools (``memory``, ``todo``, ``session_search``,
``clarify``, ``delegate_task``). The plugin uses the new kwargs:

- **`status`**: If ``"error"``, skips graph enrichment (no point associating
  memories from a failed tool call) and bypasses Dir A bridge for ``memory``
  tool writes.
- **`duration_ms`**: Slow calls (>10s) are logged with the ``turn_id`` tag
  for diagnostics.
- **`turn_id`**: Stable per-turn correlation ID (format:
  ``{session_id}:{task_id}:{hex8}``) appended to diagnostic log messages.

### Subagent Lifecycle Tracking

The plugin tracks both ``subagent_start`` and ``subagent_stop`` events,
maintaining a per-session state bag:

```python
_session_states[session_id] = {
    "api_error_count": N,
    "subagent_count": N,
    "_subagent_active": N,     # concurrent subagents
    "_subagent_start_time": t,  # latest start timestamp
    "created_at": t,
}
```

These stats are harvested in ``on_session_end`` and included in the reflection
log for richer session summaries.

