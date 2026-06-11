# v1.5 Round 1 Test Suite Review

**Date**: 2026-06-11  
**Scope**: review current pytest suite for functional-intent expression, coverage shape, and boundary/error-path detection after the v1.5 P0 repairs.

## Summary

The test suite is now much closer to the real v1.5 intent surface than it was before the first code review, but it was still uneven in two ways:

1. some newer tests described the right intent but did not yet lock down important side effects and failure paths
2. some older legacy tests still depended on fragile default temp paths and default cold-store locations, which made them noisy on Windows and weakened their signal

This pass improves both.

## Review Findings

### 1. Functional-intent expression

Strong areas:

- `tests/test_curator_pipeline.py` now expresses pipeline intent directly instead of only checking symbol presence or return-key existence.
- runtime contract checks are better anchored by `tests/test_schema_module.py`, `tests/test_runtime_import_hygiene.py`, and `tests/test_hooks.py`.
- the suite distinguishes implementation seams from user-facing behavior reasonably well for schema, hook state, and curator orchestration.

Weak spots found in this pass:

- report generation was covered as text output, but report persistence and reflection-log side effects were not directly asserted
- curator config behavior had merge coverage, but not the explicit enable/disable contract
- several tests still leaked implementation too directly:
  - reading `report_text` through `__dict__`
  - static AST counting of helper definitions
  - directly validating private persistence helpers instead of the user-visible pipeline side effects
- some action boundaries were still under-protected:
  - `CompactChains` head-update failure after successful archiving
  - `ArchiveSuperseded` recent-access preservation
  - orchestrator fallback when report generation fails
- `tests/test_memory_curator.py` still mixed legacy behavior checks with environment-sensitive temp-path assumptions

### 2. Coverage shape

Current collection snapshot:

- `pytest tests --collect-only -q` -> **510 tests collected**
- `tests/test_curator_pipeline.py` -> **81 tests**
- `tests/test_memory_curator.py` -> **9 tests**

Coverage is now strongest around:

- curator orchestration and helper seams
- runtime schema and import-path contracts
- hook state lifecycle

Current functional grouping is now also reflected in pytest marker assignment:

- `curator`: `test_memory_curator.py`, `test_curator_pipeline.py`
- `runtime`: `test_hooks.py`, `test_checkpoint.py`, `test_runtime_import_hygiene.py`, `test_schema_module.py`, `test_lb.py`
- `store`: `test_store.py`, `test_core_data.py`, `test_store_module_split.py`, `test_async_writer.py`
- plus existing graph / retrieval / bridge / dashboard / e2e groupings

Coverage is still thinner than ideal around:

- warning/diagnostic assertions for some fail-open branches
- broad matrix-style validation across the full suite in one run

### 2.1 Module-by-module audit summary

Reviewed against the current collection snapshot and the full-suite pass:

- `test_async_writer.py` / `test_backend.py`: focused, low leakage, intent matches runtime surface
- retrieval group (`test_bm25.py`, `test_search.py`, `test_fusion_rerank.py`, `test_wave3_retrieval.py`, `test_reranker*.py`, `test_entity_extraction.py`): strong behavior focus, good effective coverage
- graph group (`test_graph.py`, `test_graph_operations.py`, `test_graph_distil_failure.py`): strong black-box coverage around graph semantics and error handling
- runtime group (`test_checkpoint*.py`, `test_hooks.py`, `test_reflection.py`, `test_reflect.py`, `test_context.py`): good intent alignment; hook tests are cleaner now that checkpoint I/O is isolated
- `test_schema_module.py`: improved by replacing identity/size assertions with schema-contract assertions
- `test_runtime_import_hygiene.py`: improved from source-string scanning to real runtime recovery / compile-profile behavior regressions
- `test_lb.py`: intentionally low-level, but now focused on standalone-loading/fail-open behavior rather than source scanning
- store/data group (`test_store.py`, `test_core_data.py`, `test_store_module_split.py`): effective overall; module-split tests now focus on behavioral compatibility across split modules rather than object identity or file shape
- integration group (`test_bridge.py`, `test_dashboard.py`, `test_tool_handlers.py`, `test_e2e.py`, `test_host_contract_smoke.py`): valid and complementary to the unit suites
- `test_memory_curator.py`: now a much thinner legacy-compat regression suite that preserves wrapper and cold-store behavior without duplicating the canonical pipeline intent
- `test_curator_pipeline.py`: now the strongest intent-facing curator suite; major implementation-leak points from AST counting and private-side-channel reads were removed in this pass

### 3. Boundary and error-path detection

The suite is now better at detecting:

- stale import-path regressions
- schema/handler drift
- checkpoint side effects leaking into hook unit tests
- archive/delete partial-failure behavior
- report-generation fallback behavior

The current full-suite run is clean; no persistent warning noise remains in the verified state.

## Optimizations Applied In This Pass

### Test coverage additions

Added targeted assertions in `tests/test_curator_pipeline.py` for:

- body refinement in `build_cold_entry`
- `_curator_enabled` off-switch and unsupported-trigger warning
- `CompactChains` update-head failure reporting
- `ArchiveSuperseded` recent-access preservation
- report persistence to `curator-report.json`
- reflection-log append side effect for persisted reports
- `_run_curator` fallback reporting when `GenerateReport` fails

### Implementation-leak cleanup

Reduced direct implementation leakage by:

- replacing `result.__dict__` access with `getattr(...)`
- moving report-persistence verification to `_run_curator(...)` side effects instead of private helper-only checks
- removing AST-based source-structure tests that counted helper definitions rather than validating behavior
- replacing runtime import-path source scanning with recovery-runner / compile-profile execution tests
- replacing module split identity and line-count assertions with input/output compatibility checks across `core.store`, canonical core modules, and package exports
- aligning new/previously unclassified test modules with functional pytest markers in `tests/conftest.py`

### Test stability improvements

Updated `tests/test_memory_curator.py` so legacy curator tests use isolated temp-backed cold-store paths instead of fragile default locations. This removes false failures caused by writes to the default `~/.hermes/memory/cold_store.jsonl` path and avoids the Windows temp-dir cleanup issue that was surfacing through shared pytest temp roots.

### Documentation refresh

Updated `docs/testing/test-coverage.md` to reflect:

- new collection count (`510`)
- expanded `test_curator_pipeline.py` scope
- trimmed `test_memory_curator.py` legacy slice
- new `test_runtime_import_hygiene.py`
- updated `test_lb.py` / `test_schema_module.py` counts
- final cleanup of `test_store_module_split.py`, `test_runtime_import_hygiene.py`, and legacy curator overlap

## Validation

Focused regressions run in this pass:

1. `python -m pytest tests/test_curator_pipeline.py -q` -> `81 passed`
2. `python -m pytest tests/test_memory_curator.py -q` -> `27 passed`
3. `python -m pytest tests/test_lb.py tests/test_schema_module.py tests/test_curator_pipeline.py tests/test_memory_curator.py -q` -> `129 passed`
4. `python -m pytest tests/test_curator_pipeline.py tests/test_memory_curator.py tests/test_hooks.py tests/test_schema_module.py tests/test_runtime_import_hygiene.py tests/test_host_contract_smoke.py -q` -> `137 passed`
5. `python -m pytest tests/test_curator_pipeline.py tests/test_memory_curator.py tests/test_runtime_import_hygiene.py tests/test_store_module_split.py tests/test_schema_module.py tests/test_lb.py -q` -> `142 passed`
6. `python -m pytest tests --collect-only -q` -> `510 tests collected`
7. `python -m pytest tests -q` -> `510 passed`

## Residual Gaps

Still worth doing next:

1. add direct assertions for fail-open warnings where behavior matters operationally
2. keep watching for any future curator overlap as new legacy compatibility cases appear
