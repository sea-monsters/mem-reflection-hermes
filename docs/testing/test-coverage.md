# Test Coverage Documentation

> Version: v1.7 development baseline
> Last updated: 2026-06-13
> Collection snapshot: `pytest tests --collect-only -q` -> **615 tests collected**
> Scope note: this document reflects the current pytest layout, marker taxonomy, and v1.7 acceptance surfaces

---

## Quick Reference

### Pytest Marker Reference

The suite now has two complementary marker layers:

- cross-version functional markers:
  `store`, `search`, `retrieval`, `graph`, `reflection`, `runtime`, `context`, `curator`, `bridge`, `dashboard`, `tools`, `backend`, `config`, `contract`, `compaction`, `reranker`, `e2e`, `integration`, `smoke`, `compatibility`, `cjk`
- v1.4 regression markers:
  `v14_context`, `v14_retrieval`, `v14_runtime`, `v14_entity`, `v14_contract`, `v14_hooks`

Example commands:

```powershell
pytest -m "search and retrieval"
pytest -m "runtime and not e2e"
pytest -m "v14_runtime or v14_entity"
pytest -m "contract or smoke"
```

Marker assignment is auto-managed in `tests/conftest.py`, so legacy suites and v1.4/v1.5 additions are classified together without per-test decorators.

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
| `test_compaction.py` | 14 | episode compaction, fallback summarisation, token accounting, and return-shape stability |
| `test_config.py` | 15 | typed config fallback and diagnostics |
| `test_context.py` | 29 | context assembly, bundle split, compression |
| `test_core_data.py` | 16 | frontmatter, effectiveness, lineage, atomic write |
| `test_curator_pipeline.py` | 85 | composable curator actions, pipeline aggregation, report persistence, boundary/error isolation |
| `test_dashboard.py` | 21 | dashboard API, curator/reflection endpoints, zones |
| `test_e2e.py` | 6 | end-to-end lifecycle integration |
| `test_entity_extraction.py` | 21 | entity index lifecycle, regex patterns, weight hierarchy |
| `test_fusion_rerank.py` | 17 | recency, effectiveness, Hebbian boost, fusion |
| `test_graph.py` | 17 | graph decay, distill, cross-zone, orphan cleanup |
| `test_graph_distil_failure.py` | 1 | distil write failure exception handling |
| `test_graph_operations.py` | 15 | compat graph CRUD, spread activation, PageRank |
| `test_host_contract_smoke.py` | 1 | host contract and smoke script |
| `test_hooks.py` | 19 | v0.16.0 enhanced hooks |
| `test_lb.py` | 9 | late-binding helper, module resolution, fail-open |
| `test_memory_curator.py` | 9 | legacy curator compatibility, cold-store lifecycle, restore |
| `test_optional_deps.py` | 5 | optional dependency fallback paths |
| `test_reflect.py` | 23 | reflection engine, facts, logs, raw chunk |
| `test_reflection.py` | 27 | runtime reflection, hook cadence, JSON repair |
| `test_reflection_scope.py` | 4 | scope propagation across reflection, compaction, and manual reflection |
| `test_reranker.py` | 13 | reranker abstraction and SearchIndex integration |
| `test_reranker_exceptions.py` | 5 | reranker OOM/API failure fallbacks |
| `test_memory_events.py` | 23 | v1.6: event ledger ADD/UPDATE/DELETE/PIN/UNPIN/SUPERSEDE, query filters, atomicity, history |
| `test_scope_filters.py` | 24 | v1.6: scoped write/search/list/update/delete/explain, migration, NULL matching |
| `test_runtime_import_hygiene.py` | 4 | runtime recovery, compile-profile, tool handler dispatch |
| `test_schema_module.py` | 18 | runtime schema definitions, JSON schema validity |
| `test_search.py` | 16 | RRF, MMR, conflict, explain, entity boost |
| `test_store.py` | 21 | rebuild/validate/prune, lineage, delete callbacks |
| `test_palace_recall.py` | 4 | palace recall zone filters and scoped fallback behavior |
| `test_reflection_refinement.py` | 3 | refined candidate extraction and metadata propagation |
| `test_semantic_supersedes.py` | 7 | semantic correction/merge/store/scope-split resolution |
| `test_typed_fact_sidecar.py` | 4 | typed fact sidecar persistence, invalidation, and entity mentions |
| `test_runtime_graph_aliases.py` | 7 | runtime graph alias helpers and host-facing graph surfaces |
| `test_store_module_split.py` | 11 | core module split, behavioral compatibility, backward compat |
| `test_tool_handlers.py` | 9 | runtime tool lineage and write/read helpers |
| `test_wave3_retrieval.py` | 15 | spread activation, CJK, fusion, time sorting |

---

## Functional Grouping

### Curator

- `test_memory_curator.py`: legacy-compatible curator behavior and cold-store lifecycle
- `test_curator_pipeline.py`: canonical v1.5 curator action pipeline, orchestration, side effects, and boundary handling

### Runtime and Contracts

- `test_hooks.py`: hook state and lifecycle counters
- `test_checkpoint.py` / `test_checkpoint_backup_failure.py`: checkpoint persistence and recovery edges
- `test_schema_module.py`: runtime schema/registration contracts
- `test_runtime_import_hygiene.py`: runtime recovery, compile-profile import-path regressions, tool handler late-binding dispatch
- `test_reflection_scope.py`: scope propagation across reflection, compaction, and manual reflection entrypoints
- `test_lb.py`: late-binding and standalone loading safety

### Store and Data

- `test_store.py`, `test_core_data.py`, `test_store_module_split.py`, `test_async_writer.py`

### Retrieval and Graph

- `test_search.py`, `test_bm25.py`, `test_fusion_rerank.py`, `test_wave3_retrieval.py`, `test_reranker.py`, `test_reranker_exceptions.py`, `test_entity_extraction.py`, `test_graph.py`, `test_graph_operations.py`, `test_graph_distil_failure.py`

### Integration and Host Surfaces

- `test_bridge.py`, `test_dashboard.py`, `test_tool_handlers.py`, `test_e2e.py`, `test_host_contract_smoke.py`

---

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
| `v14_config` / `v14_backend` | typed config + backend capability abstraction | `test_config.py`, `test_backend.py` |

Verified selection example:

```powershell
pytest tests/ -m "v14" -q
```

Observed result: **49 passed** (v1.4 exclusive subset). Full suite collection snapshot: **615 tests**.

---

## Functional Coverage Summary

### Store and Data Contracts

- `test_store.py` covers index rebuild, validation, prune behavior, supersedes boundaries, and delete callback fail-open behavior.
- `test_core_data.py` covers frontmatter round-trip, effectiveness decay math, lineage helpers, and atomic-write safety.

### Retrieval and Ranking

- `test_search.py` covers RRF fusion, MMR rerank, conflict detection, cache-key boundaries, graph wiring, explain payloads, and entity boost diagnostics.
- `test_bm25.py` covers English, mixed-language, and CJK tokenization plus BM25 scoring semantics, and index build failure graceful degradation.
- `test_fusion_rerank.py` covers recency, effectiveness, Hebbian boost, supersedes penalties, channel normalization, and pipeline ranking sensitivity.
- `test_reranker.py` covers lazy provider loading and second-stage reranker integration.
- `test_reranker_exceptions.py` covers reranker OOM/API failure fallbacks and SDK shape compatibility.
- `test_palace_recall.py` covers palace recall zone filters and scoped fallback behavior.
- `test_wave3_retrieval.py` covers graph-assisted retrieval, CJK stopword behavior, and minimal fusion-search flows.
- `test_entity_extraction.py` covers 6 regex patterns, weight hierarchy, dedup, normalization, and pipeline integration.

### Context and Runtime

- `test_context.py` covers classic string assembly, `ContextBundle` stable/dynamic split, token-budget pressure, and compression-level diagnostics.
- `test_reflection.py` covers runtime hook cadence, truncated-JSON repair, supersedes regression, checkpoint recovery integration, and timeout fallback behavior.
- `test_checkpoint.py` covers atomic write, corrupt-checkpoint backup, pending-work recovery, max-pending cap, and clear-pending.
- `test_checkpoint_backup_failure.py` covers the edge case where corrupt-checkpoint backup itself fails (`os.replace` failure path).
- `test_config.py` covers typed config fallback, unknown-key diagnostics, float-string parsing, and feature flags.
- `test_hooks.py` covers v0.16.0 enhanced hooks: `api_error`, `subagent_start/stop`, `session_reset`.
- `test_tool_handlers.py` covers lineage-cycle prevention and host tool helper compatibility.
- `test_async_writer.py` covers async writer `queue.Full` fallback and worker exception handling.
- `test_runtime_import_hygiene.py` covers runtime recovery, compile-profile late-binding, and tool handler dispatch through `_lb`.

### Reflection, Curation, and Lifecycle

- `test_reflect.py` covers reflection-engine heuristics, fact extraction, raw-chunk mode, full reflection, and reflect-log behavior.
- `test_reflection_refinement.py` covers refined candidate extraction and metadata propagation.
- `test_semantic_supersedes.py` covers semantic correction/merge/store/scope-split resolution.
- `test_curator_pipeline.py` covers the canonical curator pipeline: action isolation, orchestration, report persistence, fail-open boundaries, and backward-compatible wrappers.
- `test_memory_curator.py` now covers the legacy compatibility slice: stale scan, archive wrappers, compact wrappers, cold-store append/load, restore, and no-graph cleanup.
- `test_compaction.py` covers episode clustering, fallback summarisation, token accounting, quality-gated LLM fallback, idempotency, and compact return-shape guarantees.
- `test_typed_fact_sidecar.py` covers typed fact sidecar persistence, invalidation, and entity mentions.

### Graph and API Surfaces

- `test_graph.py` covers graph decay, distillation, cross-zone analysis, and orphan-edge cleanup.
- `test_graph_operations.py` covers the compatibility graph CRUD layer, spread activation, PageRank, regression, and thread-safe reads.
- `test_graph_distil_failure.py` covers distil write failure exception handling.
- `test_runtime_graph_aliases.py` covers runtime graph alias helpers and host-facing graph surfaces.
- `test_dashboard.py` covers dashboard CRUD, graph views, stats, curator endpoints, reflection endpoints, and zone listing.
- `test_bridge.py` covers host bridge mirroring in both directions, duplicate handling, capacity checks, and tool-output cleanup.
- `test_host_contract_smoke.py` keeps the host-facing smoke script inside pytest as an acceptance gate.
- `test_e2e.py` exercises the full store-search-graph-reflection-context chain.

### Config and Dependencies

- `test_config.py` covers typed config model, diagnostics, float string parsing, and feature flags.
- `test_optional_deps.py` covers missing dependency fallback paths for frontmatter, tiktoken, spaCy, jieba, and ONNX.

---

## Maintenance Notes

- `tests/pytest.ini` is the source of truth for marker registration.
- `tests/conftest.py` is the source of truth for automatic marker assignment.
- `tests/_helpers.py` provides shared mock classes and test utilities.
- Latest coverage refresh: `pytest tests --collect-only -q` reported **615 collected tests**.
- When adding new tests, prefer classifying them by functional surface first; add a `v14_*` style marker only when the test is part of a versioned acceptance slice.
- Keep this document aligned to collected reality. The minimum refresh is:
  1. rerun `pytest tests --collect-only -q`
  2. update module counts
  3. update any version-specific acceptance notes
