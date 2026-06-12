## Why

`__init__.py` is currently 618 lines and mixes package initialization, singleton getters, helper functions, and 12 inline tool schema dicts. This violates the functional-package boundary established by CLAUDE.md: the entry point should only import, wire singletons, and expose `register()`. Extracting schema data into a dedicated `runtime/schemas.py` module reduces `__init__.py` to ~250 lines and makes the registration flow readable without changing any tool surface.

## What Changes

- Create `runtime/schemas.py` and move all 12 `_SRH_*_SCHEMA` dicts from `__init__.py` into it.
- Update `__init__.py` to import the schema dicts from `runtime/schemas.py`.
- Keep `_lb`-bound helpers (`match_skills`, `load_zone_summary`, `save_zone_summary`) and singleton getters in `__init__.py`.
- Keep `register(ctx)` unchanged in both logic and Hermes Agent contract.
- No schema content changes; only location changes.

## Capabilities

### New Capabilities

- `tool-schema-module`: Dedicated `runtime/schemas.py` that owns all registered tool schemas and is importable from the package root.

### Modified Capabilities

- *(none — this is a pure relocation with no spec-level behavior changes)*

## Impact

- `runtime/schemas.py`: new file (~130 lines).
- `__init__.py`: reduced from ~618 to ~250 lines.
- All callers of `from mem_reflection_hermes import register`, `_lb("_SRH_MEMORY_WRITE_SCHEMA")`, and similar paths remain functional.
- No changes to Hermes Agent integration, tests, or dashboard.
