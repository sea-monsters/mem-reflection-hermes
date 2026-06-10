# Test Suite Inventory — mem-reflection-hermes v1.4

**Generated**: 2026-06-09  
**Total test files**: 28  
**Total tests**: 413 passed  
**Last verified**: 2026-06-10 (`pytest tests/ -q` → 413 passed in ~23s)  

## Quick Reference

```bash
# All tests
pytest tests/ -v

# v1.4 new feature tests only (fast)
pytest tests/ -m v14 -v

# By functional category
pytest tests/ -m store -v      # Core persistence
pytest tests/ -m search -v     # Search engine
pytest tests/ -m retrieval -v  # Retrieval quality
pytest tests/ -m graph -v      # Graph memory
pytest tests/ -m reflection -v # Reflection engine
pytest tests/ -m runtime -v    # Runtime hooks + checkpoint
pytest tests/ -m context -v    # Context assembly
pytest tests/ -m config -v     # Typed config
pytest tests/ -m e2e -v        # End-to-end
```

## Test File Directory

### Core / Store (foundation layer)

| File | Tests | Classes | What it covers |
|------|-------|---------|----------------|
| `test_core_data.py` | 16 | 5 | Frontmatter serialization, CJK content, data model integrity |
| `test_store.py` | 18 | 8 | rebuild_index, validate_index, prune_index, lineage, callbacks, entity rebuild, health metrics |
| `test_async_writer.py` | 3 | 2 | **v1.4 gap-fill**: async writer `queue.Full` fallback, worker exception handling |

**Run**: `pytest tests/test_core_data.py tests/test_store.py tests/test_async_writer.py -v`

### Search / Retrieval

| File | Tests | Classes | What it covers |
|------|-------|---------|----------------|
| `test_search.py` | 16 | 5 | RRF fusion, MMR re-ranking, conflict detection, cache boundaries, graph wiring, explain signals |
| `test_bm25.py` | 14 | 2 | BM25 IDF/TF/length norm, CJK tokenization (jieba/bigram), stopword filtering |
| `test_fusion_rerank.py` | 17 | 9 | Fusion 6-dimension ranking: channel norm, recency, effectiveness, supersedes, zone diversity, MMR |
| `test_entity_extraction.py` | 21 | 6 | **v1.4**: 6 regex patterns, weight hierarchy, dedup, normalization, pipeline integration |
| `test_reranker.py` | 13 | 0 | BaseReranker interface, CrossEncoder/Cohere construction, lazy loading |
| `test_reranker_exceptions.py` | 5 | 2 | **v1.4 gap-fill**: reranker OOM/API failure fallbacks, SDK shape compatibility |
| `test_backend.py` | 6 | 2 | **v1.4**: SearchBackendLike protocol, capability flags, frozen dataclass |

**Run**: `pytest tests/test_search.py tests/test_bm25.py tests/test_fusion_rerank.py tests/test_entity_extraction.py tests/test_reranker.py tests/test_reranker_exceptions.py tests/test_backend.py -v`

### Graph Memory

| File | Tests | Classes | What it covers |
|------|-------|---------|----------------|
| `test_graph.py` | 17 | 5 | step_decay (HeLa-Mem), distill, cross_zone analysis |
| `test_graph_operations.py` | 15 | 6 | Edge CRUD, spread activation decay sensitivity, PageRank |
| `test_graph_distil_failure.py` | 1 | 1 | **v1.4 gap-fill**: distil write failure exception handling |
| `test_wave3_retrieval.py` | 15 | 6 | Spread activation convergence, read-without-lock, hub detection, BM25 CJK |

**Run**: `pytest tests/test_graph.py tests/test_graph_operations.py tests/test_graph_distil_failure.py tests/test_wave3_retrieval.py -v`

### Reflection Engine

| File | Tests | Classes | What it covers |
|------|-------|---------|----------------|
| `test_reflect.py` | 23 | 5 | is_memorable gate, fact extraction, body refinement, conflict detection |
| `test_reflection.py` | 27 | 5 | JSON parsing/repair, audit logging, pre_llm_call timeout, session recovery |
| `test_compaction.py` | 11 | 2 | Episode compaction: trigger conditions, summary generation, pruning |

**Run**: `pytest tests/test_reflect.py tests/test_reflection.py tests/test_compaction.py -v`

### Runtime / Context / Checkpoint

| File | Tests | Classes | What it covers |
|------|-------|---------|----------------|
| `test_context.py` | 29 | 6 | **v1.4**: ContextBundle stable/dynamic split, graded compression, token budget, debug metadata |
| `test_hooks.py` | 13 | 4 | **v1.4**: v0.16.0 enhanced hooks (api_error, subagent_start/stop, session_reset) |
| `test_checkpoint.py` | 16 | 3 | **v1.4**: Atomic write, corrupt backup, pending recovery, max_pending_sessions cap, clear_pending |
| `test_checkpoint_backup_failure.py` | 1 | 1 | **v1.4 gap-fill**: corrupt checkpoint when `os.replace` backup itself fails |
| `test_tool_handlers.py` | 9 | 3 | Lineage cycle detection, root/latest helpers, write/read cycle |
| `test_host_contract_smoke.py` | 1 | 0 | Host-facing contract smoke test |

**Run**: `pytest tests/test_context.py tests/test_hooks.py tests/test_checkpoint.py tests/test_checkpoint_backup_failure.py tests/test_tool_handlers.py tests/test_host_contract_smoke.py -v`

### Memory Curation

| File | Tests | Classes | What it covers |
|------|-------|---------|----------------|
| `test_memory_curator.py` | 27 | 11 | 5-phase curation: TTL/staleness, supersedes, similarity, orphan cleanup, cold storage |

**Run**: `pytest tests/test_memory_curator.py -v`

### Bridge / Sync

| File | Tests | Classes | What it covers |
|------|-------|---------|----------------|
| `test_bridge.py` | 35 | 7 | Bidirectional sync, Dir A (write→builtin), Dir B (read←builtin), conflict resolution |

**Run**: `pytest tests/test_bridge.py -v`

### Integration / E2E / Dashboard

| File | Tests | Classes | What it covers |
|------|-------|---------|----------------|
| `test_dashboard.py` | 18 | 7 | FastAPI endpoints: CRUD, graph view, zones, neighbors, CLUQI |
| `test_e2e.py` | 6 | 1 | Full lifecycle: store → search → graph → reflect → context → dashboard |

**Run**: `pytest tests/test_dashboard.py tests/test_e2e.py -v`

### Config / Dependencies

| File | Tests | Classes | What it covers |
|------|-------|---------|----------------|
| `test_config.py` | 15 | 3 | **v1.4**: Typed config model, diagnostics, float string parsing, feature flags |
| `test_optional_deps.py` | 5 | 5 | **v1.4 gap-fill**: frontmatter/tiktoken/spaCy/jieba/ONNX missing fallback paths |

**Run**: `pytest tests/test_config.py tests/test_optional_deps.py -v`

---

## v1.4 Feature Test Set

These tests exercise functionality **new or significantly enhanced in v1.4**.

### v1.4 Context Reliability

| Test | File | Intent |
|------|------|--------|
| `test_context_bundle_splits_stable_and_dynamic_sections` | test_context.py | Stable/dynamic split (FEP §5.3) |
| `test_context_bundle_stable_only_omits_dynamic_sections` | test_context.py | Stable-only mode |
| `test_bundle_records_compression_level_under_pressure` | test_context.py | Graded compression recording |
| `test_emergency_compression_keeps_pinned_stable_context` | test_context.py | Pinned survives emergency |
| `test_pre_llm_call_uses_stable_fallback_on_timeout` | test_reflection.py | Timeout → stable fallback |
| `test_pre_llm_call_zero_timeout_uses_fallback` | test_reflection.py | Zero timeout boundary |
| `test_pre_llm_call_timeout_does_not_corrupt_session_state` | test_reflection.py | Timeout safety |

### v1.4 Search Retrieval Enhancement

| Test | File | Intent |
|------|------|--------|
| `test_jieba_search_mode_uses_search_tokens` | test_bm25.py | CJK search-mode tokenization |
| `test_auto_mode_falls_back_to_bigram_without_jieba` | test_bm25.py | Bigram fallback |
| `test_explain_contains_all_signal_components` | test_search.py | 14 signal fields |
| `test_entity_links_are_indexed_and_deleted_without_orphans` | test_search.py | Entity lifecycle |
| `test_entity_boost_and_hits_appear_in_explain` | test_search.py | Entity boost in explain |
| `test_rebuild_index_recreates_entity_links` | test_search.py | Rebuild consistency |
| `test_entity_weight_boundary_values` | test_search.py | Entity enabled/disabled |

### v1.4 Entity Recall Layer (dedicated file)

| Test | File | Intent |
|------|------|--------|
| All 21 tests | test_entity_extraction.py | 6 regex patterns, weight hierarchy, dedup, pipeline |

### v1.4 Runtime Reliability

| Test | File | Intent |
|------|------|--------|
| `test_corrupt_checkpoint_is_backed_up_and_defaults_returned` | test_checkpoint.py | Corrupt handling |
| `test_recover_pending_work_runs_available_stages_and_clears_them` | test_checkpoint.py | Recovery pipeline |
| `test_on_session_start_runs_pending_recovery` | test_reflection.py | Start recovery |
| `test_on_session_end_marks_reflection_pending_when_reflection_fails` | test_reflection.py | Failure checkpointing |
| `test_invalid_types_fall_back_to_defaults` | test_config.py | Config safety |
| `test_unknown_keys_are_reported` | test_config.py | Config diagnostics |

### v1.4 Backend / Config (dedicated files)

| Test | File | Intent |
|------|------|--------|
| All 6 tests | test_backend.py | Protocol conformance, capability flags |
| All 15 tests | test_config.py | Typed config, validation, fallbacks |

### v1.4 Gap-Fill Tests (new files)

| File | Tests | What it covers |
|------|-------|----------------|
| `test_hooks.py` | 13 | v0.16.0 hooks: api_error, subagent, session_reset |
| `test_async_writer.py` | 3 | Async writer queue.Full fallback, worker exception |
| `test_reranker_exceptions.py` | 5 | Reranker failure fallbacks |
| `test_checkpoint_backup_failure.py` | 1 | Corrupt backup failure path |
| `test_graph_distil_failure.py` | 1 | Distil write failure path |
| `test_optional_deps.py` | 5 | Optional dependency fallbacks |

---

## v1.4 Quick Test Command

```bash
# Fast: only v1.4 new features and enhancements (49 tests, ~1s)
pytest tests/ -m "v14" -v

# By v1.4 sub-area
pytest tests/ -m "v14_context" -v      # ContextBundle + compression (7 tests)
pytest tests/ -m "v14_retrieval" -v    # BM25 CJK + explain (4 tests)
pytest tests/ -m "v14_runtime" -v      # Checkpoint + config safety (6 tests)
pytest tests/ -m "v14_entity" -v       # Entity extraction + backend (4 tests)
pytest tests/ -m "v14_contract" -v     # Host contract smoke (1 test)
pytest tests/ -m "v14_hooks" -v        # v0.16.0 enhanced hooks (13 tests)

# Combined with node-level markers + file-level v14
pytest tests/ -m "v14_context or v14_retrieval or v14_entity" -v
```
