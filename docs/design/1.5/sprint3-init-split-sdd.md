# v1.5 SDD: __init__.py Split

**Version**: v1.5
**Date**: 2026-06-10
**Status**: Draft
**Scope**: Sprint 3 — extract tool schemas from `__init__.py` into dedicated module
**Trigger**: L6-04 from v1.4 six-layer review

## 1. Purpose

Reduce `__init__.py` from 618 lines to ~250 by extracting tool schema definitions into a dedicated `runtime/schemas.py` module. The package init should contain only imports, singleton getters, and the `register()` function.

## 2. Problem

`__init__.py` currently contains:
- ~150 lines of imports
- ~60 lines of singleton getters
- **~120 lines of inline tool schema dicts** (12 tools × ~10 lines each)
- ~100 lines of `register()` logic
- ~40 lines of helper functions (`match_skills`, `load_zone_summary`, `save_zone_summary`)
- ~50 lines of `__all__` + aliases

The schema definitions are data, not initialization logic. They clutter the package entry point and make it hard to see the actual registration flow.

## 3. Design Goals

- All `_SRH_*_SCHEMA` dicts move to `runtime/schemas.py`.
- `__init__.py` imports schemas from `runtime/schemas.py`.
- `_lb` late-binding targets (`match_skills`, `load_zone_summary`, etc.) remain in `__init__.py`.
- `register()` function stays in `__init__.py` (it needs access to package singletons).
- All external imports and `_lb` bindings continue to work.

## 4. Non-Goals

- No changes to `register()` logic or tool surface.
- No changes to how Hermes Agent calls `register(ctx)`.
- No new Python packages or namespace changes.

## 5. Proposed Design

### 5.1 New File: `runtime/schemas.py`

```python
"""Tool schema definitions for mem-reflection-hermes."""

_SRH_MEMORY_WRITE_SCHEMA = { ... }
_SRH_MEMORY_SEARCH_SCHEMA = { ... }
_SRH_MEMORY_DELETE_SCHEMA = { ... }
_SRH_PALACE_NAVIGATE_SCHEMA = { ... }
_SRH_REFLECT_NOW_SCHEMA = { ... }
_SRH_SKILL_QUERY_SCHEMA = { ... }
_SRH_COMPILE_PROFILE_SCHEMA = { ... }
_SRH_ASSOCIATE_SCHEMA = { ... }
_SRH_GRAPH_RETRIEVE_SCHEMA = { ... }
_SRH_GRAPH_STATS_SCHEMA = { ... }
_SRH_GRAPH_VIZ_SCHEMA = { ... }
_SRH_MEMORY_HEALTH_SCHEMA = { ... }
```

### 5.2 Updated `__init__.py`

```python
from .runtime.schemas import (
    _SRH_MEMORY_WRITE_SCHEMA, _SRH_MEMORY_SEARCH_SCHEMA, ...
)
```

### 5.3 What Stays

- `match_skills()` — used by `_lb("match_skills")`
- `load_zone_summary()` / `save_zone_summary()` — used by `_lb("load_zone_summary")`
- `_get_mem_store()` / `_get_skill_store()` / `_get_search_index()` — singleton getters
- `register(ctx)` — Hermes Agent entry point
- `__all__` — public API

## 6. Files Affected

| File | Action |
|------|--------|
| `runtime/schemas.py` | New — 12 tool schema dicts (~130 lines) |
| `__init__.py` | Slim down to ~250 lines |

## 7. Acceptance Criteria

1. `__init__.py` is under 300 lines.
2. All 12 tool schemas are in `runtime/schemas.py`.
3. `from mem_reflection_hermes import register` still works.
4. `_lb("_SRH_MEMORY_WRITE_SCHEMA")` still resolves.
5. All 413+ tests pass.
