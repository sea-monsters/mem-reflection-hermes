# Tools Reference

Current SRH tool surface: **13 registered tools** (v1.6).

The 13 tools are registered in `__init__.py::register(ctx)` and declared in `plugin.yaml`.

> **Graph semantics note**: The runtime graph layer is an **associative co-activation graph** (Hebbian), not an entity-relation knowledge graph. Edges mean "these memories were used together", not "these entities have a typed factual relationship".

## Memory Operations (4)

### `srh_memory_search`

Search active memories by relevance.

```python
srh_memory_search(query="Python error handling", k=5)
srh_memory_search(query="部署流程", k=5, zone="work")  # zone filter
srh_memory_search(query="Python error handling", k=5, explain=True)  # v1.4: return score breakdown
srh_memory_search(query="alpha", k=5, filters={"user_id": "u1"})  # v1.6: scoped filter
```

**v1.6 scoped filters**: Pass `filters={"user_id": ..., "agent_id": ..., "run_id": ...}` to restrict results. `None` means `IS NULL` (universally visible). Combined filters use AND logic.

### `srh_memory_write`

Write a new memory or update existing.

```python
srh_memory_write(
    body="Always use anyhow for app-level error handling",
    scope="user",
    confidence="high",
    tags=["rust", "error-handling"],
    pinned=True,
    supersedes=[],
    zone="general",
    user_id="alice",
    agent_id="claude",
    run_id="sess-001",
)
```

**v1.6 scope fields**: `user_id`, `agent_id`, and `run_id` are optional string fields stored in both frontmatter and the SQLite index. Memories without scope fields are universally visible (NULL in DB).

### `srh_memory_delete`

Delete a memory by ID, or batch-delete by scope filters.

```python
srh_memory_delete(id="mem_abc123", scope="user")
srh_memory_delete(id="batch", filters={"run_id": "sess-001"})  # v1.6: batch delete
```

**v1.6 batch delete**: When `filters` is provided, the tool deletes all memories matching the scope filter and returns `{deleted_count: N}`. The `id` field is still required in the schema for backward compatibility.

### `srh_memory_history`

Trace the supersedes chain (version lineage) for a memory.

```python
srh_memory_history(id="mem_abc123", max_depth=5)
srh_memory_history(id="mem_abc123", include_events=True)  # v1.6: audit events
srh_memory_history(id="mem_abc123", include_events=True, event_types=["UPDATE", "DELETE"])
srh_memory_history(id="mem_abc123", include_events=True, session_id="sess-001")
```

**v1.6 audit events**: When `include_events=True`, the response includes a chronological list of events. Event types: `ADD`, `UPDATE`, `DELETE`, `SUPERSEDE`, `PIN`, `UNPIN`. Each event records `old_body`/`new_body`, `old_frontmatter`/`new_frontmatter`, `session_id`, `actor_id`, and an ISO `created_at` timestamp. Filter by `event_types` or `session_id` to narrow the timeline.

**Performance note**: The event log is append-only and indexed by `memory_id`, `session_id`, and `created_at`. Event queries for a single memory are O(1) — no full table scan.

## Palace Navigation (1)

### `srh_palace_navigate`

Topic-based recall within the Memory Palace, optionally scoped to a zone. Under the hood this delegates to `srh_palace_recall`.

```python
srh_palace_navigate(topic="editor preference", zone="work", limit=5)
```

> **Note**: Earlier versions exposed separate tools (`srh_palace_zones`, `srh_palace_read_zone`, `srh_palace_search`, `srh_palace_rebalance`, `srh_palace_recall`). These have been consolidated into the single `srh_palace_navigate` tool surface. The internal functions remain in `runtime/tools.py` for backward-compat dashboard routes.

## Reflection & Profile (2)

### `srh_reflect_now`

Trigger reflection pipeline manually.

```python
srh_reflect_now(messages=[...], mode="full")
```

Modes: `full` (session-end structured summary), `micro` (per-turn background), `embedding` (vector-only, zero LLM).

### `srh_compile_profile`

Compile memories into a structured profile.

```python
srh_compile_profile(mode="profile")       # profile.md format
srh_compile_profile(mode="palace_index")  # compiled palace index
srh_compile_profile(mode="zone")          # per-zone summaries
```

## Skills (1)

### `srh_skill_query`

Query skills by token overlap.

```python
srh_skill_query(query="rust async", k=3)
```

> **Note**: Internally delegates to `srh_skill_search`. The `_tool_srh_skill_search` internal function is used by both the tool handler and slash command logic.

## Graph Memory (4)

### `srh_associate`

Associate memories via co-activation edges.

```python
srh_associate(memory_ids=["mem_a", "mem_b"], relation="co_occurs")
```

### `srh_graph_retrieve`

Retrieve associative neighbors (Hebbian co-activation propagation).

```python
srh_graph_retrieve(seed_ids=["mem_a"], max_results=10, tier="rank")
```

### `srh_graph_stats`

Get associative graph statistics.

```python
srh_graph_stats(format="nodes", depth=2)
```

### `srh_graph_viz`

Generate graph visualization data.

```python
srh_graph_viz(format="adjacency", depth=2)
```

## Health Metrics (1)

### `srh_memory_health`

Get memory health metrics and recommendations.

```python
srh_memory_health()
```

Returns:
- `duplicate_clusters`: count of near-duplicate memory clusters
- `longest_supersedes_chain`: length of deepest version lineage
- `supersedes_cycle_count`: cycles in supersedes chains (should be 0)
- `stale_high_rank_count`: superseded memories still ranked high
- `expired_count`: memories past their `valid_until` date
- `reflection_acceptance_rate`: ratio of accepted to total audit entries

## Tool Summary Table

| Tool | Category | Handler | Notes |
|------|----------|---------|-------|
| `srh_memory_search` | Memory | `runtime/tools.py` | v1.6: `filters` for scoped queries |
| `srh_memory_write` | Memory | `runtime/tools.py` | v1.6: `user_id`/`agent_id`/`run_id` |
| `srh_memory_delete` | Memory | `runtime/tools.py` | v1.6: `filters` for batch delete |
| `srh_memory_history` | Memory | `runtime/tools.py` | v1.6: `include_events` + event filtering |
| `srh_palace_navigate` | Palace | `runtime/tools.py` | Delegates to `_tool_srh_palace_recall` |
| `srh_reflect_now` | Reflection | `runtime/tools.py` | |
| `srh_skill_query` | Skills | `runtime/tools.py` | Delegates to `_tool_srh_skill_search` |
| `srh_compile_profile` | Profile | `runtime/tools.py` | |
| `srh_associate` | Graph | `runtime/graph.py` | |
| `srh_graph_retrieve` | Graph | `runtime/graph.py` | |
| `srh_graph_stats` | Graph | `runtime/graph.py` | |
| `srh_graph_viz` | Graph | `runtime/graph.py` | |
| `srh_memory_health` | Health | `runtime/graph.py` | |

**8 base tools** live in `runtime/tools.py`; **5 graph/health tools** live in `runtime/graph.py`.
