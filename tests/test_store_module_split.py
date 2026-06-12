"""Compatibility tests for the v1.5 core module split."""
from __future__ import annotations

from mem_reflection_hermes import (
    MemoryFrontmatter as package_memory_frontmatter,
    _tokenise as package_tokenise,
    extract_entities as package_extract_entities,
    fast_hash as package_fast_hash,
    plugin_config as package_plugin_config,
)
from mem_reflection_hermes.core import config, entities, models, store, tokenization, utils


class TestModelsCompatibility:
    def test_required_symbols_exist(self):
        for name in (
            "MemoryFrontmatter",
            "LoadedMemory",
            "MemoryStatEntry",
            "MemoryEffectiveness",
            "SkillFrontmatter",
            "LoadedSkill",
            "parse_frontmatter",
            "serialize_frontmatter",
        ):
            assert hasattr(models, name), f"core/models.py missing {name}"
            assert hasattr(store, name), f"core/store.py missing public symbol {name}"

    def test_store_frontmatter_round_trip_matches_models(self):
        body = "Keep the tests focused on behavior."
        data = {
            "id": "mem-compat",
            "created": "2026-06-11T00:00:00+00:00",
            "source": "unit-test",
            "confidence": 0.75,
            "pinned": True,
            "tags": ["tests"],
            "supersedes": [],
            "supersedes_reason": "",
            "valid_from": None,
            "valid_until": None,
            "context_scope": "project",
            "zone": "general",
            "rank": 3,
        }

        rendered = store.serialize_frontmatter(data, body)

        assert rendered == models.serialize_frontmatter(data, body)
        assert store.parse_frontmatter(rendered) == models.parse_frontmatter(rendered)


class TestEntitiesCompatibility:
    def test_required_symbols_exist(self):
        for name in ("extract_entities", "_normalize_entity_text", "entity_enabled"):
            assert hasattr(entities, name), f"core/entities.py missing {name}"
            assert hasattr(store, name), f"core/store.py missing public symbol {name}"

    def test_store_extract_entities_matches_canonical_module(self):
        text = "Alice discussed Project Hermes with Bob in Shanghai."
        assert store.extract_entities(text) == entities.extract_entities(text)


class TestTokenizationCompatibility:
    def test_required_symbols_exist(self):
        for name in (
            "_tokenise",
            "estimate_tokens",
            "_memory_tokens",
            "is_cjk",
            "cjk_ratio",
            "adaptive_conflict_threshold",
        ):
            assert hasattr(tokenization, name), f"core/tokenization.py missing {name}"
            assert hasattr(store, name), f"core/store.py missing public symbol {name}"

    def test_store_tokenization_helpers_match_canonical_module(self):
        text = "Mixed English 中文 text for token checks"
        assert store._tokenise(text) == tokenization._tokenise(text)
        assert store.estimate_tokens(text) == tokenization.estimate_tokens(text)
        assert store.cjk_ratio(text) == tokenization.cjk_ratio(text)
        assert store.is_cjk("中") == tokenization.is_cjk("中")


class TestUtilsCompatibility:
    def test_required_symbols_exist(self):
        for name in (
            "fast_hash",
            "normalize_zone",
            "is_valid_zone",
            "sanitize_zone_filename",
            "_ZONE_CORE",
            "_ZONE_WORK",
            "_ZONE_EPISODE",
            "_ZONE_GENERAL",
            "_VALID_ZONES",
            "_PROJECT_ZONE_PREFIX",
            "_ZONE_SPLIT_THRESHOLD",
            "_ZONE_MERGE_THRESHOLD",
        ):
            assert hasattr(utils, name), f"core/utils.py missing {name}"
            assert hasattr(store, name), f"core/store.py missing public symbol {name}"

    def test_store_utils_match_canonical_module(self):
        text = "compatibility-test"
        zone = "project:alpha"
        assert store.fast_hash(text) == utils.fast_hash(text)
        assert store.normalize_zone(zone) == utils.normalize_zone(zone)
        assert store.is_valid_zone(zone) == utils.is_valid_zone(zone)
        assert store.sanitize_zone_filename(zone) == utils.sanitize_zone_filename(zone)


class TestConfigCompatibility:
    def test_required_symbols_exist(self):
        for name in (
            "load_config",
            "plugin_config",
            "hermes_home",
            "plugin_data_dir",
            "user_memories_dir",
            "project_memories_dir",
            "user_skills_dir",
            "project_skills_dir",
            "embeddings_enabled",
            "micro_reflection_enabled",
            "palace_mode_enabled",
            "profile_mode_enabled",
            "palace_index_path",
            "zone_cache_dir",
            "CONFIG_KEY_RERANKER",
        ):
            assert hasattr(config, name), f"core/config.py missing {name}"
            assert hasattr(store, name), f"core/store.py missing public symbol {name}"

    def test_store_config_helpers_match_canonical_module(self):
        assert store.profile_mode_enabled() == config.profile_mode_enabled()
        assert store.palace_mode_enabled() == config.palace_mode_enabled()
        assert store.plugin_config() == config.plugin_config()


class TestPackageExports:
    def test_package_exports_preserve_public_behavior(self):
        assert package_memory_frontmatter is store.MemoryFrontmatter
        assert package_plugin_config() == config.plugin_config()
        assert package_fast_hash("pkg-export") == utils.fast_hash("pkg-export")
        assert package_tokenise("hello world") == tokenization._tokenise("hello world")
        assert package_extract_entities("Alice met Bob.") == entities.extract_entities("Alice met Bob.")
