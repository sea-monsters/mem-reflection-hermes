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
│    ├── Memory curator (v1.7) — scoped maintenance, scope-aware reflection │
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

## Module Layout (v1.7)

| Module | Lines | Responsibility | Imports From |
|--------|-------|----------------|-------------|
| `core/scope.py` | canonical (v1.7) | ScopeIntent enum, `scope_from_context()`, `global_only_scope()`, `normalize_scope_filters()`, `build_scope_clauses()` — shared scope helpers for user_id/agent_id/run_id resolution | store |
| `core/store.py` | canonical | MemoryStore, SkillStore, frontmatter, config, paths, lineage, BM25 helpers, memory_events ledger | — |
| `core/search.py` | canonical | SearchIndex, BM25/embedding fusion, query templates, result cache, intent helpers, explain, scope-filtered recall; search-time Hebbian boost calls graph spread with `increment_step=False` | store |
| `core/graph.py` | canonical | GraphIndex, Hebbian edges, PageRank, cross-zone analysis, spreading activation with optional `increment_step` and `allowed_nodes` guards | store |
| `core/config.py` | canonical | Typed config models, diagnostics, validation | store |
| `core/backend.py` | canonical | Backend capability abstraction (SearchBackendLike) | — |
| `reflection/engine.py` | canonical (legacy API) | ReflectionEngine, raw_chunk default, fact extraction; now accepts optional `scope_filters` | store |
| `reflection/runtime.py` | canonical | _run_full_reflection, _run_micro_reflection, scope-aware reflection writes, audit logging, compaction | store, search, reflection/engine |
| `memory/curator/` | canonical (v1.7) | Composable action pipeline with scoped-by-default maintenance: ArchiveStale, CompactChains, ArchiveSuperseded, MergeSimilar, CleanOrphanEdges, GenerateReport; `admin_global` is explicit opt-in for full-store runs; `stop_on_error` + recovery journal added in round-5 | store, memory/bridge |
| `memory/bridge.py` | canonical | Bidirectional sync between plugin MemoryStore and host builtin memory | store |
| `memory/context.py` | canonical | Context assembly: stable/dynamic split, token budget, skill matching, graded compression | store, search |
| `runtime/tools.py` | canonical | 8 base SRH tool handlers (write, search, delete, history, palace, reflect, skill, compile); reflects normalized `srh_reflect_now` and unified `srh_memory_delete`/`srh_palace_read_zone` response shapes | store, search, reflection |
| `runtime/hooks.py` | canonical | Session hooks (start/end/pre_llm/post_tool/reset/api_error/subagent) and slash commands; scope propagation into reflection/context/compaction; legacy bare scope kwargs emit DeprecationWarning | store, reflection, search, memory |
| `runtime/graph.py` | canonical | 5 graph/health tools + graph manager singleton; `register_graph_features()` is called from `runtime/registration.py` to wire the `/graph` slash command, graph hook, and getter; supports optional `filters` for scope boundary | core/graph, store |
| `runtime/checkpoint.py` | canonical | Atomic session checkpoint, pending-stage recovery, corrupt backup | store |
| `runtime/registration.py` | canonical | Plugin registration entrypoint: wires hooks, commands, tools, post-delete callbacks, and calls `register_graph_features()` | hooks, schemas, tools, graph |
| `runtime/schemas.py` | canonical | Canonical JSON schemas for all 13 registered Hermes tools; `srh_graph_retrieve` accepts either `memory_ids` or deprecated `seed_ids` | — |
| `web/api.py` | canonical | FastAPI dashboard routes (15 endpoints) backed by store/search/runtime graph/curator APIs | package runtime services |
| `tools/handlers.py`, `hooks/lifecycle.py`, `graph/compat.py`, `reflection/engine.py` | deprecated compat | Explicit old import paths forwarding to runtime modules | runtime/* |

**Tool split**: 8 base tools live in `runtime/tools.py`; 5 graph/health tools (`srh_associate`, `srh_graph_retrieve`, `srh_graph_stats`, `srh_graph_viz`, `srh_memory_health`) are registered by `runtime/graph.py`. All 13 tools are declared in `plugin.yaml` and registered through the package `register(ctx)` path, which delegates to `runtime/registration.py` using schemas from `runtime/schemas.py`. `register_graph_features()` is invoked explicitly during registration with idempotency protection.

### Import Order Rules (v1.7)

When adding new functionality, respect the module boundaries:

1. **`core/store.py`**: Data models, store logic, config, paths — no Hermes dependencies
2. **`core/scope.py`**: Shared scope helper; resolves `user_id` / `agent_id` / `run_id` from host context — imports `core/store` (TYPE_CHECKING) only
3. **`core/search.py`**: Search and embedding helpers — imports `core/store` + `core/tokenization` + `core/models` + `core/scope`
4. **`core/graph.py`**: GraphIndex — imports `core/store` only where cross-zone analysis needs memory metadata
5. **`core/config.py`** / **`core/backend.py`**: Typed config and backend abstraction — imports `core/store`
5. **`reflection/extraction.py`** / **`reflection/supersedes_resolver.py`** / **`reflection/engine.py`** / **`reflection/runtime.py`**: Reflection pipelines — import `core/store` + `core/search`
6. **`memory/curator/`**: Composable action pipeline — imports `core/store` + `memory/bridge`; `memory/bridge.py` imports `core/store` only
7. **`memory/context.py`**: Context assembly — imports `core/store` + `core/search` + `core/config`
8. **`runtime/tools.py`** / **`runtime/hooks.py`** / **`runtime/graph.py`** / **`runtime/checkpoint.py`**: Host-facing runtime features — depend on canonical services
9. **`runtime/registration.py`**, **`runtime/schemas.py`**, **`runtime/state.py`**, **`runtime/helpers.py`**, **`runtime/_lb.py`**: Registration, schemas, singletons, late-binding
10. **`web/api.py`**: Dashboard — imports package runtime services via `sys.modules` fallback
11. **`__init__.py`**: Exports public API, backward-compat aliases, delegates `register()` to `runtime/registration`

Avoid circular dependencies. Deprecated compatibility files should forward to runtime modules and not regain implementation logic.

### Thread Safety (v1.7)

Key concurrency protections present in v1.5:

| Resource | Protection |
|----------|-----------|
| `MemoryStore` mutations | `RLock` on all public mutation methods |
| `_session_messages` dict | `threading.Lock` |
| `_turns_since_reflect` counter | `threading.Lock` |
| `_reflect_log_lock` | Covers both read and write paths |
| Embedding cache | `threading.Lock` on all cache operations |
| `_build_adjacency` | mtime check + DB query + cache update inside `self._lock` |
| `get_cache()` singleton | Double-checked locking |
| Cold store writes | `threading.Lock` (`_cold_store_lock`) guards JSONL append/rewrite |
| Stats stream writes | `threading.Lock` (`_stat_write_lock`) guards JSONL append and compaction truncate |
| Runtime late binding | Package-level explicit runtime delegates; legacy `late_binding.py` is retired |

## Slash Commands

Registered in `runtime_hooks.py` via `register_commands(ctx)` and in `runtime/graph.py` via `register_graph_features(ctx)`:

| Command | Purpose |
|---------|---------|
| `/reflect` | Trigger full reflection manually |
| `/skills` | List skills; with query, search by token overlap |
| `/pending` | Show pending skill candidates awaiting approval |
| `/approve` | Approve a pending skill candidate |
| `/reject` | Reject a pending skill candidate |
| `/memories` | List recent memories with zone/confidence filter |
| `/compile` | Compile profile from current memory set |
| `/graph` | Graph maintenance / info (v1.7: wired through `register_graph_features`) |

## Context Layering

The `pre_llm_call` hook injects context in two sections. The **stable**
section is preserved across turns to keep prompt caches warm; the
**dynamic** section varies per turn and is compressed under token pressure.

```
Stable section:
1. Pinned memories (always included)
2. Always-active skills (user-configured)

Dynamic section:
3. Active index (zone-based relevance)
4. Triggered skills (per-turn matching)
```

Each layer respects the `max_context_token_preference` budget. Token estimation
uses CJK-aware heuristics (3 bytes/token for CJK, 4 bytes/token for Latin).

## Entity Recall Layer

The plugin implements entity-based recall to improve retrieval of memories
containing proper nouns, file paths, package names, and other identifiers
that may not match well via BM25 or embedding similarity alone.

### Extraction Pipeline (`core/entities.py:extract_entities`)

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

### Search Integration (`core/store_methods.py:compute_entity_boosts`)

When `entity.enabled=true` (default), query entities are extracted and
matched against the `entity_links` table. Matching memories receive an
`entity_boost` proportional to `entity_weight * link_weight`. The boost
appears in `fusion_search_explain` output under the `entity_boost` and
`entity_hits` fields.

## Context Compression Tiers

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

