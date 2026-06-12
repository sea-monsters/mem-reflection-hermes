## Context

`core/store.py` is the largest module in the codebase at 2,349 lines. It started as a leaf data module but has accumulated config, models, tokenization, entity extraction, hashing, and zone utilities. This makes it difficult to review, slows down test iteration, and contradicts the documented module dependency DAG (store.py should be a leaf with no project imports). Sprint 4 completes the v1.5 refactor sequence by splitting these responsibilities into single-purpose modules.

Sprints 1–3 are complete and green (510 tests passing). This sprint builds on that foundation.

## Goals / Non-Goals

**Goals:**
- Reduce `core/store.py` to ~600 lines focused on `MemoryStore`, `SkillStore`, and file I/O
- Create `core/models.py`, `core/entities.py`, `core/tokenization.py`, `core/utils.py`
- Move config helpers into existing `core/config.py`
- Preserve every public symbol so `from core.store import X` continues to work
- Add frozen RED tests for re-export parity and size limits
- Keep the full test suite green

**Non-Goals:**
- No behavior changes to MemoryStore, SkillStore, or host contract
- No new features (entity extraction algorithm stays the same)
- No changes to the SQLite schema
- No removal of deprecated aliases

## Decisions

1. **Keep re-exports in `core/store.py` instead of updating every import site.**
   - Rationale: Dozens of files import `MemoryFrontmatter`, `_tokenise`, `plugin_config`, etc. from `core.store`. Re-exports minimize blast radius and keep the refactor mechanical.
   - Alternative considered: update all imports. Rejected because it would touch ~15 files and increase regression risk.

2. **Promote `core/config.py` to own all config helpers rather than creating a new module.**
   - Rationale: `core/config.py` already exists with typed config models. Migrating `load_config`, `plugin_config`, and `*_enabled` helpers there is a natural extension.

3. **Place entity extraction in `core/entities.py`, not inside `core/search.py`.**
   - Rationale: Entity extraction is a data-layer concern that populates SQLite `entities`/`entity_links` tables. Search consumes entities but should not own extraction logic.

4. **Do not migrate `_bm25_search` / `_bm25_search_scored` out of `store.py` yet.**
   - Rationale: These are tightly coupled to `MemoryStore` private state and tokenization. Moving them now would require exposing internals. They will move when `core/search.py` is later refactored to accept an explicit inverted-index provider.

5. **Use module-level imports (not `_lb`) between new core modules.**
   - Rationale: Core modules form a stable DAG. Late binding is reserved for runtime/hooks/tools that need host-safe deferred loading.

## Risks / Trade-offs

- **[Risk] Circular imports between new core modules** → Mitigation: establish strict layer order (utils → tokenization → config → models → entities → store). Use deferred imports only where unavoidable.
- **[Risk] Test fixtures that monkeypatch `core.store` attributes break if the attribute is only a re-export** → Mitigation: RED tests verify `getattr(core.store, X) is getattr(core.models, X)` so monkeypatches on `core.store.X` still propagate to the canonical symbol.
- **[Risk] `__init__.py` imports the real module path but tests still patch `core.store`** → Mitigation: keep re-exports identity-equal (`from .models import MemoryFrontmatter` sets the same object reference).
- **[Risk] Windows path helpers (`hermes_home`, `*_dir`) move to config and break runtime paths** → Mitigation: paths are pure functions with no state; unit tests exercise every helper.
