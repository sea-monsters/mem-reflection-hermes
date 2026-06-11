# v1.5 Round 2 Test Suite Review — Functional Intent Audit

**Date**: 2026-06-11
**Scope**: Second pass functional-intent audit. Compare against [Round 1 findings](round1-test-suite-review.md). Identify remaining implementation-leak points, missing intent coverage, and test-design improvements.
**Status**: P1/P2/P3 fixes completed 2026-06-11.

## Summary

Round 1 cleaned up the major implementation-leak categories (AST counting, `__dict__` access, source-string scanning, identity assertions). Round 2 finds the suite in much better shape — most tests now express functional intent clearly. The remaining issues are subtler: a few tests still reach into private internals when observable public side effects would suffice, some error/fail-open branches still lack explicit warning assertions, and a handful of tests have fragile temporal assumptions.

**Overall assessment**: The test suite is functionally sound. All 511 tests pass. The P1/P2/P3 improvements have been applied.

## 1. Comparison with Round 1

| Round 1 Finding | Status in Round 2 |
|---|---|
| `__dict__` access for report_text | **Fixed** — now uses `getattr(result, "report_text", None)` |
| AST counting of helper definitions | **Fixed** — removed entirely |
| Source-string scanning for import paths | **Fixed** — replaced with runtime recovery tests |
| Identity/line-count assertions in module split | **Fixed** — now behavioral compatibility checks |
| Fragile default temp paths in legacy curator | **Fixed** — isolated cold-store paths |
| Report persistence not asserted | **Fixed** — `TestReportPersistence` added |
| Curator enable/disable contract | **Fixed** — `TestCuratorConfig` covers on/off |
| CompactChains head-update failure | **Fixed** — `test_update_head_failure_is_reported_after_archiving` |
| ArchiveSuperseded recent-access preservation | **Fixed** — `test_recently_accessed_node_is_skipped` |
| Orchestrator fallback when report gen fails | **Fixed** — `test_report_failure_falls_back_to_text_summary` |
| Fail-open warning assertions | **Partially addressed** — some still missing (see §3) |

## 2. Module-by-Module Audit

### 2.1 Curator Pipeline (`test_curator_pipeline.py`, 81 tests)

**Strengths (maintained from R1)**:
- Pipeline ordering test (`test_compact_runs_before_archive`) is a textbook example of intent via observable invariant
- Error isolation test correctly patches module-level binding, not source
- Report persistence now asserts both file content and reflection-log side effect
- Cold store I/O tests cover corrupt JSONL, prune, restore round-trip

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| CP-1 | ~~MEDIUM~~ **FIXED** | ~~`test_body_refinement_in_cold_entry` monkeypatches `_refine_body_fn` — a private function seam. The intent (body is cleaned before archiving) is correct, but the patch target is an internal detail. A better approach: verify the *output* (entry["body"] does not contain code blocks/tool markers) rather than injecting a sentinel transformation.~~ **Fixed**: Replaced with `test_refines_body_strips_tool_noise_and_truncates` that verifies output content directly. |
| CP-2 | LOW | `_cold_store_path_override` is used heavily in MockStore. This is a test seam in production code — acceptable for now but worth noting as a pattern that couples tests to implementation. |
| CP-3 | ~~INFO~~ **FIXED** | ~~`MockFrontmatter` / `MockMemory` / `MockStore` are duplicated between `test_curator_pipeline.py` and `test_memory_curator.py` (identical classes). Could be extracted to `tests/_helpers.py` for DRY, but this is a maintenance concern, not an intent issue.~~ **Fixed**: Extracted to `tests/_helpers.py` in P3. |

### 2.2 Legacy Curator (`test_memory_curator.py`, 9 tests)

**Strengths**: Much thinner after R1 cleanup. Now correctly scoped as backward-compat regression suite.

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| MC-1 | MEDIUM | `test_restore_succeeds_without_graph` calls `_restore_from_cold` — a private function. The intent (cold entry can be restored) is correct, but the test should ideally go through `restore_from_cold` (public API) or at minimum assert the store state change through public methods. |
| MC-2 | ~~LOW~~ **FIXED** | ~~MockStore/Store duplication with `test_curator_pipeline.py` (see CP-3).~~ **Fixed**: Extracted to `tests/_helpers.py` in P3. |

### 2.3 Runtime Hooks (`test_hooks.py`, 12 tests)

**Strengths**: Clean isolation via `_setup_namespace()`. Checkpoint hooks properly neutralized via `autouse` fixture. Session state tests are pure observable-behavior.

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| HK-1 | LOW | Tests access `_hooks._session_states` dict directly for assertions. This is the simplest way to verify state, and `_session_states` is the canonical data structure, so this is acceptable. No change needed. |
| HK-2 | INFO | `_on_session_reset` tests only verify logging output, not any state mutation. This is correct — the hook is purely observational. |

### 2.4 Schema Module (`test_schema_module.py`, 10 tests)

**Strengths**: Schema-contract assertions (type, required fields, enum values) are good functional-intent tests. Register behavior verified through `call_count` and `kwargs` inspection.

**No remaining issues found.**

### 2.5 Late Binding (`test_lb.py`, 9 tests)

**Strengths**: Focused on standalone loading behavior and fail-open semantics. `test_returns_none_for_none_input` is a good boundary test.

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| LB-1 | LOW | `test_caches_successful_lookup` asserts `first is second` — this is identity comparison, but for module caching it's the correct intent (same object = cache hit). Acceptable. |

### 2.6 Runtime Import Hygiene (`test_runtime_import_hygiene.py`, 3 tests)

**Strengths**: Tests exercise real runtime behavior (session recovery dispatches to stage runners; compile profile uses late bindings and writes output). Far superior to the R0 source-scanning approach. New tool handler dispatch test validates `_lb` usage for `_tool_srh_memory_search`.

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| RH-1 | ~~INFO~~ **FIXED** | ~~Only 2 tests in this file. The coverage is narrow — session recovery and compile profile. Other runtime paths (tool handlers, graph tools) are covered elsewhere but could benefit from similar real-execution tests.~~ **Fixed**: Added tool handler dispatch test in P2. |

### 2.7 Store Module Split (`test_store_module_split.py`, 8 tests)

**Strengths**: Pure behavioral compatibility — verifies that `store.X()` produces identical results to `canonical_module.X()`. No identity checks, no line counts.

**No remaining issues found.**

### 2.8 Store Index Tooling (`test_store.py`, ~15 tests)

**Strengths**: Rebuild/validate/prune tests verify observable data outcomes (file existence, DB row counts, orphan detection). Post-delete callback tests verify both success and failure paths.

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| ST-1 | LOW | `test_prune_removes_orphaned_rows` uses `store._get_conn()` to verify DB state directly. Acceptable for index integrity tests since the public API (`store.get()`) returns `None` for orphaned rows regardless — the DB query is the only way to verify the row was actually removed. |
| ST-2 | INFO | `test_duplicate_detection_without_datasketch` removes `datasketch` from `sys.modules` — fragile pattern that depends on import order. Works correctly but could break if test ordering changes. |

### 2.9 Search (`test_search.py`, ~14 tests)

**Strengths**: RRF fusion tests verify mathematical properties (k=60 constant, overlap boost). MMR tests verify diversity behavior. Graph wiring tests verify observable ranking changes.

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| SR-1 | LOW | `test_mmr_promotes_diversity` assertion `assert len(reranked) == 3` only checks count, not that diversity actually happened. The test body comments acknowledge this. The `test_mmr_lambda_zero_pure_diversity` test is stronger and covers the actual intent. |

### 2.10 Context Assembly (`test_context.py`, ~27 tests)

**Strengths**: Layer priority tests are intent-excellent — they verify section headers in output text. Graded compression tests verify `debug["compression_level"]` metadata. Budget enforcement tests check section inclusion/exclusion.

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| CX-1 | ~~MEDIUM~~ **FIXED** | ~~`test_format_memory_truncation` calls `_context._format_memory()` directly — a private formatting helper. The intent (long bodies are truncated) is correct. However, this is tested through the public `build_context()` path implicitly in the budget tests. The direct call is redundant and couples to internal API. Consider removing or replacing with a public-path test.~~ **Fixed**: Removed in P3 — formatting is covered through public `build_context()` path. |
| CX-2 | ~~LOW~~ **FIXED** | ~~`test_format_skill_basic` similarly calls `_context._format_skill()` directly. Same assessment as CX-1.~~ **Fixed**: Removed in P3. |

### 2.11 Graph (`test_graph.py`, ~16 tests)

**Strengths**: Step decay tests verify weight reduction, pruning threshold, and counter accumulation — all through observable return values. Distill tests verify semantic memory creation (zone, tags). Cross-zone tests verify bridge detection. Orphan cleanup tests verify DB state.

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| GR-1 | ~~MEDIUM~~ **FIXED** | ~~`test_step_counter_incremented` directly reads `gi._step_counter` — a private attribute. The intent (spread increments counter) is correct, but `step_decay()` return value already exposes `steps_since_last_decay`. The test could verify counter indirectly through decay results.~~ **Fixed**: Replaced with `test_spread_accumulates_decay_steps` that uses `step_decay()` return values. |
| GR-2 | LOW | Multiple tests call `gi._get_conn()` for DB verification. Same assessment as ST-1 — acceptable for graph integrity tests where public API doesn't expose row-level state. |

### 2.12 Bridge (`test_bridge.py`, ~22 tests)

**Strengths**: Dir A and Dir B both thoroughly tested with observable outcomes (search hits, file existence, body content). Body refinement tests are particularly clean — they verify output content, not internal processing.

**No remaining issues found.**

### 2.13 Reflection (`test_reflect.py`, ~15 tests + `test_reflection.py`, ~15 tests)

**Strengths**: Content gate tests are pure input/output classification. Fact extraction tests verify source categorization. JSON repair tests verify mathematical properties of truncation recovery.

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| RF-1 | ~~MEDIUM~~ **FIXED** | ~~`test_pre_llm_call_keeps_counter_when_llm_reflection_skips_without_ctx` directly reads `_lifecycle_mod._turns_since_reflect` — a private counter. The intent (counter not incremented when reflection skipped) is correct, but the test could verify the observable effect (next call triggers reflection) instead of internal counter value.~~ **Fixed**: Replaced with `test_pre_llm_call_skips_micro_reflection_when_ctx_unavailable` that verifies behavioral outcome. |
| RF-2 | ~~LOW~~ **FIXED** | ~~`test_pre_llm_call_timeout_does_not_corrupt_session_state` directly reads `_lifecycle_mod._session_messages` — similar to RF-1. The intent is valuable (timeout doesn't corrupt state) but reaches into internals.~~ **Fixed**: Replaced with `test_pre_llm_call_timeout_produces_valid_stable_fallback` that verifies output validity. |

### 2.14 Checkpoint (`test_checkpoint.py`, ~14 tests)

**Strengths**: Excellent intent alignment. Tests verify file-level outcomes (backup creation, temp file cleanup), behavioral properties (atomic write, cap enforcement, recency ordering), and failure recovery.

**No remaining issues found.** This is one of the strongest modules in the suite.

### 2.15 Config (`test_config.py`, ~12 tests)

**Strengths**: Config parsing tests verify output model fields. Feature flag integration tests go beyond parsing to verify behavioral impact (entity_boost=0, compression_level constraints).

**Remaining issues (R2)**:

| ID | Severity | Finding |
|---|---|---|
| CF-1 | LOW | `test_entity_disabled_skips_entity_boost_in_search` directly patches `_store_mod.plugin_config` (production module-level function). Acceptable for integration test but fragile — if config loading mechanism changes, this test breaks while the intent (disabled entity → no boost) remains valid. |

### 2.16 Async Writer (`test_async_writer.py`, 3 tests)

**Strengths**: Tests verify observable outcomes (file exists, warning logged) rather than internal queue state.

**No remaining issues found.**

### 2.17 Tool Handlers (`test_tool_handlers.py`, ~10 tests)

**Strengths**: Lineage cycle detection tests are pure graph-algorithm tests with clean input/output. Write/read cycle test verifies round-trip through serialization.

**No remaining issues found.**

## 3. Cross-Cutting Findings

### 3.1 Implementation Leak Summary

| Category | Count (R1) | Count (R2) | After Fixes |
|---|---|---|---|
| `__dict__` access | ~3 | 0 | 0 (eliminated) |
| AST/source counting | ~2 | 0 | 0 (eliminated) |
| Source-string scanning | ~2 | 0 | 0 (eliminated) |
| Identity/line-count assertions | ~4 | 0 | 0 (eliminated) |
| Private function monkeypatching | ~1 | 1 (CP-1) | 0 (fixed) |
| Private attribute direct reads | ~5 | 4 (GR-1, RF-1, RF-2, LB-1) | 1 (LB-1 acceptable) |
| Private DB conn access | ~3 | 5 (ST-1, GR-2) | 5 (acceptable for integrity tests) |

*DB conn access for integrity verification has no public API alternative — these are intentional and acceptable.

### 3.2 Missing Warning Assertions

The following fail-open branches now have explicit `caplog` assertions:

| Module | Branch | Status |
|---|---|---|
| `test_curator_pipeline.py` | `archive_and_delete` failure path logs warning | **FIXED** — `test_preserves_active_memory_when_delete_fails` now asserts caplog |
| `test_async_writer.py` | Sync fallback failure is tested ✓ | Already covered |
| `test_bm25.py` | BM25 index rebuild warning when data is corrupt | **FIXED** — `TestBM25IndexBuildFailure` class added |

### 3.3 Test Stability Notes

| Pattern | Files | Assessment |
|---|---|---|
| `sys.modules` manipulation for import isolation | `test_hooks.py`, `test_reflection.py`, `test_async_writer.py`, `test_checkpoint.py` | Acceptable but fragile — test ordering dependencies could emerge |
| `_MOCK_TIME = time.time()` at module level | `test_curator_pipeline.py`, `test_memory_curator.py` | Stale if tests run across midnight — low risk in practice |
| `monkeypatch.setattr` on production module dicts | `test_config.py` (CF-1) | Works but couples to module loading order |

## 4. Recommendations (Priority Order)

### P1 — Intent Alignment Improvements ✅ COMPLETED

1. **CP-1** ✅: Replaced `_refine_body_fn` monkeypatch with output verification. `test_refines_body_strips_tool_noise_and_truncates` now verifies output content directly.

2. **GR-1** ✅: Replaced direct `_step_counter` reads with indirect verification through `step_decay()` return values. `test_spread_accumulates_decay_steps` verifies counter through decay results.

3. **RF-1/RF-2** ✅: Replaced direct `_turns_since_reflect` / `_session_messages` reads with behavioral verification. New tests verify output validity without reaching into internals.

### P2 — Coverage Gaps ✅ COMPLETED

4. ✅ Added `TestBM25IndexBuildFailure` class with 2 tests for BM25 index rebuild with corrupt data (warning log assertion).

5. ✅ Added `caplog` assertions to `test_preserves_active_memory_when_delete_fails` for `archive_and_delete` logging behavior on partial failure.

6. ✅ Added `test_runtime_tools_search_handler_uses_late_binding_and_returns_json` to `test_runtime_import_hygiene.py` for tool handler dispatch coverage.

### P3 — Maintenance ✅ COMPLETED

7. ✅ Extracted `MockFrontmatter`, `MockMemory`, `MockStore` from `test_curator_pipeline.py` and `test_memory_curator.py` into `tests/_helpers.py`.

8. ✅ Removed `test_format_memory_truncation` and `test_format_skill_basic` (CX-1, CX-2) — they tested private helpers already covered through the public `build_context()` path.

## 5. Validation

```
python -m pytest tests --collect-only -q  → 511 tests collected
python -m pytest tests -q                 → 511 passed in 16.89s
```

All 511 tests pass cleanly with no warnings.

### Changes Summary

- **Test count**: 510 → 511 (+1 net: +3 added, -2 removed)
- **New tests**: BM25 index failure (2), tool handler dispatch (1)
- **Removed tests**: Private formatting helpers (2)
- **Mock classes**: Extracted to `tests/_helpers.py`
- **Implementation leaks**: Reduced from 6 to 1 (LB-1 acceptable)
