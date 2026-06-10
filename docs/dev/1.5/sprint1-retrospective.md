# Sprint 1 Retrospective — Curator Action Pipeline

**Date**: 2026-06-10
**Status**: Completed
**Tests**: 446 passed, 0 failed (413 legacy + 33 new)

## What was delivered

- `memory/curator.py` (1,124 deletions) replaced by `memory/curator/` package:
  - `helpers.py` — single `is_protected`, `archive_and_delete`, `load_last_access`, `build_cold_entry`
  - `cold_store.py` — JSONL I/O, prune, restore
  - `actions.py` — `CuratorAction` base + 6 concrete actions
  - `report.py` — text report + persistence
  - `__init__.py` — pipeline orchestration + backward-compat wrappers
- `archive+delete` duplicate reduced from 5 blocks to 1
- `pinned/keep` guard reduced from 4 blocks to 1
- Legacy public API preserved: `scan_for_stale`, `archive_expired`, `archive_superseded`, `compact_superseded_chains`, `scan_for_similar`, `merge_similar`, `clean_orphan_edges`

## Test-first methodology adherence

The Sprint followed the Spec → Test → Code workflow documented in `CLAUDE.md`.

### Tests written before implementation

- `tests/test_curator_pipeline.py` (33 tests) was written before the refactor and initially failed as expected (RED phase).
- Coverage included: data structures, helpers, all 6 actions, pipeline ordering, error isolation, backward compatibility.

### Test modifications during implementation

Two test changes were required after implementation started. Both are recorded here for process learning:

1. **Cross-platform invalid path in cold-store failure tests**
   - Original test: `_cold_store_path_override = "/nonexistent/path/_cold.jsonl"`
   - Problem: On Windows this path is interpreted relative to the current drive and `mkdir` succeeds, so the failure simulation did not trigger.
   - Fix: Replaced with `"/nonexistent\x00path/_cold.jsonl"` (null byte is invalid on all platforms).
   - Lesson: Platform portability must be considered during test construction, not after. Future cold-store / I/O tests should use `pathlib` + a guaranteed-invalid character or a read-only directory fixture.

2. **Dashboard test hard-coded old `curator.py` path**
   - `tests/test_dashboard.py` used `importlib.util.spec_from_file_location(..., "memory/curator.py")` to mock the curator module for dashboard tests.
   - Problem: The file was deleted as part of the refactor, so the mock failed with `FileNotFoundError`.
   - Fix: Updated path to `"memory/curator/__init__.py"`.
   - Lesson: File-path references in tests are structural coupling. When a module is promoted to a package, these references must be updated. They are not "changing assertions to fit implementation" — they are keeping the test aligned with the filesystem reality. Such changes should be planned in the SDD's "Files Affected" table and executed immediately after the structural move.

## What to keep doing

- Write the SDD first; it caught the backward-compat requirement early.
- Write action-level tests independently from legacy integration tests; it surfaced the dashboard mock coupling.
- Run the full suite after every significant step (not just the new tests).

## What to improve

- **Platform fixtures**: Add a shared fixture for "guaranteed-invalid path" to `conftest.py` so future I/O-failure tests do not hard-code Unix assumptions.
- **Path-audit in SDD**: Add a checklist item to search for hard-coded `.py` paths in tests before deleting a module.
- **Stricter freeze rule**: Once tests are declared frozen, only two modification categories are allowed:
  1. Fixing test bugs unrelated to implementation correctness (platform portability, typos).
  2. Updating structural file references when a module moves.
  Both must be documented in the commit message.

## Follow-up: Sprint 2 resolution

The Sprint 1 structural test `test_no_bare_except_pass` was deliberately marked `xfail` because the curator package still contained `try/except ImportError: pass` fallback blocks used for standalone module loading. Sprint 2 (`v15-sprint2-importerror-fallback-unification`) replaced those blocks with a shared `_lb()` late-binding helper in `runtime/_lb.py`. The `xfail` was removed in `tests/test_curator_pipeline.py`, and the test now passes. The Sprint 1 curator pipeline refactor is therefore fully closed.

## Metrics

| Metric | Before | After |
|--------|--------|-------|
| `memory/curator.py` | 1,121 lines | deleted |
| `memory/curator/` package | — | 5 files, ~780 lines |
| `archive+delete` duplicates | 5 | 1 |
| `pinned/keep` duplicates | 4 | 1 |
| `_load_effectiveness` duplicates | 3 | 1 |
| Tests | 413 | 446 (+33) |
