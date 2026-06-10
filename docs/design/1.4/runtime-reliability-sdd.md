# v1.4 SDD: Runtime Reliability

**Version**: v1.4  
**Date**: 2026-06-09  
**Status**: Completed  
**Scope**: Phase 3 implementation for session-end checkpoint recovery, graded context compression, and typed config diagnostics

## 1. Purpose

This SDD defines the current runtime-reliability slice of v1.4 for `mem-reflection-hermes`.

The goal of this slice is to reduce session-end work loss and make context budgeting degrade by structure rather than by blunt tail truncation.

## 2. Problem

Two practical gaps remained after the first v1.4 slices:

1. Reflection, compaction, and curator work were only tracked in memory, so a failed or interrupted session end could silently drop pending work.
2. Context budgeting still behaved like coarse section dropping plus per-item truncation, without explicit compression levels for dynamic recall.
3. New v1.4 config knobs were still read as untyped nested dicts, so invalid values and unknown keys had no structured diagnostics.

## 3. Design Goals

- Persist pending session-end work with atomic checkpoint writes.
- Back up corrupt checkpoint files and fail open.
- Recover only low-risk pending work on session start.
- Keep pinned context stable while degrading dynamic context by level.
- Expose compression and recovery diagnostics through bundle/debug metadata.
- Provide typed defaults and diagnostics for v1.4 config surfaces while preserving `plugin_config()` compatibility.

## 4. Non-Goals

- No schema migration for checkpoint storage beyond the single JSON file.
- No LLM-based compression in this slice.
- No host protocol change beyond the existing string fallback contract.

## 5. Proposed Design

### 5.1 Runtime Checkpoint

Add `runtime/checkpoint.py` with:

- `runtime-checkpoint.json` under `plugin_data_dir()`
- atomic temp-file write + replace
- corrupt-file backup using `runtime-checkpoint.corrupt-<timestamp>.json`
- split buckets:
  - `session_states`
  - `pending_reflections`
  - `pending_compactions`
  - `pending_curator_runs`
  - `last_completed`

### 5.2 Lifecycle Integration

`runtime/hooks.py` now:

- snapshots lightweight session state before session-end work
- marks reflection pending before execution
- marks compaction and curator pending before each stage
- clears pending state only after successful completion
- attempts best-effort recovery on session start

Recovery policy:

- reflection: rerun only when a transcript snapshot exists
- compaction: rerun directly
- curator: rerun directly
- unrecoverable pending reflection without transcript: log diagnostic and clear

### 5.3 Graded Context Compression

`memory/context.py` now uses dynamic compression levels:

- `none`
- `mild`
- `aggressive`
- `emergency`

The builder preserves stable sections first, then tries to fit dynamic sections by:

- shortening per-memory previews
- shortening skill descriptions
- reducing episode-summary detail
- dropping lower-priority dynamic sections only after structured degradation

The chosen level is recorded in `ContextBundle.debug["compression_level"]`.

### 5.4 Typed Config Diagnostics

Add `core/config.py` with dataclass-based config models for:

- `search`
- `context`
- `checkpoint`

and a diagnostics helper:

```python
get_config_diagnostics()
```

Behavior:

- invalid types fall back to defaults
- unknown keys are reported in diagnostics
- callers can adopt typed access gradually without removing `plugin_config()`

## 6. Files Affected

- `runtime/checkpoint.py`
- `runtime/hooks.py`
- `memory/context.py`
- `core/config.py`
- `core/__init__.py`
- `tests/test_checkpoint.py`
- `tests/test_reflection.py`
- `tests/test_context.py`
- `tests/test_config.py`
- `docs/dev/1.4/DEVELOPMENT_PROGRESS.md`

## 7. Acceptance Criteria

- Reflection failure leaves a pending checkpoint entry instead of losing work silently.
- Session start can recover or report pending session-end work.
- Corrupt checkpoint JSON is backed up and does not crash runtime loading.
- Context compression records a level and does not rely on whole-string tail cutting.
- Pinned context remains available under tight budgets.
- Config diagnostics report invalid types and unknown keys without crashing.

## 8. Progress Notes

- 2026-06-09: SDD created.
- 2026-06-09: Added `runtime/checkpoint.py` with atomic writes, corrupt backup, pending buckets, and recovery helpers.
- 2026-06-09: Wired checkpoint pending/completion markers into `runtime/hooks.py`.
- 2026-06-09: Added session-start pending recovery path.
- 2026-06-09: Reworked dynamic context assembly to use structured compression levels.
- 2026-06-09: Added `core/config.py` typed config defaults plus diagnostics.
- 2026-06-09: Added targeted checkpoint, hook, compression, and config tests.
- 2026-06-09: Scope completed and aligned with the v1.4 progress ledger.
