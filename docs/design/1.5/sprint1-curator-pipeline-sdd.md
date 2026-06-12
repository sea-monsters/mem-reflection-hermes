# v1.5 SDD: Curator Action Pipeline

**Version**: v1.5
**Date**: 2026-06-10
**Status**: Draft
**Scope**: Sprint 1 — refactor `memory/curator.py` (1,121 lines) into composable Action pipeline
**Trigger**: L6-01 from v1.4 six-layer review

## 1. Purpose

Decompose the monolithic `memory/curator.py` into a directory package with composable actions, shared helpers, and unified error handling. No behavior change — all 6 curator phases produce identical results after refactor.

## 2. Problem

`memory/curator.py` has accumulated technical debt:

| Metric | Count |
|--------|-------|
| Lines | 1,121 |
| `archive+delete` copy-paste blocks | 5 (lines ~448, ~572, ~690, ~858, ~908) |
| `pinned/keep` guard checks | 4 (identical 4-line blocks) |
| `_load_effectiveness` calls | 3 (identical try/except) |
| Cold store entry construction | 5 (similar dict builders) |
| `except Exception` blocks | 19 (8 silent `pass`, 7 `logger.warning`, 4 `continue`) |

A single God file makes it hard to add new curation phases, test phases independently, or reason about error propagation.

## 3. Design Goals

- Each curation phase becomes an independent `CuratorAction` class with `execute(ctx) -> CuratorResult`.
- Shared patterns (pinned guard, cold store write+delete, effectiveness loading) become single helper functions.
- Error handling is uniform: each action catches its own exceptions, logs with `logger.warning`, and returns error counts.
- Public API (`_run_curator`, `scan_for_stale`, `merge_similar`, `clean_orphan_edges`, etc.) is re-exported from the package so external callers are unaffected.
- All 413 existing tests pass without modification.

## 4. Non-Goals

- No new curation phases or behavior changes.
- No async/await conversion (curator runs synchronously in `on_session_end`).
- No plugin-level config schema changes.
- No changes to cold storage file format.

## 5. Proposed Design

### 5.1 Package Structure

```
memory/curator/
    __init__.py      — _run_curator() entry + public API re-exports
    actions.py       — CuratorAction base + 6 concrete actions
    cold_store.py    — cold store read/write/prune/restore (extracted from curator.py)
    helpers.py       — is_protected(), archive_and_delete(), load_last_access(), build_cold_entry()
    report.py        — generate_report()
```

### 5.2 Core Types

```python
# actions.py

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class CuratorContext:
    """Input context shared by all actions in a pipeline run."""
    mem_store: Any  # MemoryStore instance
    errors: List[str] = field(default_factory=list)

@dataclass
class CuratorResult:
    """Output from a single curator action."""
    action_name: str
    archived: int = 0
    compacted: int = 0
    merged: int = 0
    similar_pairs: int = 0
    orphan_edges: int = 0
    errors: List[str] = field(default_factory=list)

class CuratorAction:
    """Base class for a single curation phase."""
    name: str = ""

    def should_run(self, ctx: CuratorContext) -> bool:
        return True

    def execute(self, ctx: CuratorContext) -> CuratorResult:
        raise NotImplementedError
```

### 5.3 Concrete Actions

| Action | Source function | Responsibility |
|--------|----------------|----------------|
| `ArchiveStale` | `scan_for_stale` + `archive_expired` | TTL/staleness detection + cold archive |
| `CompactChains` | `compact_superseded_chains` | Compress long supersedes chains |
| `ArchiveSuperseded` | `archive_superseded` | Deep chain archiving (depth >= 3) |
| `MergeSimilar` | `scan_for_similar` + `merge_similar` | Detect + merge near-duplicates |
| `CleanOrphanEdges` | `clean_orphan_edges` | Remove dangling graph edges |
| `GenerateReport` | `generate_report` | Text summary for reflection log |

### 5.4 Shared Helpers

```python
# helpers.py

def is_protected(fm) -> bool:
    """True if memory is pinned or has keep/permanent tags."""
    if fm.pinned:
        return True
    return bool(fm.tags and any(t in ("keep", "permanent") for t in fm.tags))

def build_cold_entry(mem, context_tag: str, **extra) -> dict:
    """Construct standard cold store entry dict."""

def archive_and_delete(mem_store, mem, entry: dict, context: str) -> tuple[bool, Optional[str]]:
    """Cold-store-write + active-delete with unified error handling.
    Returns (success, error_msg)."""

def load_last_access(mem_store, mid: str) -> float:
    """Load last_accessed timestamp, return 0 on failure."""
```

### 5.5 Pipeline Orchestration

```python
# __init__.py

ACTIONS = [
    ArchiveStale,
    CompactChains,
    ArchiveSuperseded,
    MergeSimilar,
    CleanOrphanEdges,
]

def _run_curator(ctx, mem_store) -> Dict[str, Any]:
    pipeline_ctx = CuratorContext(mem_store=mem_store)
    results = []
    for action_cls in ACTIONS:
        action = action_cls()
        if not action.should_run(pipeline_ctx):
            continue
        try:
            r = action.execute(pipeline_ctx)
            results.append(r)
            pipeline_ctx.errors.extend(r.errors)
        except Exception as e:
            pipeline_ctx.errors.append(f"{action.name}: {e}")
            logger.warning("Curator action %s failed: %s", action.name, e)
    # Aggregate + report
    report_action = GenerateReport()
    report = report_action.execute(pipeline_ctx, results)
    return _aggregate_results(results, report)
```

### 5.6 Backward Compatibility

`__init__.py` re-exports all public names:

```python
from .actions import ArchiveStale, CompactChains, ArchiveSuperseded, MergeSimilar, CleanOrphanEdges
from .cold_store import _load_cold_store, _append_to_cold_store, _restore_from_cold
from .helpers import is_protected, build_cold_entry, archive_and_delete, load_last_access
from .report import generate_report
```

External callers (`runtime/hooks.py`, `tests/test_memory_curator.py`) can continue importing from `memory.curator` unchanged.

The old `memory/curator.py` file becomes a thin forwarder:

```python
# memory/curator.py (after refactor)
"""Backward-compat forwarder — all logic moved to memory/curator/."""
from .curator import *  # noqa: F401,F403
```

Wait — there is a naming conflict: `memory/curator.py` file vs `memory/curator/` directory. Python cannot have both. We must **delete** `memory/curator.py` and create `memory/curator/` in its place. All imports using `from memory.curator import X` will resolve to the package automatically.

## 6. Files Affected

| File | Action |
|------|--------|
| `memory/curator.py` | Delete, replace with `memory/curator/` package |
| `memory/curator/__init__.py` | New — pipeline orchestration + re-exports |
| `memory/curator/actions.py` | New — CuratorAction base + 6 actions |
| `memory/curator/cold_store.py` | New — extracted cold store I/O |
| `memory/curator/helpers.py` | New — shared helpers |
| `memory/curator/report.py` | New — report generation |
| `tests/test_curator_pipeline.py` | New — action-level tests |
| `CLAUDE.md` | Update curator description to reflect package structure |

Existing test files (`tests/test_memory_curator.py`) should require no changes because imports remain `from memory.curator import X`.

## 7. Acceptance Criteria

1. `memory/curator.py` is replaced by `memory/curator/` package directory.
2. All 6 curator phases produce identical observable behavior to v1.4.
3. `_run_curator(ctx, mem_store)` return dict has same keys and semantics.
4. `archive+delete` pattern appears exactly once (in `archive_and_delete()`).
5. `pinned/keep` guard appears exactly once (in `is_protected()`).
6. `_load_effectiveness` wrapper appears exactly once (in `load_last_access()`).
7. No `except Exception: pass` blocks remain — all use `logger.warning`.
8. `from memory.curator import _run_curator, scan_for_stale, merge_similar, clean_orphan_edges` still works.
9. All 413+ existing tests pass without modification.
10. New test file `tests/test_curator_pipeline.py` covers:
    - Each action independently (6 actions × 2-3 tests = 12-18 tests)
    - Pipeline ordering (compact runs before archive)
    - Error isolation (one action failure does not skip subsequent actions)
    - Helper edge cases (empty store, all-pinned, cold store full)
