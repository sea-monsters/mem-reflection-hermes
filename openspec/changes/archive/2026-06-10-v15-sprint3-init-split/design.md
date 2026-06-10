## Context

`__init__.py` currently owns three unrelated responsibilities:
1. Package exports and singleton wiring (`register()`, `_get_mem_store()`, etc.)
2. Helper functions used by `_lb()` calls in `runtime/tools.py` and `runtime/hooks.py`
3. Twelve tool schema dicts totaling ~120 lines of JSON-like data

The schema dicts are pure data and have no dependency on runtime state. They are the ideal first extraction target because they reduce file size without changing any runtime behavior or `_lb` contracts.

## Goals / Non-Goals

**Goals:**
- Create `runtime/schemas.py` containing all 12 `_SRH_*_SCHEMA` dicts.
- Import those schemas into `__init__.py` so `register()` continues to use them unchanged.
- Reduce `__init__.py` to under 300 lines.
- Ensure `_lb("_SRH_MEMORY_WRITE_SCHEMA")` and similar late-bound lookups still resolve.
- Preserve all external import contracts (`from mem_reflection_hermes import register`).

**Non-Goals:**
- No changes to schema content, tool names, descriptions, or parameter shapes.
- No changes to `register()` logic, Hermes Agent contract, or dashboard behavior.
- No new dependencies.

## Decisions

- **Decision: Keep schemas as module-level dicts in `runtime/schemas.py`.**
  - Rationale: This matches the current representation, requires no serialization changes, and keeps `register()` a simple import-and-iterate loop.
- **Decision: Re-export schemas in `__init__.py` via explicit `from .runtime.schemas import ...`.**
  - Rationale: Hermes Agent registers tools by importing the package and calling `register(ctx)`. The schemas must remain accessible from the package namespace. Explicit imports are clearer than wildcard exports and align with the existing `__all__` discipline.
- **Decision: Do not move `_lb`-bound helpers (`match_skills`, `load_zone_summary`, `save_zone_summary`).**
  - Rationale: These helpers are thin wrappers around `core.search` / `core.store` singletons and are part of the runtime binding contract used by `runtime/tools.py`. Moving them would break existing `_lb("match_skills")` callers and add Sprint 3 scope creep.

## Risks / Trade-offs

- **[Risk] External plugins or scripts importing schemas directly from `mem_reflection_hermes._SRH_MEMORY_WRITE_SCHEMA`.**
  - Mitigation: The new `__init__.py` still re-exports all `_SRH_*_SCHEMA` names, so direct attribute access on the package object remains valid.
- **[Risk] Standalone loading of `__init__.py` via importlib fails because of the new relative import to `runtime.schemas`.**
  - Mitigation: `runtime/schemas.py` is a leaf module with no project imports. The conftest already registers `mem_reflection_hermes` as a package before loading submodules.
