# Test Coverage Documentation

> Version: v1.7 release baseline (incl. round-3 functional audit fixes + A5 schema stability, round-4 slash-command/graph/curator/stats fixes)
> Last updated: 2026-06-14
> Collection snapshot: `pytest tests --collect-only -q` -> **647 tests collected**
> Scope note: this document reflects the current pytest layout, marker taxonomy, and v1.7 acceptance surfaces

---

## Quick Reference

### Marker taxonomy

The suite has three complementary marker layers, all registered in `pytest.ini`
(so `pytest --markers` lists them and `pytest -m "<expr>"` works) and
auto-assigned per-file in `tests/conftest.py`:

1. **functional surface** — `store`, `search`, `retrieval`, `reranker`, `cjk`,
   `graph`, `sidecar`, `reflection`, `extraction`, `supersedes`, `scope`,
   `compaction`, `context`, `runtime`, `curator`, `events`, `config`,
   `backend`, `bridge`, `dashboard`, `tools`, `contract`, `smoke`, `e2e`,
   `integration`, `compatibility`.
2. **version acceptance** — `v16` (event ledger + scoped filters), `v17`
   (round-3 typed sidecar invalidation, semantic supersedes merge/scope_split,
   kind typing, ScopeIntent).
3. **v1.4 regression** — `v14_context`, `v14_retrieval`, `v14_runtime`,
   `v14_entity`, `v14_contract`, `v14_hooks` (plus a coarse `v14` file tag).

### Quick selection commands

```powershell
pytest tests/ -q                              # full suite (baseline: 647 passed)

# by functional surface
pytest -m "reflection"                        # all reflection pipelines
pytest -m "graph or sidecar"                  # Hebbian + typed fact sidecar
pytest -m "search and retrieval"              # retrieval channels
pytest -m "scope"                             # scope filter + ScopeIntent
pytest -m "supersedes"                        # semantic supersedes resolver

# by version acceptance
pytest -m "v17"                               # round-3 functional fixes + A5 schema stability
pytest -m "v16"                               # event ledger + scoped filters
pytest -m "v14"                               # all v1.4 regression slices

# narrow / smoke
pytest -m "contract or smoke"                 # host-facing gates
pytest -m "e2e"                               # end-to-end lifecycle
pytest -m "integration and not slow"
```

### Typical group sizes (637-test baseline)

| Marker | Tests | Marker | Tests |
|---|---:|---|---:|
| `runtime` | 157 | `search` | 141 |
| `integration` | 150 | `curator` | 117 |
| `compatibility` | 106 | `reflection` | 92 |
| `store` | 85 | `graph` | 61 |
| `v14` | 55 | `scope` | 38 |
| `config` | 36 | `bridge` | 35 |
| `tools` | 31 | `context` | 29 |
| `v17` | 24 | `dashboard` | 23 |
| `v16` | 23 | `contract` | 19 |
| `cjk` | 16 | `compaction` | 14 |
| `supersedes` | 11 | `extraction` | 7 |
| `e2e` | 6 | `sidecar` | 6 |
| `smoke` | 1 | | |

Intent note:

- behavior-facing tests should prefer public or workflow-visible effects over source inspection
- private helpers are tested directly only when they are stable package seams or the smallest authoritative contract for a functional surface
- implementation-shape checks (for example AST structure counting) are intentionally avoided unless no behavior-level proof exists

### Current Collected Test Modules

| Test File | Tests | Primary Surface |
|-----------|------:|-----------------|
| `test_async_writer.py` | 3 | async writer queue.Full fallback, worker exception |
| `test_backend.py` | 6 | backend capability abstraction |
| `test_bm25.py` | 16 | tokenisation, BM25 scoring, CJK retrieval, index build failure |
| `test_bridge.py` | 35 | built-in/plugin bridge, body refinement, capacity rules |
| `test_checkpoint.py` | 16 | runtime checkpoint persistence and recovery |
| `test_checkpoint_backup_failure.py` | 1 | corrupt backup when os.replace fails |
| `test_compaction.py` | 14 | episode compaction, fallback summarisation, token accounting, quality gate |
| `test_config.py` | 15 | typed config fallback and diagnostics |
| `test_context.py` | 29 | context assembly, bundle split, compression |
| `test_core_data.py` | 16 | frontmatter, effectiveness, lineage, atomic write |
| `test_curator_pipeline.py` | 85 | composable curator actions, pipeline aggregation, report persistence, boundary/error isolation |
| `test_dashboard.py` | 21 | dashboard API, curator/reflection endpoints, zones |
| `test_dashboard_integration.py` | 5 | dashboard graph service resolution + real-graph endpoints (v1.7 regression) |
| `test_e2e.py` | 6 | end-to-end lifecycle integration |
| `test_entity_extraction.py` | 21 | entity index lifecycle, regex patterns, weight hierarchy |
| `test_effectiveness_snapshot.py` | 8 | dual-track stats read path, compaction fold+truncate, tail merge, backward-compat (v1.7 round-4) |
| `test_fusion_rerank.py` | 17 | recency, effectiveness, Hebbian boost, fusion |
| `test_graph.py` | 17 | graph decay, distill, cross-zone, orphan cleanup |
| `test_graph_distil_failure.py` | 1 | distil write failure exception handling |
| `test_graph_operations.py` | 15 | compat graph CRUD, spread activation, PageRank |
| `test_host_contract_smoke.py` | 1 | host contract and smoke script |
| `test_hooks.py` | 19 | v0.16.0 enhanced hooks |
| `test_lb.py` | 9 | late-binding helper, module resolution, fail-open |
| `test_memory_curator.py` | 9 | legacy curator compatibility, cold-store lifecycle, restore |
| `test_memory_events.py` | 23 | v1.6: event ledger ADD/UPDATE/DELETE/PIN/UNPIN/SUPERSEDE, query filters, atomicity, history |
| `test_optional_deps.py` | 5 | optional dependency fallback paths |
| `test_palace_recall.py` | 4 | palace recall zone filters, scoped fallback behavior, and schema-stable `graph_expanded` key |
| `test_reflect.py` | 23 | reflection engine, facts, logs, raw chunk |
| `test_reflection.py` | 27 | runtime reflection, hook cadence, JSON repair |
| `test_reflection_refinement.py` | 7 | refined candidate extraction, kind vocabulary, LLM kind propagation |
| `test_reflection_scope.py` | 4 | scope propagation across reflection, compaction, and manual reflection |
| `test_reranker.py` | 13 | reranker abstraction and SearchIndex integration |
| `test_reranker_exceptions.py` | 5 | reranker OOM/API failure fallbacks |
| `test_runtime_graph_aliases.py` | 7 | runtime graph alias helpers and host-facing graph surfaces |
| `test_runtime_import_hygiene.py` | 4 | runtime recovery, compile-profile, tool handler dispatch |
| `test_schema_module.py` | 18 | runtime schema definitions, JSON schema validity |
| `test_scope_filters.py` | 34 | scoped write/search/list/update/delete/explain, migration, NULL matching, ScopeIntent / GLOBAL_ONLY |
| `test_search.py` | 16 | RRF, MMR, conflict, explain, entity boost |
| `test_semantic_supersedes.py` | 11 | semantic correction/merge/store/scope-split resolution + side-effect edges |
| `test_store.py` | 21 | rebuild/validate/prune, lineage, delete callbacks |
| `test_store_module_split.py` | 11 | core module split, behavioral compatibility, backward compat |
| `test_tool_handlers.py` | 9 | runtime tool lineage and write/read helpers |
| `test_typed_fact_sidecar.py` | 6 | typed fact sidecar persistence, batch invalidation, entity mentions, compaction end-to-end |
| `test_wave3_retrieval.py` | 15 | spread activation, CJK, fusion, time sorting |

---

## Functional Grouping

### Store & data layer
`test_store.py`, `test_core_data.py`, `test_store_module_split.py`, `test_async_writer.py`

### Retrieval, ranking & entities
`test_search.py`, `test_bm25.py`, `test_fusion_rerank.py`, `test_wave3_retrieval.py`, `test_reranker.py`, `test_reranker_exceptions.py`, `test_entity_extraction.py`, `test_palace_recall.py`

### Graph layer (incl. typed fact sidecar)
`test_graph.py`, `test_graph_operations.py`, `test_graph_distil_failure.py`, `test_runtime_graph_aliases.py`, `test_typed_fact_sidecar.py`

### Scope & filters
`test_scope_filters.py`

### Reflection, extraction & supersedes
`test_reflect.py`, `test_reflection.py`, `test_reflection_refinement.py`, `test_reflection_scope.py`, `test_semantic_supersedes.py`, `test_compaction.py`

### Context, runtime & config
`test_context.py`, `test_hooks.py`, `test_runtime_import_hygiene.py`, `test_checkpoint.py`, `test_checkpoint_backup_failure.py`, `test_config.py`, `test_optional_deps.py`, `test_backend.py`, `test_schema_module.py`, `test_lb.py`

### Curation & lifecycle
`test_memory_curator.py`, `test_curator_pipeline.py`, `test_memory_events.py`

### Integration & host surfaces
`test_bridge.py`, `test_dashboard.py`, `test_dashboard_integration.py`, `test_tool_handlers.py`, `test_e2e.py`, `test_host_contract_smoke.py`

---

## v1.7 Round-3 Acceptance Surface (`v17`)

The round-3 functional audit (see `docs/dev/1.7/v1.7-round3-functional-audit.md`)
landed five fixes plus one schema-stability fix, each with dedicated regression coverage tagged `v17`:

| Fix | Marker files | New tests |
|-----|--------------|----------|
| **P1-1** typed sidecar invalidation chain | `test_typed_fact_sidecar.py` | `test_invalidate_facts_for_memories_bulks_invalidates_owned_facts`, `test_compaction_invalidates_superseded_episode_facts` |
| **P1-2** merge / scope_split handling | `test_semantic_supersedes.py` | `test_record_semantic_relation_sidecar_writes_merge_edge`, `…_scope_split_edge`, `…_noop_for_store_and_supersede`, `test_merge_action_invalidates_merge_target_facts` |
| **P2-1** kind vocabulary + LLM propagation | `test_reflection_refinement.py` | `test_normalize_memory_kind_canonicalizes_known_and_unknown`, `test_reflect_schema_includes_kind_enum`, `test_llm_full_reflection_propagates_kind_to_sidecar`, `test_llm_full_reflection_falls_back_unknown_kind_to_fact` |
| **P2-2** engine.py dead-import + CJK helper consolidation | (covered by `test_compaction.py` / `test_reflect.py`) | — |
| **P2-3** ScopeIntent / GLOBAL_ONLY | `test_scope_filters.py::TestScopeIntent` | 10 cases incl. `test_list_with_global_only_returns_only_null_scope_rows` |
| **A5** palace_recall schema stability | `test_palace_recall.py` | `test_scoped_recall_emits_stable_graph_expanded_key` |

```powershell
pytest -m "v17"          # 24 tests across the five round-3 fixes + A5 schema stability
```

## v1.4 & v1.5 Acceptance Surface

The v1.4 work is covered by seven dedicated marker groups:

| Marker | Focus | Main Files |
|--------|-------|------------|
| `v14_context` | stable/dynamic context split, timeout fallback, graded compression | `test_context.py`, `test_reflection.py` |
| `v14_retrieval` | CJK tokenizer upgrades and explainable retrieval | `test_bm25.py`, `test_search.py` |
| `v14_runtime` | checkpoint persistence, recovery, typed config diagnostics | `test_checkpoint.py`, `test_reflection.py`, `test_config.py` |
| `v14_entity` | entity index lifecycle, entity boost, backend readiness | `test_search.py`, `test_backend.py`, `test_entity_extraction.py` |
| `v14_contract` | host-facing compatibility gate | `test_host_contract_smoke.py` |
| `v14_hooks` | v0.16.0 enhanced runtime hooks | `test_hooks.py` |
| `v14` (coarse) | any file-level v1.4-era tag | scattered |

```powershell
pytest tests/ -m "v14" -q          # all v1.4 regression coverage
pytest tests/ -m "v14_runtime or v14_entity" -q
```

---

## Functional Coverage Summary

### Store and data contracts
- `test_store.py`: index rebuild, validation, prune, supersedes boundaries, delete-callback fail-open.
- `test_core_data.py`: frontmatter round-trip, effectiveness decay, lineage helpers, atomic-write safety.
- `test_store_module_split.py`: core module split, behavioral + backward compatibility.
- `test_async_writer.py`: async writer `queue.Full` fallback and worker exception handling.

### Retrieval and ranking
- `test_search.py`: RRF fusion, MMR rerank, conflict detection, cache-key boundaries, graph wiring, explain payloads, entity boost.
- `test_bm25.py`: English / mixed-language / CJK tokenization, BM25 scoring, index build failure degradation.
- `test_fusion_rerank.py`: recency, effectiveness, Hebbian boost, supersedes penalties, channel normalization.
- `test_reranker.py` / `test_reranker_exceptions.py`: lazy reranker provider loading + OOM/API fallbacks.
- `test_palace_recall.py`: palace recall zone filters and scoped fallback.
- `test_wave3_retrieval.py`: graph-assisted retrieval, CJK stopwords, minimal fusion flows.
- `test_entity_extraction.py`: 6 regex patterns, weight hierarchy, dedup, normalization.

### Scope and filters
- `test_scope_filters.py`: scoped write/search/list/update/delete/explain, schema migration, NULL matching, and the round-3 `ScopeIntent` / `GLOBAL_ONLY` model (`TestScopeIntent`).

### Reflection, extraction, and supersedes
- `test_reflect.py`: reflection-engine heuristics, fact extraction, raw-chunk mode, reflect-log behavior.
- `test_reflection.py`: runtime hook cadence, truncated-JSON repair, supersedes regression, checkpoint recovery, timeout fallback.
- `test_reflection_refinement.py`: refined candidate extraction, the `REFINED_MEMORY_KINDS` vocabulary + `normalize_memory_kind`, and LLM-mode kind propagation into the sidecar.
- `test_reflection_scope.py`: scope propagation across reflection, compaction, manual reflection entrypoints.
- `test_semantic_supersedes.py`: semantic correction/merge/store/scope-split resolution, the merge/scope_split side-effect edges, and cross-scope supersedes rejection.
- `test_compaction.py`: episode clustering, scored fallback summarisation, token accounting, quality-gated LLM fallback, idempotency, return-shape guarantees.

### Graph and sidecar surfaces
- `test_graph.py`: graph decay, distillation, cross-zone analysis, orphan-edge cleanup.
- `test_graph_operations.py`: compatibility graph CRUD, spread activation, PageRank, thread-safe reads.
- `test_graph_distil_failure.py`: distil write failure exception handling.
- `test_runtime_graph_aliases.py`: runtime graph alias helpers and host-facing graph surfaces.
- `test_typed_fact_sidecar.py`: typed fact sidecar persistence, **batch invalidation** (`invalidate_facts_for_memories`), entity mentions, and the compaction end-to-end invalidation path.

### Context, runtime, and config
- `test_context.py`: classic string assembly, `ContextBundle` stable/dynamic split, token-budget pressure, compression-level diagnostics.
- `test_hooks.py`: v0.16.0 enhanced hooks (`api_error`, `subagent_start/stop`, `session_reset`).
- `test_runtime_import_hygiene.py`: runtime recovery, compile-profile late-binding, tool handler dispatch.
- `test_checkpoint.py` / `test_checkpoint_backup_failure.py`: atomic write, corrupt-checkpoint backup, pending-work recovery, backup failure edge.
- `test_config.py` / `test_optional_deps.py`: typed config model, diagnostics, feature flags, optional-dependency fallbacks.
- `test_backend.py`: backend capability abstraction.
- `test_schema_module.py`: runtime schema/registration contracts.
- `test_lb.py`: late-binding and standalone loading safety.

### Curation and lifecycle
- `test_curator_pipeline.py`: canonical v1.5 curator action pipeline, orchestration, side effects, boundary handling.
- `test_memory_curator.py`: legacy-compatible curator behavior, cold-store lifecycle, restore, no-graph cleanup.
- `test_memory_events.py`: v1.6 event ledger ADD/UPDATE/DELETE/PIN/UNPIN/SUPERSEDE, query filters, atomicity, history.

### Integration and host surfaces
- `test_bridge.py`: host bridge mirroring in both directions, duplicate handling, capacity checks, tool-output cleanup.
- `test_dashboard.py`: dashboard CRUD, graph views, stats, curator/reflection endpoints, zones.
- `test_dashboard_integration.py`: dashboard graph service resolution through `runtime.graph` and real-graph endpoints (v1.7 regression for the dropped `runtime_graph` import).
- `test_tool_handlers.py`: lineage-cycle prevention and host tool helper compatibility.
- `test_e2e.py`: full store-search-graph-reflection-context chain.
- `test_host_contract_smoke.py`: host-facing smoke script as an acceptance gate.

---

## Maintenance Notes

- **`pytest.ini`** (project root) is the source of truth for **marker registration** — it lists every functional / version / v1.4 marker so `pytest --markers` and `pytest -m "<expr>"` resolve cleanly.
- **`tests/conftest.py`** is the source of truth for **automatic marker assignment** (`_FILE_MARKERS` per-file, plus `_V14_NODE_MARKERS` for node-level v1.4 overrides). When you add a test file, add it to `_FILE_MARKERS` so it joins a functional group.
- **`tests/_helpers.py`** provides shared mock classes and test utilities (`make_memory`, `make_memory_with_id`, `MockStore`, …).
- Latest coverage refresh: `pytest tests --collect-only -q` reports **647 collected tests**.
- When adding new tests: classify them by functional surface first (add the file to `_FILE_MARKERS`); use a `v16` / `v17` marker only when the test is part of a versioned acceptance slice.
- Keep this document aligned to collected reality. The minimum refresh is:
  1. rerun `pytest tests --collect-only -q`
  2. update the module counts table
  3. update the version-specific acceptance notes and the group-sizes table

> Note on `PytestUnknownMarkWarning`: markers are auto-assigned via
> `item.add_marker(getattr(pytest.mark, name))` in `conftest.py`. That dynamic
> assignment path can still emit a benign `PytestUnknownMarkWarning` on some
> pytest versions even for ini-registered marks; it is informational and must
> not be promoted to an error (it would break CI). The marks themselves work
> correctly for selection and reporting.
