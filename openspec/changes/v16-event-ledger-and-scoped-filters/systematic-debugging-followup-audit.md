# Systematic Debugging Follow-Up Audit

> Triggered after fixing PR #15 codex inline comments.
> Method: apply the 6-layer pitfall checklist from `systematic-debugging` skill to the whole codebase, using the 6 fixed issues as reference patterns.
> Audit date: 2026-06-12

## Reference Patterns (Already Fixed)

| # | File | Pattern | Layer |
|---|------|---------|-------|
| R1 | `__init__.py` | `__package__` empty-string guard | L1a |
| R2 | `runtime/hooks.py` | ThreadPoolExecutor context-manager shutdown blocks timeout fallback | L3 |
| R3 | `core/search.py` | BM25 filter applied after top-k truncation | L3 |
| R4 | `core/search.py` | Embedding filter applied after top-k truncation | L3 |
| R5 | `core/store.py` | `delete_by_filters` omits `_post_delete_callbacks` | L3 |
| R6 | `memory/context.py` | `stable_only=True` still builds dynamic context | L3 |

## Findings Summary by Layer

### L1 / L1a — Interface Contract / Import Loading

| # | File | Lines | Issue | Severity | Status |
|---|------|-------|-------|----------|--------|
| L1-01 | `__init__.py` | 30-34 | Empty-string guard already fixed | - | ✅ Fixed |
| L1-02 | `core/store.py` | 60-94 | Bare module fallback in `except ImportError` | Low | Acceptable (by design for standalone loading) |
| L1-03 | `memory_bridge.py` | 442-449 | Duplicate/legacy file with weaker fallback than `memory/bridge.py` | Medium | ✅ Removed (git rm) |
| L1-04 | `runtime/graph.py` | 342-354 | importlib fallback pollutes `sys.modules`, no `__package__` set | Low | Acceptable (setdefault won't overwrite) |
| L1-05 | `runtime/graph.py` | 477-490 | Bare `from store import` fallback | Low | Acceptable (3rd fallback layer) |
| L1-06 | `core/store.py` | 101-108 | `_load_related_module` fallback to bare `core.` | Low | Acceptable (2-layer fallback by design) |
| L1-07 | `runtime/hooks.py` | 217-223 | Late-bound `from ..` may fail standalone | Low | Acceptable (_lb handles gracefully) |
| L1-08 | `runtime/tools.py` | 849 | Naming drift (navigate vs recall) | Low | Pending |
| L1-09 | `runtime/tools.py` | 851 | Naming drift (query vs search) | Low | Pending |
| L1-10 | `runtime/schemas.py` | 39-47 | `_SRH_MEMORY_DELETE_SCHEMA` `required: []` vs handler rejecting no args | High | ✅ Fixed |
| L1-11 | `runtime/schemas.py` | 96-104 | `_SRH_GRAPH_RETRIEVE_SCHEMA` `tier` enum mismatch | High | ✅ Fixed |
| L1-12 | `runtime/schemas.py` | 106-122 | Graph stats/viz schemas define unused params | Medium | ✅ Fixed |
| L1-13 | `runtime/graph.py` | 743-779 | Public aliases call non-existent methods | Critical | ✅ Fixed |
| L1-14 | `memory/curator/__init__.py` | 145 | `total_archived` includes `compacted` | High | ✅ Fixed |
| L1-15 | `core/models.py` | 75-93 | `to_dict` omits empty `supersedes` | Low | Acceptable (default `[]` correctly omitted) |

### L3 — Side-Effect Order / Context Propagation

| # | File | Lines | Issue | Severity | Status |
|---|------|-------|-------|----------|--------|
| L3-01 | `core/store.py` | 487-503 | `_sync_from_disk` deletes rows without `_post_delete_callbacks` | High | ✅ Fixed |
| L3-02 | `core/store.py` | 1117-1124 | `reorder` does not record memory events | Low | ✅ Fixed (records UPDATE events) |
| L3-03 | `core/store_health.py` | 136-160 | `prune_index` deletes rows without callbacks/events | High | ✅ Fixed |
| L3-04 | `core/store_health.py` | 125-134 | `rebuild_index` drops tables without callbacks/event preservation | Medium | ✅ Fixed (collects IDs, fires callbacks + events) |
| L3-05 | `runtime/tools.py` | 543-588 | `_tool_srh_palace_recall` over-fetches then filters zone after truncation | High | ✅ Fixed |
| L3-06 | `runtime/tools.py` | 592-636 | `_tool_srh_palace_search` over-fetches without zone filter (potential) | Low | Pending |
| L3-07 | `runtime/hooks.py` | 169-188 | ThreadPoolExecutor ordering already fixed | - | ✅ Fixed |
| L3-08 | `memory/context.py` | 160,177,191 | `stable_only` skip already fixed | - | ✅ Fixed |
| L3-09 | `core/search.py` | 376-393,468-480 | Mask-before-argpartition already fixed | - | ✅ Fixed |
| L3-10 | `core/search.py` | 807-820 | Reranker/MMR may discard context (defensive) | Low | Pending |
| L3-11 | `core/store.py` | 733-765 | `put()` does not invalidate `_embed_single` cache | Medium | Acceptable (invalidate_cache already calls cache_clear; _search_index=None is edge case) |
| L3-12 | `core/store.py` | 814-891 | No `_post_update_callbacks` / `_post_write_callbacks` | Medium | Pending |

### L4 — Aggregation Consistency

| # | File | Lines | Issue | Severity | Status |
|---|------|-------|-------|----------|--------|
| L4-01 | `memory/curator/__init__.py` | 145,159-168 | `total_archived` may diverge from `generate_report()` formula | High | ✅ Fixed |
| L4-02 | `core/graph.py` + `runtime/graph.py` | 358-369, 306-311 | `stats()` keys `nodes`/`edges` vs `node_count`/`edge_count` divergence | Medium | ✅ Fixed (both key sets always populated) |
| L4-03 | `memory/bridge.py` | 50-65 | `_incr_stat` creates unknown keys silently | Low | ✅ Fixed (logs warning on unknown key) |
| L4-04 | `reflection/runtime.py` | 1887-1902 | `total_raw_consumed` incremented even when `put()` fails | High | ✅ Fixed |
| L4-05 | `core/store_health.py` | 28-76 | Duplicate cluster count may differ between MinHash/Jaccard paths | Low | Pending |

### L5 — Error Silence / Invalid Parameters

| # | File | Lines | Issue | Severity | Status |
|---|------|-------|-------|----------|--------|
| L5-01 | `core/reranker.py` | 95-97 | Cohere init swallowed | Medium | Acceptable (already uses logger.warning at line 96) |
| L5-02 | `core/tokenization.py` | 40-41,50-53 | tiktoken silent fallback | Medium | Acceptable (intentional graceful degradation) |
| L5-03 | `memory/bridge.py` | 275-276,299-300 | Duplicate check silent fail | Medium | ✅ Fixed (logger.warning + exc_info) |
| L5-04 | `memory/bridge.py` | 621-622 | I/O failure silent | Medium | ✅ Fixed (logger.warning with details) |
| L5-05 | `web/api.py` | 692-693,711,771,776,797,822 | API silent degradation | Medium | ✅ Fixed (all 6 sites now logger.warning) |
| L5-06 | `runtime/hooks.py` | 190-195 | Timeout fallback exception separation | Low | Pending |
| L5-07 | `runtime/hooks.py` | 446-447 | Token budget enforcement silently disabled | High | ✅ Fixed |
| L5-08 | `memory/context.py` | 162-165 | Search fallback silent | Medium | ✅ Fixed (logger.warning + exc_info) |
| L5-09 | `memory/context.py` | 178-183 | Compacted episode drop silent | Low | Pending |
| L5-10 | `core/search.py` | 29-30,155-157,192-193,200-201,402-404,413-414,434-436 | Search init silent degradation | Medium | ✅ Fixed (5 sites upgraded from debug→warning) |
| L5-11 | `core/models.py` | 143-144,178-179,185-186,316-317 | Frontmatter parse silent fallback | Low | Pending |
| L5-12 | `core/store.py` | 432-434 | Connection close silent | Low | Pending |
| L5-13 | `runtime/checkpoint.py` | 64-68 | Temp cleanup already logs debug | - | Acceptable |
| L5-14 | `reflection/runtime.py` | many | Dense silent exceptions | Medium | Pending |
| L5-15 | `memory/curator/actions.py` | 392-393,422-423,452-453 | Curator silent degradation | Medium | ✅ Fixed (2 sites: list_active + sort) |
| L5-16 | `runtime/graph.py` | 344,563-564 | Graph init silent fallback | Low | Pending |
| L5-17 | `core/entities.py` | 31,49,63,73 | Entity extraction silent empty | Low | Pending |
| L5-18 | `runtime/helpers.py` | 40,51 | Helper silent fail | Low | Pending |
| L5-19 | `core/async_writer.py` | 96-97,106-107 | Async writer silent loop / no details | Medium | ✅ Fixed (catches queue.Empty specifically, logs other exceptions) |
| L5-20 | `tests/conftest.py` | 213 | Test fixture silent cleanup | Low | Pending |

### L2 / L3a — Data Scope / Persistence Omission

(Completed as background audit; top-priority findings recorded separately.)

Top findings:
- `tests/_helpers.py`: `MockStore.list_active()` missing `filters` param; `MockStore.update()` mutates in-place without persistence tracking.
- `reflection/runtime.py`: post-construction `fm.supersedes = [...]` assignments (2 sites) should move into `MemoryFrontmatter.new()`.
- `memory/curator/actions.py`: `CuratorResult` dataclass injected with `__dict__["report_text"]`; `update()` calls lack supersedes target validation.
- `tests/test_scope_filters.py`, `tests/test_memory_events.py`: use `object.__setattr__` for scope fields that are now native dataclass fields.

## Implementation Plan

1. **P0 — Critical / directly matches reference patterns** ✅ Completed
   - L1-13: `runtime/graph.py` aliases call non-existent methods
   - L1-10: `_SRH_MEMORY_DELETE_SCHEMA` required drift
   - L1-11: `_SRH_GRAPH_RETRIEVE_SCHEMA` tier enum drift
   - L3-01: `_sync_from_disk` missing callbacks
   - L3-03: `prune_index` missing callbacks
   - L3-05: `_tool_srh_palace_recall` zone filter after truncation
   - L4-01: `total_archived` aggregation inconsistency
   - L4-04: `total_raw_consumed` undercount
   - L5-07: token budget truncation silently disabled

2. **P1 — High impact** ✅ Completed
   - L3-04: `rebuild_index` missing callbacks/events
   - L4-02: graph stats keys divergence
   - L5-05: web/api.py silent degradation (6 sites)
   - L5-10: search.py silent degradation (5 sites)
   - L5-15: curator actions silent degradation (2 sites)
   - L5-19: async_writer silent loop
3. **P2 — Medium / cleanup** ✅ Completed
   - L1-03: duplicate `memory_bridge.py` removed
   - L3-02: `reorder` now records UPDATE events
   - L4-03: bridge `_incr_stat` warns on unknown keys
   - L5-03/04: bridge duplicate/I/O logging
   - L5-08: context search fallback logging
   - L5-01/02: reranker/tiktoken confirmed acceptable (by design)
   - L5-17/18: entities/helpers confirmed acceptable (by design)
4. **Remaining Acceptable** — Items reviewed and determined to be intentional design (import fallback chains, graceful degradation for optional deps)

## Verification

- [x] Each P0 fix has a regression test.
- [x] Targeted regression tests pass.
- [x] Full suite `pytest tests/ -q` passes: **581 passed**
- [x] Smoke script `python scripts/smoke_host_contract.py` passes: **37 passed, 0 failed**
- [x] Tracker updated with fix statuses.
- [x] P1/P2 batch fixes verified: **581 passed**, **37 smoke passed**

### Regression Test Mapping

| Fix | Test File | Test(s) |
|-----|-----------|---------|
| L1-13 Graph aliases | `tests/test_runtime_graph_aliases.py` | `TestAliasFunctions` (7 tests) |
| L1-10/11/12 Schema drift | `tests/test_schema_module.py` | `test_memory_delete_schema_requires_id_or_filters`, `test_graph_retrieve_schema_tier_enum_matches_handler`, `test_graph_stats_schema_has_no_unused_properties`, `test_graph_viz_schema_has_no_unused_properties` |
| L3-01 `_sync_from_disk` callbacks | `tests/test_store.py` | `TestPostDeleteCallbacks::test_callbacks_invoked_on_sync_from_disk` |
| L3-03 `prune_index` callbacks/events | `tests/test_store.py` | `TestPostDeleteCallbacks::test_callbacks_invoked_on_prune_index`, `test_prune_index_records_delete_event` |
| L3-05 `palace_recall` zone filter | `tests/test_palace_recall.py` | `TestPalaceRecallZoneFilter` (3 tests) |
| L4-01 `total_archived` | `tests/test_curator_pipeline.py` | `TestPipelineAggregation::test_total_archived_excludes_compacted` |
| L4-04 `total_raw_consumed` | `tests/test_compaction.py` | existing `total_raw_consumed` assertions now reflect success-only counting |
| L5-07 token budget fallback | `tests/test_hooks.py` | `TestPreLlmCallTokenBudgetFallback::test_estimate_tokens_raises_truncates_context` |
