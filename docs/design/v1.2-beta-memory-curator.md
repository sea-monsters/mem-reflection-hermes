# v1.2-beta: Memory Curator

**Status**: Design Proposal — Pre-Implementation
**Target**: Hermes Agent v0.16.0+ / mem-reflection-hermes v1.1+
**Goal**: Automated memory curation — TTL expiry, staleness detection, similarity merge, cold storage
**Research**: deepxiv literature survey (RecMem, SCM, AMV-L, Agentlas, ECS) — see [References](#references)

## 1. Motivation

The current memory system is **write-only with no automatic pruning**:

- Memories accumulate indefinitely across zones (core, work, episode, general)
- No automated way to detect stale or redundant entries
- No lifecycle management — old facts persist even when superseded
- Episode zone can grow unbounded, diluting signal in context injection
- Users must manually use `srh_memory_delete` + `srh_palace_rebalance` for cleanup

## 2. Design Principles

| Principle | Rationale |
|-----------|-----------|
| **Fail open** | Curator failures should never block session lifecycle |
| **Pure rules first, LLM optional** | TTL/heuristic rules handle 80% of cases; LLM pass for semantic merge only when configured |
| **Respect supersedes chain** | Archived superseded memories are prime candidates for cold storage |
| **Session-end trigger** | Curator runs after reflection and compaction in `on_session_end` |
| **No new storage layer** | Reuses existing `MemoryStore`, supersedes metadata, and zone structure |
| **Observability** | Curator actions logged to reflect-log.jsonl for audit |

## 3. Architecture

### Trigger Point

```
on_session_end → reflection → episode compaction → CURSOR RUNS HERE → cleanup
```

The curator is a new module `memory_curator.py` called from `runtime_hooks._on_session_end()` after reflection + compaction complete. It runs in a `try/except` block (fail-open).

### Module Structure

```
memory_curator.py  (~350 lines)
  ├── scan_for_stale()      → List[str] (memory_ids)
  ├── scan_for_similar()    → List[Tuple[str, str, float]] (id_a, id_b, score)
  ├── archive_superseded()  → int (count)
  ├── cold_storage()        → int (count)
  └── generate_report()     → str (summary for reflection log)
```

### Data Flow

```
[on_session_end]
       │
       ▼
┌─────────────────────────────┐
│ 1. archive_superseded()     │ ← Move fully-superseded chains to cold
│    (no active references)   │
└─────────────────────────────┘
       │
       ▼
┌─────────────────────────────┐
│ 2. scan_for_stale()         │ ← TTL + access-pattern based
│    (optional: archive/flag) │
└─────────────────────────────┘
       │
       ▼
┌─────────────────────────────┐
│ 3. scan_for_similar()       │ ← BM25 overlap + embedding cosine
│    (optional: merge or flag)│     (only if embeddings enabled)
└─────────────────────────────┘
       │
       ▼
┌─────────────────────────────┐
│ 4. cold_storage()           │ ← Move dormant to JSONL archive
│    (>30 days, no accesses)  │
└─────────────────────────────┘
       │
       ▼
┌─────────────────────────────┐
│ 5. generate_report()        │ ← Text summary for session log
└─────────────────────────────┘
```

## 4. Curation Rules

### 4.1 TTL & Staleness (`scan_for_stale`)

| Rule | Default | Config |
|------|---------|--------|
| Expired (`valid_until` past) | Auto-delete | `curator.ttl.expired_action: "delete"` |
| Unaccessed > 90 days | Flag → manual review | `curator.stale.days: 90` |
| Low effectiveness (< 0.1, no accesses) | Archive to cold | `curator.stale.effectiveness_threshold: 0.1` |
| Episode entries > 30 days | Archive summarised | `curator.episode.ttl_days: 30` |

Exempt from staleness: pinned memories, explicitly tagged `keep` or `permanent`.

### 4.2 Similarity Detection (`scan_for_similar`)

Two-phase approach:

1. **BM25 pre-filter**: All memories scored against each other; pairs above threshold (default 0.6) proceed.
2. **Embedding cosine** (if enabled): Re-rank BM25 candidates for semantic similarity.

Merge candidates flagged when:
- BM25 score ≥ 0.7 **or** cosine ≥ 0.85
- Same zone or compatible zones (e.g. `work` + `general`)

**Action**: Flag for LLM merge (optional) or suggest manual merge via dashboard.

Config: `curator.similarity.bm25_threshold: 0.6`, `curator.similarity.embedding_threshold: 0.85`.

### 4.3 Supersedes Archiving (`archive_superseded`)

Memories where `supersedes` chain depth ≥ 2 and no active user references (not pinned, not accessed in last 7 days) are moved to cold storage as a single compressed entry preserving the chain lineage.

### 4.4 Cold Storage (`cold_storage`)

Cold storage is a **JSONL file** at `<plugin_data_dir>/cold_store.jsonl`. Each entry:

```json
{
  "id": "orig-id",
  "body": "original text",
  "zone": "episode",
  "archived_at": "2026-06-06T12:00:00Z",
  "supersedes_chain": ["id1", "id2", "id3"],
  "tags": ["archived", "cold"],
  "original_frontmatter": { ... }
}
```

Cold memories are excluded from search and context injection. They can be:
- Rehydrated via `srh_memory_history` (which already walks chains)
- Bulk-restored via dashboard

## 5. Configuration

In plugin config (part of `plugin_config()` block):

```yaml
curator:
  enabled: true
  trigger: "session_end"        # when to run: "session_end" | "manual" | "cron"
  ttl:
    expired_action: "delete"    # "delete" | "flag" | "archive"
  stale:
    days: 90
    effectiveness_threshold: 0.1
  episode:
    ttl_days: 30
  similarity:
    enabled: true
    bm25_threshold: 0.6
    embedding_threshold: 0.85
    llm_merge: false            # requires ctx (LLM access)
  cold_storage:
    enabled: true
    max_archive_size_mb: 10
```

## 6. Integration Points

| Integration | File | Change |
|-------------|------|--------|
| Hook trigger | `runtime_hooks.py:_on_session_end` | Add `_run_curator(ctx, mem_store)` call after compaction |
| Config read | `store.py:plugin_config()` | Already reads full config dict |
| Config helpers | `__init__.py` | Add `_curator_enabled()` / `_curator_config()` |
| Reflection log | `reflect.py` | Curator report appended to session summary |
| Dashboard | `dashboard/plugin_api.py` | Optional: GET /curator/report, POST /curator/run |
| CLI | `runtime_tools.py` | Optional: `/curator` slash command |
| Supersedes chain | `store.py` | Reuse `_lineage_latest`, `_lineage_depth` |

Zero changes needed to `MemoryStore` core — all curation uses existing public API (`put`, `delete`, `search`, `list_active`, `get_meta`).

## 7. Implementation Plan

### Phase 0 — Scaffold (~40 LOC)
- [ ] Create `memory_curator.py` with module header + config helpers
- [ ] Add `_run_curator(ctx, mem_store)` placeholder in `runtime_hooks.py`
- [ ] Wire try/except call from `_on_session_end`

### Phase 1 — TTL + Staleness (~80 LOC)
- [ ] Implement `scan_for_stale()` — check `valid_until`, access timestamps, effectiveness
- [ ] Implement `archive_expired()` — move expired entries to cold storage
- [ ] Config: `curator.ttl.*`, `curator.stale.*`

### Phase 2 — Supersedes Archiving (~60 LOC)
- [ ] Implement `archive_superseded()` — deep chains (≥2), no recent access → cold
- [ ] Preserve chain lineage in cold storage entry

### Phase 3 — Similarity Detection (~80 LOC)
- [ ] Implement `scan_for_similar()` — BM25 pair scoring
- [ ] Optional: embedding re-rank when embeddings enabled
- [ ] Flag candidates (no auto-merge without LLM)

### Phase 4 — Cold Storage Engine (~60 LOC)
- [ ] JSONL read/write with file lock
- [ ] `cold_storage_max_size` hard cap
- [ ] List/restore API for dashboard

### Phase 5 — Dashboard Integration (~60 LOC)
- [ ] GET /curator/stats endpoint
- [ ] POST /curator/run manual trigger
- [ ] Optional: curator report in dashboard admin panel

### Total Estimate: ~380 LOC, 0 new dependencies

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| False-positive deletions | All delete actions are reversible (cold storage); default is "flag" not "delete" |
| Performance on large stores | BM25 pair scoring is O(n²) — clamp to 500 most-recent memories per scan |
| LLM merge latency | LLM merge is opt-in; default pipeline is pure heuristic |
| Supersedes chain corruption | Read-only traversal; archiving creates a snapshot, never mutates chain |
| Cold storage unbounded growth | Configurable `max_archive_size_mb`; oldest entries auto-purged on overflow |

## 9. References

| Paper | Venue | Relevance |
|-------|-------|-----------|
| **RecMem** (2605.16045) | arXiv May 2025 | TTL-based memory eviction with importance scoring |
| **SCM** (2604.20943) | arXiv Apr 2025 | Structured context management — episode lifecycle |
| **AMV-L** (2603.04443) | arXiv Mar 2025 | Adaptive memory value — staleness decay curve |
| **Agentlas** | — | Self-organising memory lifecycle with auto-summarisation |
| **ECS** (2604.15877) | arXiv Apr 2025 | Episode-centric cold storage with compression |

All references reviewed via deepxiv-sdk v0.3.1 during v1.2-alpha research phase.
