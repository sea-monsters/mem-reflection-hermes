# Tools Reference

Current SRH tool surface in this commit: 17 registered tools.

Graph-related capabilities are exposed both as SRH tools (`srh_associate`, `srh_graph_retrieve`, `srh_graph_stats`, `srh_graph_viz`) and through the dashboard API.

> **Graph semantics note**: The ahe_graph layer is an **associative co-activation graph** (Hebbian), not an entity-relation knowledge graph. Edges mean "these memories were used together", not "these entities have a typed factual relationship".

## Memory Operations

```python
# Search active memories by relevance
srh_memory_search(query="Python error handling", k=5)
srh_memory_search(query="部署流程", k=5, zone="work")  # zone filter

# Write a new memory
srh_memory_write(
    body="Always use anyhow for app-level error handling",
    scope="user",
    confidence="high",
    tags=["rust", "error-handling"],
    pinned=True,
    supersedes=[],
    zone="general",
)

# Delete a memory
srh_memory_delete(id="mem_abc123", scope="user")

# Trace version lineage through supersedes chains
srh_memory_history(id="mem_abc123", max_depth=5)
```

## Skill Operations

```python
# Search skills by token overlap
srh_skill_search(query="rust async", k=3)
```

## Reflection

```python
# Trigger reflection pipeline
srh_reflect_now()  # session-end structured summary
```

## Palace Navigation

```python
# List all zones with memory counts
srh_palace_zones()

# Read a zone's summary
srh_palace_read_zone(zone="work")

# Topic-based recall within a zone
srh_palace_recall(topic="editor preference", zone="work", limit=5)

# Cross-zone search
srh_palace_search(query="Docker", limit=10)

# Auto split/merge zones (dry_run first)
srh_palace_rebalance(dry_run=True)
srh_palace_rebalance(dry_run=False)
```

## Profile Compilation

```python
# Compile memories into a structured profile
srh_compile_profile(mode="profile")       # profile.md format
srh_compile_profile(mode="palace_index")  # palace index format
srh_compile_profile(mode="zone")          # per-zone summaries
```

## Graph Tools

```python
# Associate memories via co-activation edges
srh_associate(memory_ids=["mem_a", "mem_b"], relation="co_occurs")

# Retrieve associative neighbors (Hebbian co-activation propagation)
srh_graph_retrieve(memory_ids=["mem_a"], task_type="reasoning", tier="list")

# Get associative graph stats
srh_graph_stats()

# Get graph visualization data
srh_graph_viz(tier="summary")
```

## Health Metrics (v0.9.2-beta2)

```python
# Get memory health metrics and recommendations
srh_memory_health()
```

Returns:
- `duplicate_clusters`: count of near-duplicate memory clusters
- `longest_supersedes_chain`: length of deepest version lineage
- `supersedes_cycle_count`: cycles in supersedes chains (should be 0)
- `stale_high_rank_count`: superseded memories still ranked high
- `expired_count`: memories past their `valid_until` date
- `reflection_acceptance_rate`: ratio of accepted to total audit entries

## Registered Tools (17 total)

### Core Memory (4)
- `srh_memory_search`
- `srh_memory_write`
- `srh_memory_delete`
- `srh_memory_history`

### Palace Navigation (5)
- `srh_palace_zones`
- `srh_palace_read_zone`
- `srh_palace_recall`
- `srh_palace_search`
- `srh_palace_rebalance`

### Reflection & Profile (2)
- `srh_reflect_now`
- `srh_compile_profile`

### Skills (1)
- `srh_skill_search`

### Graph Memory (4)
- `srh_associate`
- `srh_graph_retrieve`
- `srh_graph_stats`
- `srh_graph_viz`

### Health & Governance (1)
- `srh_memory_health` (v0.9.2-beta2)
