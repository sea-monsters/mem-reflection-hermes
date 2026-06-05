# v1.0-beta3 Code Review — Round 1

**Date**: 2026-06-05  
**Scope**: beta3 cleanup runtime after retiring pre-beta2 implementations.  
**Review lens**: functional intent, implementation logic, and boundary handling.

---

## Executive Summary

The beta3 cleanup succeeds at moving the active runtime to the canonical modules:

- `store.py`
- `search.py`
- `graph.py`
- `reflect.py`
- `runtime_tools.py`
- `runtime_hooks.py`
- `runtime_graph.py`
- `runtime_reflection.py`
- `dashboard/plugin_api.py`

The host-facing contract remains intact: 17 tools, 4 hooks, and 8 slash commands. Four explicit old-path compatibility entrypoints remain by design:

- `tools/handlers.py`
- `hooks/lifecycle.py`
- `graph/compat.py`
- `reflection/engine.py`

Round 1 found four real issues. All four were fixed in this pass.

---

## Findings And Fixes

### B3-R1-1 — Palace/search derived views were not invalidated after store mutations

**Severity**: High  
**Files**: `store.py`, `__init__.py`, `search.py`

**Functional intent**: Memory writes, updates, deletes, and reorder operations should immediately affect search results and Memory Palace context.

**Problem**: `__init__.py` still uses package-level palace index state (`_index_dirty`, `_cached_index`, `_last_index_hash`) when building the context block. The beta3 `MemoryStore` did not initialize these fields, and `put()`, `update()`, `delete()`, and `reorder()` did not consistently invalidate derived views. In the worst case, pre-LLM context building could degrade to an empty context via the broad context safety wrapper; in normal mutation flows, search cache and palace index could stay stale.

**Fix**:

- Added `_index_dirty`, `_cached_index`, and `_last_index_hash` to `MemoryStore`.
- Added `_mark_changed()` to invalidate search cache and mark palace index dirty.
- Called `_mark_changed()` after successful `put()`, `update()`, `delete()`, and `reorder()`.

**Validation**:

- `python -m py_compile store.py search.py __init__.py`
- `python -m pytest tests\test_store.py tests\test_search.py tests\test_dashboard.py tests\test_host_contract_smoke.py -q`

### B3-R1-2 — Dashboard delete closed the shared runtime graph SQLite connection

**Severity**: High  
**Files**: `dashboard/plugin_api.py`, `graph.py`

**Functional intent**: Deleting a memory from the dashboard should remove related graph rows without breaking later graph operations.

**Problem**: `dashboard/plugin_api.py` retrieved the runtime graph connection through `gm.store._connect()` and then closed it in `finally`. That connection belongs to the thread-local `GraphIndex` singleton. Closing it without clearing the singleton's thread-local reference can cause later graph operations to reuse a closed SQLite connection.

**Fix**:

- Removed the dashboard-side close of the shared graph connection.
- Hardened `GraphIndex._get_conn()` to detect a closed thread-local connection and recreate it.

**Validation**:

- `python -m py_compile graph.py dashboard\plugin_api.py`
- `python -m pytest tests\test_graph.py tests\test_dashboard.py -q`

### B3-R1-3 — Zone-scoped search filtered too late

**Severity**: Medium  
**Files**: `search.py`

**Functional intent**: `SearchIndex.search(zone=...)` should search within the requested zone, not search globally and filter after top-k.

**Problem**: Zone filtering happened after global recall, fusion, MMR, and top-k truncation. A relevant memory in the requested zone could be excluded because unrelated global results occupied the candidate pool.

**Fix**:

- Normalized the zone at cache-key construction.
- Built the active candidate pool with `store.list(zone=..., active_only=...)` before recall/rerank.
- Removed the late zone filter.

**Validation**:

- Manual zone-search smoke: a work-zone-only match is returned for `search(..., zone="work")`.
- `python -m pytest tests\test_search.py tests\test_fusion_rerank.py -q`

### B3-R1-4 — Version metadata still pointed at beta2

**Severity**: Medium  
**Files**: `plugin.yaml`, `README.md`, `CLAUDE.md`, `docs/*`, `scripts/check_v092.py`

**Functional intent**: Beta3 cleanup should be externally visible as `1.0-beta3`, while historical beta2 documents remain clearly historical.

**Problem**: Current entry documents and the runtime verification script still described the active version as `1.0-beta2`.

**Fix**:

- Bumped `plugin.yaml` to `1.0-beta3`.
- Updated current entry docs to `v1.0-beta3`.
- Updated `scripts/check_v092.py` version assertion and heading to `v1.0-beta3`.
- Added the beta3 changelog entry.

**Validation**:

- `python scripts\check_v092.py`
- Version scan confirms current entry docs use beta3; historical beta2 research/review documents remain as version history.

---

## Residual Risks

- The repository still intentionally keeps four deprecated compatibility entrypoints for explicit old import paths. Removing them requires a separate external contract decision.
- Historical review, research, and changelog documents still contain beta2 and pre-beta2 names by design. They are version evidence, not current runtime documentation.
- The test directory has pre-existing historical diffs from earlier migration work. This review did not modify tests.

---

## Validation Summary

Final validation for Round 1:

- `python -m py_compile __init__.py runtime_tools.py runtime_hooks.py runtime_graph.py runtime_reflection.py store.py search.py graph.py reflect.py context.py dashboard\plugin_api.py tools\handlers.py hooks\lifecycle.py graph\compat.py reflection\engine.py scripts\check_issues.py scripts\check_v092.py scripts\smoke_host_contract.py`
- `python scripts\smoke_host_contract.py` -> 37 passed, 0 failed
- `python scripts\check_v092.py` -> 7 passed, 0 failed
- `python scripts\check_issues.py` -> Total issues found: 0
- `python -m pytest tests -q` -> 215 passed, 1 warning
