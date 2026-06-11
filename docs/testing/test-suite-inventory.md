# Test Suite Inventory - mem-reflection-hermes v1.5

**Generated**: 2026-06-11
**Total test files**: 34
**Total tests**: 511 passed
**Last verified**: 2026-06-11 (`pytest tests/ -q` -> 511 passed in ~17s)

## Quick Reference

```bash
# All tests
pytest tests/ -v

# v1.4 new feature tests only
pytest tests/ -m v14 -v

# Functional slices
pytest tests/ -m store -v
pytest tests/ -m search -v
pytest tests/ -m retrieval -v
pytest tests/ -m graph -v
pytest tests/ -m reflection -v
pytest tests/ -m runtime -v
pytest tests/ -m context -v
pytest tests/ -m curator -v
pytest tests/ -m config -v
pytest tests/ -m e2e -v
```

## Current Test Matrix

### Core, Store, and Compatibility

| File | Tests | Classes | What it covers |
|------|------:|---------|----------------|
| `test_core_data.py` | 16 | 5 | Frontmatter serialization, CJK content, data model integrity |
| `test_store.py` | 18 | 8 | rebuild_index, validate_index, prune_index, lineage, callbacks, entity rebuild, health metrics |
| `test_async_writer.py` | 3 | 2 | async writer `queue.Full` fallback, worker exception handling |
| `test_store_module_split.py` | 11 | 5 | split-core behavioral compatibility, package exports, backward-compat surface |

### Search and Retrieval

| File | Tests | Classes | What it covers |
|------|------:|---------|----------------|
| `test_search.py` | 16 | 5 | RRF fusion, MMR re-ranking, conflict detection, cache boundaries, graph wiring, explain signals |
| `test_bm25.py` | 16 | 3 | BM25 IDF/TF/length norm, CJK tokenization (jieba/bigram), stopword filtering, index build failure graceful degradation |
| `test_fusion_rerank.py` | 17 | 9 | fusion ranking: channel norm, recency, effectiveness, supersedes, zone diversity, MMR |
| `test_entity_extraction.py` | 21 | 6 | 6 regex patterns, weight hierarchy, dedup, normalization, pipeline integration |
| `test_reranker.py` | 13 | 0 | BaseReranker interface, CrossEncoder/Cohere construction, lazy loading |
| `test_reranker_exceptions.py` | 5 | 2 | reranker OOM/API failure fallbacks, SDK shape compatibility |
| `test_backend.py` | 6 | 2 | SearchBackendLike protocol, capability flags, frozen dataclass |
| `test_wave3_retrieval.py` | 15 | 6 | spread activation convergence, read-without-lock, hub detection, BM25 CJK |

### Graph Memory

| File | Tests | Classes | What it covers |
|------|------:|---------|----------------|
| `test_graph.py` | 17 | 5 | step_decay, distill, cross_zone analysis |
| `test_graph_operations.py` | 15 | 6 | edge CRUD, spread activation decay sensitivity, PageRank |
| `test_graph_distil_failure.py` | 1 | 1 | distill write failure exception handling |

### Reflection Engine

| File | Tests | Classes | What it covers |
|------|------:|---------|----------------|
| `test_reflect.py` | 23 | 5 | memorable-content gate, fact extraction, body refinement, conflict detection |
| `test_reflection.py` | 27 | 5 | JSON parsing/repair, audit logging, pre_llm_call timeout, session recovery |
| `test_compaction.py` | 11 | 2 | episode compaction: trigger conditions, summary generation, pruning |

### Runtime, Context, and Checkpoint

| File | Tests | Classes | What it covers |
|------|------:|---------|----------------|
| `test_context.py` | 27 | 6 | ContextBundle stable/dynamic split, graded compression, token budget, debug metadata |
| `test_hooks.py` | 13 | 4 | enhanced hooks: api_error, subagent_start/stop, session_reset |
| `test_checkpoint.py` | 16 | 3 | atomic write, corrupt backup, pending recovery, max_pending cap, clear_pending |
| `test_checkpoint_backup_failure.py` | 1 | 1 | corrupt checkpoint when `os.replace` backup fails |
| `test_tool_handlers.py` | 9 | 3 | lineage-cycle prevention and host tool helper compatibility |
| `test_host_contract_smoke.py` | 1 | 0 | host-facing contract smoke test |
| `test_runtime_import_hygiene.py` | 3 | 3 | runtime recovery, compile-profile import-path regressions, tool handler late-binding dispatch |
| `test_lb.py` | 9 | 2 | late-binding helper, module resolution, fail-open |

### Curator

| File | Tests | Classes | What it covers |
|------|------:|---------|----------------|
| `test_curator_pipeline.py` | 81 | 17 | canonical curator action pipeline, orchestration, side effects, boundary/error isolation |
| `test_memory_curator.py` | 9 | 6 | legacy curator compatibility, cold-store lifecycle, restore |

### Bridge and Integration

| File | Tests | Classes | What it covers |
|------|------:|---------|----------------|
| `test_bridge.py` | 35 | 7 | bidirectional sync, body refinement, duplicate handling, capacity checks |
| `test_dashboard.py` | 18 | 7 | FastAPI endpoints: CRUD, graph view, zones, neighbors, curator, reflections |
| `test_e2e.py` | 6 | 1 | full lifecycle: store -> search -> graph -> reflect -> context -> dashboard |

### Config and Optional Dependencies

| File | Tests | Classes | What it covers |
|------|------:|---------|----------------|
| `test_config.py` | 15 | 3 | typed config model, diagnostics, float string parsing, feature flags |
| `test_optional_deps.py` | 5 | 5 | frontmatter/tiktoken/spaCy/jieba/ONNX missing fallback paths |

## Intent Notes

- `test_curator_pipeline.py` is the canonical v1.5 curator suite.
- `test_memory_curator.py` now stays intentionally smaller and only preserves the legacy compatibility slice.
- `test_runtime_import_hygiene.py` and `test_store_module_split.py` are regression guards for the split-module runtime shape, not source-scan tests.
- Behavior-facing tests should prefer user-visible effects over implementation inspection.
- `MockFrontmatter`, `MockMemory`, `MockStore` are centralized in `tests/_helpers.py` (extracted from curator tests in Round 2).
- Private formatting helper tests (`_format_memory`, `_format_skill`) were removed in Round 2 — these are implicitly covered by public `build_context()` path.

## Maintenance Notes

- `tests/pytest.ini` is the source of truth for marker registration.
- `tests/conftest.py` is the source of truth for automatic marker assignment.
- `tests/_helpers.py` provides shared mock classes and test utilities.
- Keep this document aligned to collected reality:
  1. rerun `pytest tests --collect-only -q`
  2. update module counts
  3. update any version-specific acceptance notes

## Change Log

- **2026-06-11 (Round 2 P1/P2/P3 fixes)**: 510→511 tests. Added BM25 index failure tests (+2), tool handler dispatch test (+1), removed private formatting tests (-2). Extracted mock classes to `_helpers.py`.
