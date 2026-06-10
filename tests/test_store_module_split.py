"""test_store_module_split.py — Verify core/store.py responsibility split.

Coverage:
- New core modules expose required symbols
- core/store.py re-exports are identity-equal to canonical modules
- core/store.py stays under 800 lines
- __init__.py imports from canonical paths
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PKG = "mem_reflection_hermes"


def _ensure_pkg(fqn: str, path: Path):
    if fqn not in sys.modules:
        pkg = types.ModuleType(fqn)
        pkg.__path__ = [str(path)]
        sys.modules[fqn] = pkg


def _load_module(fqn: str, rel_path: str):
    _ensure_pkg(_PKG, _REPO)
    _ensure_pkg(f"{_PKG}.core", _REPO / "core")
    spec = importlib.util.spec_from_file_location(fqn, str(_REPO / rel_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fqn] = mod
    spec.loader.exec_module(mod)
    return mod


# Load canonical new modules using real package names so relative imports work.
models = _load_module("mem_reflection_hermes.core.models", "core/models.py")
entities = _load_module("mem_reflection_hermes.core.entities", "core/entities.py")
tokenization = _load_module("mem_reflection_hermes.core.tokenization", "core/tokenization.py")
utils = _load_module("mem_reflection_hermes.core.utils", "core/utils.py")
config = _load_module("mem_reflection_hermes.core.config", "core/config.py")

# Load store module (which should re-export from canonical modules)
store = _load_module("mem_reflection_hermes.core.store", "core/store.py")


def _count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


class TestModelsModule:
    def test_required_symbols_exist(self):
        for name in (
            "MemoryFrontmatter", "LoadedMemory", "MemoryStatEntry",
            "MemoryEffectiveness", "SkillFrontmatter", "LoadedSkill",
            "parse_frontmatter", "serialize_frontmatter",
        ):
            assert hasattr(models, name), f"core/models.py missing {name}"

    def test_store_re_exports_models(self):
        for name in (
            "MemoryFrontmatter", "LoadedMemory", "MemoryStatEntry",
            "MemoryEffectiveness", "SkillFrontmatter", "LoadedSkill",
            "parse_frontmatter", "serialize_frontmatter",
        ):
            assert hasattr(store, name), f"core/store.py missing re-export {name}"
            assert getattr(store, name) is getattr(models, name), (
                f"core/store.{name} is not identity-equal to core.models.{name}"
            )


class TestEntitiesModule:
    def test_required_symbols_exist(self):
        for name in ("extract_entities", "_normalize_entity_text", "entity_enabled"):
            assert hasattr(entities, name), f"core/entities.py missing {name}"

    def test_store_re_exports_entities(self):
        for name in ("extract_entities", "_normalize_entity_text", "entity_enabled"):
            assert hasattr(store, name), f"core/store.py missing re-export {name}"
            assert getattr(store, name) is getattr(entities, name), (
                f"core/store.{name} is not identity-equal to core.entities.{name}"
            )


class TestTokenizationModule:
    def test_required_symbols_exist(self):
        for name in (
            "_tokenise", "estimate_tokens", "_memory_tokens",
            "is_cjk", "cjk_ratio", "adaptive_conflict_threshold",
        ):
            assert hasattr(tokenization, name), f"core/tokenization.py missing {name}"

    def test_store_re_exports_tokenization(self):
        for name in (
            "_tokenise", "estimate_tokens", "_memory_tokens",
            "is_cjk", "cjk_ratio", "adaptive_conflict_threshold",
        ):
            assert hasattr(store, name), f"core/store.py missing re-export {name}"
            assert getattr(store, name) is getattr(tokenization, name), (
                f"core/store.{name} is not identity-equal to core.tokenization.{name}"
            )


class TestUtilsModule:
    def test_required_symbols_exist(self):
        for name in (
            "fast_hash", "normalize_zone", "is_valid_zone", "sanitize_zone_filename",
            "_ZONE_CORE", "_ZONE_WORK", "_ZONE_EPISODE", "_ZONE_GENERAL",
            "_VALID_ZONES", "_PROJECT_ZONE_PREFIX",
            "_ZONE_SPLIT_THRESHOLD", "_ZONE_MERGE_THRESHOLD",
        ):
            assert hasattr(utils, name), f"core/utils.py missing {name}"

    def test_store_re_exports_utils(self):
        for name in (
            "fast_hash", "normalize_zone", "is_valid_zone", "sanitize_zone_filename",
            "_ZONE_CORE", "_ZONE_WORK", "_ZONE_EPISODE", "_ZONE_GENERAL",
            "_VALID_ZONES", "_PROJECT_ZONE_PREFIX",
            "_ZONE_SPLIT_THRESHOLD", "_ZONE_MERGE_THRESHOLD",
        ):
            assert hasattr(store, name), f"core/store.py missing re-export {name}"
            assert getattr(store, name) is getattr(utils, name), (
                f"core/store.{name} is not identity-equal to core.utils.{name}"
            )


class TestConfigModule:
    def test_required_symbols_exist(self):
        for name in (
            "load_config", "plugin_config", "hermes_home", "plugin_data_dir",
            "user_memories_dir", "project_memories_dir", "user_skills_dir",
            "project_skills_dir", "embeddings_enabled", "micro_reflection_enabled",
            "palace_mode_enabled", "profile_mode_enabled", "palace_index_path",
            "zone_cache_dir", "CONFIG_KEY_RERANKER",
        ):
            assert hasattr(config, name), f"core/config.py missing {name}"

    def test_store_re_exports_config(self):
        for name in (
            "load_config", "plugin_config", "hermes_home", "plugin_data_dir",
            "user_memories_dir", "project_memories_dir", "user_skills_dir",
            "project_skills_dir", "embeddings_enabled", "micro_reflection_enabled",
            "palace_mode_enabled", "profile_mode_enabled", "palace_index_path",
            "zone_cache_dir",
        ):
            assert hasattr(store, name), f"core/store.py missing re-export {name}"
            assert getattr(store, name) is getattr(config, name), (
                f"core/store.{name} is not identity-equal to core.config.{name}"
            )


class TestStoreModuleSize:
    def test_store_under_800_lines(self):
        lines = _count_lines(_REPO / "core" / "store.py")
        assert lines < 800, f"core/store.py is {lines} lines (must be < 800)"


class TestPackageImports:
    def test_init_imports_from_canonical_paths(self):
        init_path = _REPO / "__init__.py"
        _ensure_pkg(_PKG, _REPO)
        # Register subpackages expected by __init__
        for sub in ("core", "reflection", "memory", "runtime", "web"):
            sub_init = _REPO / sub / "__init__.py"
            sub_pkg = importlib.util.module_from_spec(
                importlib.util.spec_from_file_location(f"{_PKG}.{sub}", str(sub_init))
            )
            sub_pkg.__path__ = [str(_REPO / sub)]
            sys.modules[f"{_PKG}.{sub}"] = sub_pkg

        spec = importlib.util.spec_from_file_location(f"{_PKG}.__init__", str(init_path))
        init_mod = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = init_mod
        init_mod.__path__ = [_REPO]
        spec.loader.exec_module(init_mod)

        assert getattr(init_mod, "MemoryFrontmatter") is models.MemoryFrontmatter
        assert getattr(init_mod, "plugin_config") is config.plugin_config
        assert getattr(init_mod, "fast_hash") is utils.fast_hash
        assert getattr(init_mod, "_tokenise") is tokenization._tokenise
        assert getattr(init_mod, "extract_entities") is entities.extract_entities
