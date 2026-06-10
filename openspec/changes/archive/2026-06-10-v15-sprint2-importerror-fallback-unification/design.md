# Design: v1.5 Sprint 2 — ImportError Fallback Unification

## Context

`mem-reflection-hermes` supports two loading modes:
1. **Package mode**: `import mem_reflection_hermes.memory.curator` when installed as a package.
2. **Standalone mode**: `importlib.util.spec_from_file_location("memory.curator", "memory/curator/__init__.py")` used by tests and some runtime contexts.

To survive mode 2, modules contain defensive blocks like:
```python
try:
    from ...core.store import plugin_config
except Exception:
    cfg = {}
```
These blocks are duplicated (~15 instances), hide real import errors, and make static analysis unreliable. Sprint 1 left one test (`test_no_bare_except_pass`) as `xfail` because of them.

## Goals / Non-Goals

**Goals:**
- Centralize all cross-module late-bound imports into a single helper.
- Remove every `except Exception: pass` / `except ImportError: pass` used for import fallback.
- Preserve fail-open behavior for optional dependencies (return `None`, log `warning`).
- Make the Sprint 1 structural AC test pass without xfail.
- Ensure standalone test loading continues to work.

**Non-Goals:**
- No new features or behavior changes.
- No async conversion.
- No changes to public tool schemas or dashboard API.

## Decisions

### Decision 1: `runtime/_lb.py` as the canonical late-binding helper

Create `runtime/_lb.py` with `_lb(name: str) -> Optional[ModuleType]`:
- Accepts a dotted module name (`"core.store"`, `"mem_reflection_hermes.core.store"`, `"runtime.graph"`).
- Tries `importlib.import_module(name)`.
- On any import failure returns `None`.
- Caches successful lookups in a module-level dict.

**Rationale**: Single source of truth. Callers can choose to handle `None` explicitly rather than silently swallowing.

**Alternative considered**: A pure function without cache. Rejected because modules are loaded repeatedly in test loops; caching avoids repeated import cost.

### Decision 2: Leaf modules use a local `_resolve` shim

`core/store.py` is a leaf module (no project imports). It cannot import `runtime/_lb.py` without creating a cycle. Instead, it will expose a tiny `_resolve(name)` helper that mirrors `_lb()` semantics for its own consumers.

Wait — `core/store.py` should not need cross-module imports. The only fallback consumers are in `memory/curator/*`, `reflection/*`, and `runtime/*`. `core/store.py` can stay unchanged. `_lb()` lives in `runtime/_lb.py`, which is at the runtime layer and may import core modules safely.

**Rationale**: Avoid dependency inversion. Lower layers should not import higher layers.

### Decision 3: Replace bare `except Exception` with specific error handling

Every current `except Exception: pass/return None/continue` is replaced by one of:
- `_lb("module.name")` returning `None` + an explicit `if mod is None: return default`.
- A narrow `except (ImportError, ModuleNotFoundError)` if the fallback is truly unavoidable.
- `logger.warning(...)` for all failure paths that could indicate degraded functionality (per CLAUDE.md conventions).

**Rationale**: Explicit handling is observable and testable.

### Decision 4: Preserve test-loading compatibility via package namespace registration

Tests that use standalone importlib loading already register `mem_reflection_hermes` in `conftest.py` (line 116-125). For tests that load `memory.curator.helpers` directly without the package prefix, `_lb()` will still return `None` when the dotted name cannot be resolved, preserving the existing fail-open path.

**Rationale**: Do not change test infrastructure; change the implementation to be robust under the same infrastructure.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| A missing `logger.warning` call silently swallows a real bug | Add a static test that AST-scans for bare `except Exception: pass` |
| `_lb()` returns `None` for a required module and downstream code raises `AttributeError` | Add a runtime assertion or explicit error in callers where the dependency is required |
| Caching causes stale module object after module reload in long-lived tests | Accept; tests do not reload project modules |
| Standalone loading path changes break existing tests | Run full suite before and after refactor |

## Migration Plan

1. Add `runtime/_lb.py` and a minimal test file `tests/test_lb.py`.
2. Rewrite `memory/curator/actions.py`, `cold_store.py`, `helpers.py`, `report.py` to use `_lb()`.
3. Run `tests/test_curator_pipeline.py` and remove the `xfail` marker.
4. Audit `reflection/*`, `runtime/*`, `core/*` for remaining fallback blocks; replace or document.
5. Run full test suite (`pytest tests/ -q`) to confirm zero regressions.

## Open Questions

None — scope is narrow and requirements are clear from Sprint 1 residuals.
