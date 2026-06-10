# v1.4 Development Progress

**Version**: v1.4  
**Started**: 2026-06-09  
**Status**: Completed  
**Last Updated**: 2026-06-09

## Scope

Current active implementation slice:

- Phase 1: Context Reliability
  - structured context bundle
  - backward-compatible string wrapper
  - `pre_llm_call` timeout and stable fallback
  - targeted tests
- Phase 2: Retrieval Quality and Explainability
  - configurable CJK tokenizer
  - explainable search score breakdown
  - `srh_memory_search` opt-in explain output
  - targeted tests
- Phase 3: Runtime Reliability
  - session-end checkpoint + pending recovery
  - graded context compression
  - corruption fail-open handling
  - typed config diagnostics
  - targeted tests
- Phase 4: Entity Recall and Backend Readiness
  - SQLite entity/entity_links index
  - query entity boost + explain hits
  - backend capability abstraction
  - targeted tests

## Live Status

| Item | Status | Notes |
|------|--------|-------|
| v1.4 planning report | Done | `docs/dev/1.4/feature-enhancement-plan.md` |
| Phase 1 SDD | Done | `docs/design/1.4/context-reliability-sdd.md` |
| ContextBundle design | Done | implemented in `memory/context.py` |
| Hook timeout/fallback | Done | implemented in `runtime/hooks.py` |
| Targeted tests | Done | context + hook coverage added |
| Verification run | Done | targeted pytest completed |
| Phase 2 SDD | Done | `docs/design/1.4/search-retrieval-enhancement-sdd.md` |
| CJK tokenizer modes | Done | implemented in `core/store.py` |
| Search explain path | Done | implemented in `core/search.py` |
| Tool explain flag | Done | implemented in `runtime/tools.py` |
| Retrieval tests | Done | BM25 + search coverage added |
| Phase 3 SDD | Done | `docs/design/1.4/runtime-reliability-sdd.md` |
| Runtime checkpoint | Done | implemented in `runtime/checkpoint.py` |
| Session-end pending recovery | Done | integrated in `runtime/hooks.py` |
| Graded context compression | Done | implemented in `memory/context.py` |
| Typed config diagnostics | Done | implemented in `core/config.py` |
| Runtime reliability tests | Done | checkpoint + hook + context coverage added |
| Entity recall layer | Done | implemented in `core/store.py` + `core/search.py` |
| Backend capability abstraction | Done | implemented in `core/backend.py` |
| Entity/backend tests | Done | search + backend coverage added |

## Work Log

### 2026-06-09

- Created versioned v1.4 development plan under `docs/dev/1.4/`.
- Created Phase 1 SDD under `docs/design/1.4/`.
- Confirmed current implementation baseline:
  - `memory/context.py` is string-only
  - `runtime/hooks.py::_pre_llm_call()` injects a single `context` string
  - existing tests target `build_context()` string output
- Chosen first implementation slice:
  - add internal structured context bundle
  - preserve public string contract
  - add timeout-safe hook behavior
- Implemented `ContextBundle` in `memory/context.py`.
- Added `build_context_bundle(...)` with:
  - stable section: pinned memories + always-active skills
  - dynamic section: relevant memories + triggered skills + compacted episode summaries
  - debug metadata for future diagnostics
- Preserved `build_context(...)` as a string-returning compatibility wrapper.
- Exported bundle helpers through package facades in `memory/__init__.py` and `__init__.py`.
- Implemented timeout-protected context assembly in `runtime/hooks.py`.
- Added stable-only fallback path when full context assembly times out or fails.
- Added targeted tests:
  - `tests/test_context.py`: bundle split + stable-only behavior
  - `tests/test_reflection.py`: hook timeout fallback path
- Verification results:
  - `pytest tests/test_context.py -q` -> 15 passed
  - `pytest tests/test_reflection.py -q` -> 23 passed
  - `pytest tests/test_host_contract_smoke.py -q` -> 1 passed
- Implemented retrieval-phase enhancement slice:
  - configurable CJK tokenization with `auto`, `bigram`, and `jieba` modes
  - `jieba.cut_for_search` optional path with fail-open fallback to bigram
  - `SearchIndex.search_explain()` for structured ranking diagnostics
  - `MemoryStore.fusion_search_explain()` store-facing compatibility wrapper
  - `srh_memory_search.explain=true` opt-in tool payload
- Added targeted retrieval tests:
  - `tests/test_bm25.py`: jieba-mode tokens + auto fallback coverage
  - `tests/test_search.py`: explain payload coverage
- Verification results:
  - `pytest tests/test_bm25.py -q` -> 17 passed
  - `pytest tests/test_search.py -q` -> 23 passed
  - `pytest tests/test_reflection.py -q` -> 23 passed
  - `pytest tests/test_context.py -q` -> 15 passed
  - `pytest tests/test_host_contract_smoke.py -q` -> 1 passed
- Implemented runtime-reliability slice:
  - `runtime/checkpoint.py` with atomic persistence, corrupt backup, and pending-stage recovery
  - `runtime/hooks.py` session-start recovery + session-end pending/completed stage markers
  - `memory/context.py` graded dynamic compression (`none/mild/aggressive/emergency`)
  - preserved stable context priority while degrading dynamic recall structurally
- Added targeted reliability tests:
  - `tests/test_checkpoint.py`: corrupt checkpoint backup + staged recovery
  - `tests/test_reflection.py`: session-start recovery plumbing + failed reflection pending marker
  - `tests/test_context.py`: compression-level debug + pinned survival under pressure
  - `tests/test_config.py`: invalid-type fallback + unknown-key diagnostics
- Verification results:
  - `python -m py_compile core/config.py memory/context.py runtime/checkpoint.py runtime/hooks.py` -> passed
  - `pytest tests/test_checkpoint.py -q` -> 2 passed
  - `pytest tests/test_reflection.py -q` -> 25 passed
  - `pytest tests/test_context.py -q` -> 17 passed
  - `pytest tests/test_config.py -q` -> 2 passed
  - `pytest tests/test_host_contract_smoke.py -q` -> 1 passed

## Current Implementation Outcome

- All scoped v1.4 phases are implemented and documented.
- Host-facing return contract remains `{"context": "..."}` for compatibility.
- Context stack now supports:
  - internal `ContextBundle` stable/dynamic split
  - timeout-protected `pre_llm_call` assembly with stable-only fallback
  - graded compression levels recorded in bundle debug metadata
- Retrieval stack now supports:
  - optional `jieba` search-mode tokenization with automatic fallback
  - opt-in explain payload for BM25, embedding, recency, effectiveness, supersedes, entity, and Hebbian signals
  - SQLite-backed `entities` / `entity_links` lifecycle on write, delete, and rebuild paths
- Runtime stack now supports:
  - checkpointed pending session-end work with best-effort recovery
  - corrupt-checkpoint fail-open backup behavior
  - typed config defaults plus diagnostics
- Backend abstraction now exposes current SQLite capabilities without changing default runtime behavior.

## Final Verification Snapshot

- `python -m py_compile core/backend.py core/config.py core/store.py core/search.py runtime/checkpoint.py runtime/hooks.py memory/context.py runtime/tools.py` -> passed
- `pytest tests/test_bm25.py -q` -> 12 passed
- `pytest tests/test_search.py -q` -> 19 passed
- `pytest tests/test_context.py -q` -> 17 passed
- `pytest tests/test_reflection.py -q` -> 25 passed
- `pytest tests/test_checkpoint.py tests/test_config.py tests/test_backend.py tests/test_host_contract_smoke.py -q` -> 7 passed
- `pytest tests --collect-only -q` -> 317 tests collected

## New Test Suite Classification

The v1.4-added or materially-extended tests are grouped below for targeted regression and acceptance runs.

### A. Context Reliability

Primary scope:

- `tests/test_context.py`
  - `test_context_bundle_splits_stable_and_dynamic_sections`
  - `test_context_bundle_stable_only_omits_dynamic_sections`
  - `test_bundle_records_compression_level_under_pressure`
  - `test_emergency_compression_keeps_pinned_stable_context`

- `tests/test_reflection.py`
  - `test_pre_llm_call_uses_stable_fallback_on_timeout`

Use when:

- validating `ContextBundle`
- validating timeout/fallback injection
- validating graded compression behavior

### B. Retrieval Quality And Explainability

Primary scope:

- `tests/test_bm25.py`
  - `test_jieba_search_mode_uses_search_tokens`
  - `test_auto_mode_falls_back_to_bigram_without_jieba`

- `tests/test_search.py`
  - `test_store_fusion_search_explain_exposes_score_components`

Use when:

- validating CJK tokenizer behavior
- validating explain payload shape and ranking diagnostics

### C. Runtime Reliability

Primary scope:

- `tests/test_checkpoint.py`
  - `test_corrupt_checkpoint_is_backed_up_and_defaults_returned`
  - `test_recover_pending_work_runs_available_stages_and_clears_them`

- `tests/test_reflection.py`
  - `test_on_session_start_runs_pending_recovery`
  - `test_on_session_end_marks_reflection_pending_when_reflection_fails`

- `tests/test_config.py`
  - `test_invalid_types_fall_back_to_defaults`
  - `test_unknown_keys_are_reported`

Use when:

- validating checkpoint persistence/recovery
- validating session-end pending markers
- validating typed config fallback and diagnostics

### D. Entity Recall And Backend Readiness

Primary scope:

- `tests/test_search.py`
  - `test_entity_links_are_indexed_and_deleted_without_orphans`
  - `test_entity_boost_and_hits_appear_in_explain`

- `tests/test_backend.py`
  - `test_sqlite_backend_capabilities_are_partial`
  - `test_fake_backend_can_report_full_capabilities`

Use when:

- validating entity index lifecycle
- validating entity boost/explain integration
- validating backend capability abstraction

### E. Contract Smoke

Primary scope:

- `tests/test_host_contract_smoke.py`

Use when:

- confirming host-facing compatibility after any 1.4 change

### Pytest Marker Labels

These labels are now registered in `tests/pytest.ini` and auto-assigned from `tests/conftest.py`:

- `v14_context`
- `v14_retrieval`
- `v14_runtime`
- `v14_entity`
- `v14_contract`

Cross-version functional labels are also available for the broader test suite:

- `store`
- `search`
- `retrieval`
- `graph`
- `reflection`
- `runtime`
- `context`
- `curator`
- `bridge`
- `dashboard`
- `tools`
- `backend`
- `config`
- `contract`
- `compaction`
- `reranker`
- `e2e`
- `integration`
- `smoke`
- `compatibility`
- `cjk`

Example selection commands:

- `pytest -m "v14_runtime"`
- `pytest -m "search and retrieval"`
- `pytest -m "contract or smoke"`

## Remaining Gaps

- No required v1.4 feature gap remains against `docs/dev/1.4/feature-enhancement-plan.md`.
- Some broader repo tests still have pre-existing Windows temp-dir and curator-environment issues outside the scoped v1.4 feature surfaces; they were not introduced by the 1.4 work and are not used as acceptance proof here.

## Risks Being Managed

- Avoid breaking current host contract.
- Keep runtime recovery fail-open when recovery prerequisites are missing.
- Keep tests focused on backward compatibility first.
