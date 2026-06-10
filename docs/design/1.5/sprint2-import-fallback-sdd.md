# v1.5 SDD: ImportError Fallback Unification

**Version**: v1.5
**Date**: 2026-06-10
**Status**: Draft
**Scope**: Sprint 2 — eliminate or unify `importlib.util.spec_from_file_location` fallback blocks across 10 production files
**Trigger**: L6-03 from v1.4 six-layer review

## 1. Purpose

Remove ~250 lines of duplicated `importlib` fallback code from production modules. Tests should use `pythonpath` configuration instead of forcing production code to support two import paths.

## 2. Problem

10 production files contain try/except ImportError blocks with `importlib.util.spec_from_file_location` fallbacks. These exist because tests load modules in isolation without package context. The result:

- ~250 lines of dead code in production paths
- 5 distinct pattern variations across files
- Every new module must include the same boilerplate
- Confusing for contributors — which import path actually runs?

## 3. Design Goals

- Production code uses only standard relative imports (`from ..core.store import X`).
- Test infrastructure handles import isolation, not production code.
- Optional dependencies (jieba, spaCy, tiktoken) keep their try/except — these are legitimate.
- All 413+ tests pass with only `conftest.py` and `pytest.ini` changes.

## 4. Non-Goals

- No changes to optional dependency handling (jieba/spaCy/tiktoken).
- No test file rewrites beyond import setup in `conftest.py`.
- No package restructuring — that is Sprint 4 (L6-05).

## 5. Proposed Design

### 5.1 Root Cause

Tests use `importlib.util.spec_from_file_location` to load individual `.py` files without package context. This makes relative imports (`from ..core.store import X`) fail, triggering the fallback path.

### 5.2 Fix: pytest.ini pythonpath + conftest.py registration

```ini
# tests/pytest.ini
[pytest]
pythonpath = ..
```

This makes `mem_reflection_hermes` importable as a package from the test directory. Combined with the existing `conftest.py` that registers the package in `sys.modules`, all relative imports resolve correctly.

### 5.3 Affected Files and Changes

For each file, remove the `except ImportError` fallback block and keep only the relative import:

| File | Fallback lines | Change |
|------|---------------|--------|
| `core/search.py` | ~15 | Remove fallback, keep `from .store import ...` |
| `core/graph.py` | ~10 | Remove fallback, keep `from .store import ...` |
| `memory/context.py` | ~25 | Remove fallback, keep relative imports |
| `reflection/engine.py` | ~20 | Remove fallback, keep relative imports |
| `runtime/graph.py` | ~15 | Remove fallback, keep relative imports |
| `web/api.py` | ~30 | Remove fallback, keep package import |
| `memory/bridge.py` | ~12 | Remove fallback, keep relative imports |
| `memory/curator.py` (now `curator/`) | ~20 | Remove fallback, keep relative imports |
| `runtime/hooks.py` | ~10 | Remove fallback, keep package import |
| `runtime/tools.py` | ~10 | Remove fallback, keep package import |

### 5.4 Test Loading Pattern Update

Test files currently do:

```python
_spec = importlib.util.spec_from_file_location("_mod", str(_REPO / "core" / "store.py"))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["_mod"] = _mod
_spec.loader.exec_module(_mod)
```

After this change, tests can simply:

```python
from mem_reflection_hermes.core.store import MemoryStore
```

However, existing test loading patterns should still work (backward compat). The key is that production code no longer needs to support the fallback path.

## 6. Files Affected

| File | Action |
|------|--------|
| `tests/pytest.ini` | Add `pythonpath = ..` |
| `tests/conftest.py` | Ensure package is importable |
| 10 production `.py` files | Remove ImportError fallback blocks |

## 7. Acceptance Criteria

1. No production file contains `importlib.util.spec_from_file_location`.
2. All production imports use standard relative or absolute import syntax.
3. Optional dependency try/except blocks (jieba, spaCy, tiktoken) remain untouched.
4. `pytest tests/ -v` passes all 413+ tests.
5. ~250 lines removed from production code.
