# Proposal: v1.5 Sprint 2 — ImportError Fallback Unification

## Why

The `mem-reflection-hermes` codebase uses standalone import paths (`importlib.util.spec_from_file_location`) for tests and production, leading to defensive `try/except ImportError: pass` fallback blocks scattered across modules. These blocks complicate testing, swallow real errors, and make the dependency graph unclear. Sprint 2 consolidates all fallback behavior into a single `runtime/_lb.py` late-binding helper and removes duplicated fallback code.

## What Changes

- Introduce `runtime/_lb.py` as the single late-binding helper (`_lb(name)`) for all project imports.
- Replace every `try/except ImportError: pass` fallback in `memory/curator/*`, `core/*`, `reflection/*`, and `runtime/*` with calls to `_lb()`.
- Remove remaining `except Exception: pass` blocks from the curator package (resolving Sprint 1 xfail).
- Provide a `_resolve(name)` helper in `core/store.py` for leaf modules that cannot import `runtime/_lb.py`.
- Update test files that rely on standalone loading to register the package namespace before module exec.
- No public API changes; all existing imports remain valid.

## Capabilities

### New Capabilities
- `late-binding-imports`: A unified late-binding helper that resolves project modules safely whether running as a package or standalone.

### Modified Capabilities
- `curator-actions`: Remove silent fallback blocks and centralize import error handling.

## Impact

- Affected files: `memory/curator/actions.py`, `memory/curator/cold_store.py`, `memory/curator/helpers.py`, `memory/curator/report.py`, `runtime/_lb.py` (new), `core/store.py`, `runtime/graph.py`, and standalone test loaders.
- Test impact: `tests/test_curator_pipeline.py` xfail on `except Exception: pass` will be resolved.
- Risk: Low — all 490 existing tests must continue to pass after fallback consolidation.
