## 1. RED Phase — Intent-Facing Tests

- [x] 1.1 Create `tests/test_store_module_split.py` with tests verifying `core/models.py`, `core/entities.py`, `core/tokenization.py`, `core/utils.py`, and `core/config.py` expose required symbols.
- [x] 1.2 Add tests verifying `from core.store import X` returns the same object as the canonical module for each relocated symbol group.
- [x] 1.3 Add test verifying `core/store.py` line count is under 800 lines.
- [x] 1.4 Add test verifying `__init__.py` imports `MemoryFrontmatter` from `core.models` and `plugin_config` from `core.config`.
- [x] 1.5 Run `pytest tests/test_store_module_split.py -v` and confirm tests fail (RED phase).

## 2. GREEN Phase — Extraction

- [x] 2.1 Create `core/models.py` and move `MemoryFrontmatter`, `LoadedMemory`, `MemoryStatEntry`, `MemoryEffectiveness`, `SkillFrontmatter`, `LoadedSkill`, plus parse/serialize frontmatter helpers.
- [x] 2.2 Create `core/entities.py` and move `extract_entities`, `_normalize_entity_text`, `entity_enabled`.
- [x] 2.3 Create `core/tokenization.py` and move `_tokenise`, `estimate_tokens`, `_memory_tokens`, `is_cjk`, `cjk_ratio`, `adaptive_conflict_threshold`.
- [x] 2.4 Create `core/utils.py` and move `fast_hash`, `normalize_zone`, `is_valid_zone`, `sanitize_zone_filename`, zone constants, and thresholds.
- [x] 2.5 Extend `core/config.py` with `load_config`, `plugin_config`, `hermes_home`, `plugin_data_dir`, `user_memories_dir`, `project_memories_dir`, `user_skills_dir`, `project_skills_dir`, `embeddings_enabled`, `micro_reflection_enabled`, `palace_mode_enabled`, `profile_mode_enabled`, `palace_index_path`, `zone_cache_dir`, `CONFIG_KEY_RERANKER`.
- [x] 2.6 Update `core/store.py` to import from the new modules, remove inline definitions, and keep re-exports for backward compatibility.
- [x] 2.7 Update `__init__.py` to import relocated symbols from their canonical paths (`core.models`, `core.config`, `core.tokenization`, `core.utils`).

## 3. Verification

- [x] 3.1 Run `pytest tests/test_store_module_split.py -v` until all tests pass.
- [x] 3.2 Run full test suite `pytest tests/ -v` and confirm no regressions.
- [x] 3.3 Run `python -m py_compile` on all new and modified core files.
- [x] 3.4 Grep for any remaining inline definitions in `core/store.py` that should be re-exports only.
