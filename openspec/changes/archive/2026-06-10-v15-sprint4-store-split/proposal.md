## Why

`core/store.py` has grown to 2,349 lines and now carries 7 distinct responsibilities: config management, data models, file I/O, SQLite storage, entity extraction, tokenization, and utility functions. This violates the "leaf module" contract documented in CLAUDE.md, makes testing and review expensive, and blocks further v1.5 architectural cleanup. We need to split it into focused modules while preserving every existing external import.

## What Changes

- Extract data models (`MemoryFrontmatter`, `LoadedMemory`, `SkillFrontmatter`, parse/serialize) into `core/models.py`
- Extract entity extraction into `core/entities.py`
- Extract token estimation + CJK helpers into `core/tokenization.py`
- Extract utility helpers (`fast_hash`, `normalize_zone`, `is_valid_zone`, `sanitize_zone_filename`) into `core/utils.py`
- Extend existing `core/config.py` to own `load_config`, `plugin_config`, and all `*_enabled` / `*_dir` helpers currently in `store.py`
- Shrink `core/store.py` to ~600 lines focused on `MemoryStore`, `SkillStore`, and file I/O
- Keep backward compatibility via re-exports from `core/store.py`
- Update `__init__.py` imports to prefer new module paths
- Add RED-phase tests verifying re-export parity and `core/store.py` line count under 800

## Capabilities

### New Capabilities
- `store-module-split`: Requirements governing how `core/store.py` responsibilities are relocated into focused modules while preserving external imports.

### Modified Capabilities
- *(none — this is a pure structural refactor with no behavior change)*

## Impact

- `core/store.py`, `core/config.py`, `core/models.py` (new), `core/entities.py` (new), `core/tokenization.py` (new), `core/utils.py` (new)
- `__init__.py` import paths
- All tests importing from `core.store` continue to work via re-exports
- No Hermes host contract changes
