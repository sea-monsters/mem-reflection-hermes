## ADDED Requirements

### Requirement: Data models live in core/models.py
The system SHALL define `MemoryFrontmatter`, `LoadedMemory`, `MemoryStatEntry`, `MemoryEffectiveness`, `SkillFrontmatter`, `LoadedSkill`, and frontmatter parse/serialize helpers in `core/models.py`.

#### Scenario: Models are importable from the new module
- **WHEN** a caller runs `from core.models import MemoryFrontmatter, LoadedMemory`
- **THEN** the import succeeds and returns the same classes used by `MemoryStore`

#### Scenario: Old import path remains compatible
- **WHEN** a caller runs `from core.store import MemoryFrontmatter, LoadedMemory`
- **THEN** the import succeeds and returns the exact same objects as `core.models`

### Requirement: Entity extraction lives in core/entities.py
The system SHALL define `extract_entities`, `_normalize_entity_text`, and `entity_enabled` in `core/entities.py`.

#### Scenario: Entity extraction is importable from the new module
- **WHEN** a caller runs `from core.entities import extract_entities`
- **THEN** the import succeeds and returns the same function used by `MemoryStore`

#### Scenario: Old import path remains compatible
- **WHEN** a caller runs `from core.store import extract_entities`
- **THEN** the import succeeds and returns the exact same object as `core.entities.extract_entities`

### Requirement: Token estimation lives in core/tokenization.py
The system SHALL define `_tokenise`, `estimate_tokens`, `_memory_tokens`, `is_cjk`, `cjk_ratio`, and `adaptive_conflict_threshold` in `core/tokenization.py`.

#### Scenario: Token helpers are importable from the new module
- **WHEN** a caller runs `from core.tokenization import _tokenise, estimate_tokens`
- **THEN** the import succeeds and returns the same functions used by search and store

#### Scenario: Old import path remains compatible
- **WHEN** a caller runs `from core.store import _tokenise, estimate_tokens`
- **THEN** the import succeeds and returns the exact same objects as `core.tokenization`

### Requirement: Utility helpers live in core/utils.py
The system SHALL define `fast_hash`, `normalize_zone`, `is_valid_zone`, `sanitize_zone_filename`, `_ZONE_CORE`, `_ZONE_WORK`, `_ZONE_EPISODE`, `_ZONE_GENERAL`, `_VALID_ZONES`, `_PROJECT_ZONE_PREFIX`, `_ZONE_SPLIT_THRESHOLD`, and `_ZONE_MERGE_THRESHOLD` in `core/utils.py`.

#### Scenario: Utility helpers are importable from the new module
- **WHEN** a caller runs `from core.utils import fast_hash, normalize_zone`
- **THEN** the import succeeds and returns the same functions used by store

#### Scenario: Old import path remains compatible
- **WHEN** a caller runs `from core.store import fast_hash, normalize_zone`
- **THEN** the import succeeds and returns the exact same objects as `core.utils`

### Requirement: Config helpers live in core/config.py
The system SHALL define `load_config`, `plugin_config`, `hermes_home`, `plugin_data_dir`, `user_memories_dir`, `project_memories_dir`, `user_skills_dir`, `project_skills_dir`, `embeddings_enabled`, `micro_reflection_enabled`, `palace_mode_enabled`, `profile_mode_enabled`, `palace_index_path`, `zone_cache_dir`, and `CONFIG_KEY_RERANKER` in `core/config.py`.

#### Scenario: Config helpers are importable from the config module
- **WHEN** a caller runs `from core.config import plugin_config, load_config`
- **THEN** the import succeeds and returns the same callables used by store and runtime

#### Scenario: Old import path remains compatible
- **WHEN** a caller runs `from core.store import plugin_config, load_config`
- **THEN** the import succeeds and returns the exact same objects as `core.config`

### Requirement: core/store.py remains focused
The system SHALL keep `core/store.py` under 800 lines after the split and restrict it to `MemoryStore`, `SkillStore`, file I/O helpers, and re-exports.

#### Scenario: Store module size reduction
- **WHEN** `core/store.py` is measured
- **THEN** its line count is under 800 lines

#### Scenario: Store module responsibilities
- **WHEN** a reviewer inspects `core/store.py`
- **THEN** it contains no definitions of `MemoryFrontmatter`, `_tokenise`, `extract_entities`, `plugin_config`, `fast_hash`, or zone constants other than re-exports

### Requirement: Package entrypoint imports from canonical paths
The system SHALL update `__init__.py` to import `MemoryFrontmatter`, `LoadedMemory`, `SkillFrontmatter`, and frontmatter helpers from `core.models`; config helpers from `core.config`; token helpers from `core.tokenization`; and utility helpers from `core.utils`.

#### Scenario: Package import uses relocated modules
- **WHEN** `__init__.py` is loaded
- **THEN** it imports the relocated symbols from their canonical module paths

#### Scenario: Public package API is unchanged
- **WHEN** a caller runs `from mem_reflection_hermes import MemoryFrontmatter, plugin_config, fast_hash`
- **THEN** the import succeeds and returns the same objects as before the refactor
