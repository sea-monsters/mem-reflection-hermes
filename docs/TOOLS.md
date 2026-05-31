# Tools Reference

## Memory Operations

```python
# Search memories (TF-IDF/BM25 + optional semantic)
srh_memory_search(query="Python error handling", k=5)
srh_memory_search(query="部署流程", k=5, zone="work")  # zone filter

# Write a new memory
srh_memory_write(
    body="Always use anyhow for app-level error handling",
    tags=["rust", "error-handling"],
    confidence="high",
    pinned=true
)

# Delete a memory
srh_memory_delete(memory_id="mem_abc123")

# Trace version lineage through supersedes chains
srh_memory_history(memory_id="mem_abc123")
```

## Skill Operations

```python
# Search skills by token overlap + optional embedding
srh_skill_search(query="rust async", k=3)
```

## Reflection

```python
# Trigger reflection pipeline
srh_reflect_now(mode="full")   # session-end structured summary
srh_reflect_now(mode="micro")  # lightweight per-turn reflection
```

## Palace Navigation

```python
# List all zones with memory counts
srh_palace_zones()

# Read a zone's summary
srh_palace_read_zone(zone="work")

# Topic-based recall within a zone
srh_palace_recall(topic="editor preference", zone="work")

# Cross-zone search
srh_palace_search(query="Docker")

# Auto split/merge zones (dry_run first)
srh_palace_rebalance(dry_run=true)
srh_palace_rebalance(dry_run=false)
```

## Profile Compilation

```python
# Compile all memories into structured profile
srh_compile_profile(mode="profile")       # profile.md format
srh_compile_profile(mode="palace_index")  # palace index format
srh_compile_profile(mode="zone")          # per-zone summaries
```

## Graph Memory (ahe_graph)

```python
# Create an association between two memories
srh_associate(
    source_id="mem_abc",
    target_id="mem_def",
    relation="supersedes"
)

# Retrieve connected memories via graph traversal
srh_graph_retrieve(seed_id="mem_abc", depth=2)

# Graph statistics
srh_graph_stats()

# Graph visualization (returns DOT or Mermaid)
srh_graph_viz()
```

## Slash Commands

```
/memories              # List all active memories
/skills                # List all active skills
/pending-skills        # Show skills awaiting approval
/approve-skill <id>    # Approve a pending skill
/reject-skill <id>     # Reject a pending skill
/compile-profile       # Compile memories into profile via LLM
```
