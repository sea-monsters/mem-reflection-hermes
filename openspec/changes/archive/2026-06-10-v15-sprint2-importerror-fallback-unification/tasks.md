## 1. Test Suite (RED Phase — Freeze Before Implementation)

- [x] 1.1 Create `tests/test_lb.py` covering `_lb` success, failure, caching, and dotted-name resolution.
- [x] 1.2 Add `tests/test_curator_pipeline.py` tests verifying curator modules use `_lb` and emit warnings on missing optional deps.
- [x] 1.3 Add AST-based test scanning `memory/curator/` for `except Exception: pass` / `except ImportError: pass` blocks (remove xfail from Sprint 1).
- [x] 1.4 Add standalone-loading test verifying `memory/curator/helpers.py` loads via `importlib` without raising.
- [x] 1.5 Run new tests and confirm they FAIL (RED phase) before writing implementation.

## 2. Late-Binding Helper

- [x] 2.1 Create `runtime/_lb.py` with `_lb(name)` function, `None` fallback, and module-level cache.
- [x] 2.2 Export `_lb` from `runtime/__init__.py` if a package init exists.

## 3. Curator Package Refactor

- [x] 3.1 Replace fallback blocks in `memory/curator/helpers.py` with `_lb("core.store")` and explicit `None` handling.
- [x] 3.2 Replace fallback blocks in `memory/curator/cold_store.py` with `_lb("core.store")` and explicit `None` handling.
- [x] 3.3 Replace fallback blocks in `memory/curator/actions.py` with `_lb("runtime.graph")` / `_lb("core.store")`.
- [x] 3.4 Replace fallback blocks in `memory/curator/report.py` with `_lb("reflection.runtime")`.
- [x] 3.5 Ensure every failure path logs `logger.warning` per CLAUDE.md convention.

## 4. Broader Audit and Cleanup

- [x] 4.1 Audit `reflection/*.py` for `except ImportError: pass` blocks and replace with `_lb()`.
- [x] 4.2 Audit `runtime/*.py` for `except ImportError: pass` blocks and replace with `_lb()`.
- [x] 4.3 Audit `core/*.py` for bare fallback blocks; if any, replace with narrow exception handling.

## 5. Verification

- [x] 5.1 Run `pytest tests/test_curator_pipeline.py -v` and confirm all tests pass including the formerly-xfail structural test.
- [x] 5.2 Run `pytest tests/ -q` and confirm full suite passes with zero regressions.
- [x] 5.3 Update Sprint 1 retrospective noting the xfail was resolved in Sprint 2.
