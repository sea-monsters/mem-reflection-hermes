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
│    └── Write session summary + episode compaction     │
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

## Module Layout

| Module | Lines | Responsibility | Imports From |
|--------|-------|----------------|-------------|
| `store.py` | canonical | MemoryStore, SkillStore, frontmatter, config, paths, lineage, BM25 helpers | — |
| `search.py` | canonical | SearchIndex, BM25/embedding fusion, intent helpers | store |
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

### Thread Safety

Key concurrency protections:

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

## v0.16.0 Enhanced Telemetry Hooks

Starting with Hermes Agent v0.16.0 (Jun 5, 2026), the plugin system supports
richer observer-style hooks. The mem-reflection-hermes plugin leverages
all v0.16 hook points with zero-cost when no subscriber is active (`has_hook()` gate).

### Hook Registration (`runtime_hooks.py`)

| Hook Name | Handler | Purpose | v0.16+ kwargs Consumed |
|-----------|---------|---------|----------------------|
| `on_session_start` | `_on_session_start` | Reset turn counter, clear session exclusion set | — |
| `on_session_end` | `_on_session_end` | Full reflection, graph decay, episode compaction, session cleanup | `session_id`, `reason` (v0.16: `"shutdown"` \| `"session_expired"` \| `"new_session"`) |
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

