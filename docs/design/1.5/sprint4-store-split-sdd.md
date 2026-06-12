# v1.5 SDD: core/store.py Split

**Version**: v1.5
**Date**: 2026-06-10
**Status**: Draft
**Scope**: Sprint 4 — decompose `core/store.py` (2,349 lines) into 5 focused modules
**Trigger**: L6-05 from v1.4 six-layer review

## 1. Purpose

Split `core/store.py` from a 2,349-line "leaf module" that carries 7 distinct responsibilities into 5 focused modules while preserving all import paths via re-exports.

## 2. Problem

`core/store.py` is the largest file in the project and the declared "leaf module" of the dependency DAG, yet it contains:

| Responsibility | Lines | Symbols |
|---------------|-------|---------|
| Data models | ~180 | `MemoryFrontmatter`, `LoadedMemory`, `SkillFrontmatter`, `parse_frontmatter` |
| Config management | ~100 | `plugin_config`, `load_config`, `*_enabled`, `*_dir` |
| File I/O | ~200 | `write_memory_atomic`, `read_memory`, async writer |
| MemoryStore + SkillStore | ~500 | `MemoryStore`, `SkillStore`, SQLite ops |
| Entity extraction | ~80 | `extract_entities`, `_normalize_entity_text`, `entity_enabled` |
| Token estimation | ~250 | `_tokenise`, `estimate_tokens`, `is_cjk`, `cjk_ratio`, `adaptive_conflict_threshold` |
| Utility functions | ~40 | `fast_hash`, `normalize_zone`, `is_valid_zone`, `sanitize_zone_filename` |

External callers import ~30 symbols from `core.store`. Any split must preserve these imports.

## 3. Design Goals

- Each extracted module has a single responsibility.
- `core/store.py` remains as a re-export facade — all existing `from core.store import X` calls continue to work.
- `core/config.py` (already exists at 253 lines) absorbs config management functions from store.py.
- The dependency DAG is preserved: extracted modules import from each other only in one direction.

## 4. Non-Goals

- No API changes to `MemoryStore`, `SkillStore`, or any public function.
- No changes to storage format (Markdown + YAML + SQLite).
- No changes to the module dependency DAG in `CLAUDE.md`.
- No performance optimization — this is a structural refactor only.

## 5. Proposed Design

### 5.1 Target Structure

```
core/
    models.py         (~200 lines) — dataclasses: MemoryFrontmatter, LoadedMemory, SkillFrontmatter
    config.py         (~200 lines) — existing + plugin_config, load_config, *_enabled, *_dir
    entities.py       (~100 lines) — extract_entities, _normalize_entity_text, entity tables
    tokenization.py   (~120 lines) — _tokenise, estimate_tokens, is_cjk, cjk_ratio, adaptive thresholds
    utils.py          (~50 lines)  — fast_hash, normalize_zone, is_valid_zone, sanitize_zone_filename
    store.py          (~600 lines) — MemoryStore, SkillStore, file I/O, re-exports
    search.py         (unchanged)
    graph.py          (unchanged)
    backend.py        (unchanged)
```

### 5.2 Import Direction

```
utils.py         ← no project imports
tokenization.py  ← imports utils (for CJK char classification)
models.py        ← no project imports
config.py        ← imports models (for type references)
entities.py      ← imports tokenization, utils
store.py         ← imports all above + re-exports their public symbols
```

### 5.3 Re-export Facade

```python
# core/store.py (after split)
"""MemoryStore, SkillStore, and re-exports from extracted modules."""

from .models import MemoryFrontmatter, LoadedMemory, SkillFrontmatter  # noqa: F401
from .config import plugin_config, load_config, plugin_data_dir  # noqa: F401
from .entities import extract_entities, entity_enabled  # noqa: F401
from .tokenization import _tokenise, estimate_tokens, is_cjk  # noqa: F401
from .utils import fast_hash, normalize_zone, is_valid_zone  # noqa: F401

# ... MemoryStore and SkillStore class definitions ...
```

### 5.4 Dependency Verification

All external imports must resolve. Key consumers:

- `core/search.py`: `from .store import MemoryStore, _tokenise, estimate_tokens`
- `core/graph.py`: `from .store import MemoryStore`
- `memory/context.py`: `from ..core.store import MemoryStore, plugin_config`
- `reflection/engine.py`: `from ..core.store import MemoryStore, MemoryFrontmatter`
- `runtime/tools.py`: via `_lb("MemoryStore")`
- `__init__.py`: `from .core.store import MemoryStore, SkillStore, ...`

All of these resolve through the re-export facade without changes.

## 6. Files Affected

| File | Action |
|------|--------|
| `core/models.py` | New — dataclass definitions + parse/serialize |
| `core/entities.py` | New — entity extraction pipeline |
| `core/tokenization.py` | New — token estimation + CJK functions |
| `core/utils.py` | New — hash, zone, path utilities |
| `core/config.py` | Expand — absorb config functions from store.py |
| `core/store.py` | Slim down — MemoryStore/SkillStore + re-exports |
| `CLAUDE.md` | Update module table |

## 7. Acceptance Criteria

1. `core/store.py` is under 700 lines (MemoryStore + SkillStore + re-exports).
2. All `from core.store import X` paths still resolve.
3. No circular imports — `python -m py_compile` succeeds on all files.
4. New modules have no Hermes dependencies (pure Python stdlib).
5. All 413+ tests pass without modification.
6. `grep -r "from.*core.store import" --include="*.py"` shows no broken imports.
