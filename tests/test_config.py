from __future__ import annotations

from core.config import get_config_diagnostics, get_plugin_config_model


class TestConfigModel:
    def test_invalid_types_fall_back_to_defaults(self):
        model = get_plugin_config_model({
            "search": {"cjk_tokenizer": "bogus"},
            "context": {
                "token_budget": "bad",
                "recall_timeout_ms": True,
                "compression": {"enabled": "yes", "mild_ratio": "oops"},
            },
            "checkpoint": {"max_pending_sessions": "many"},
            "entity": {"weight": "heavy", "extractor": "mystery"},
            "reflection": {"mode": "sideways", "micro_reflection": "later"},
            "curator": {"stale": {"days": "old", "effectiveness_threshold": "low"}},
        })

        assert model.search.cjk_tokenizer == "auto"
        assert model.context.token_budget == 2000
        assert model.context.recall_timeout_ms == 1500
        assert model.context.compression.enabled is True
        assert model.checkpoint.max_pending_sessions == 20
        assert model.entity.weight == 0.08
        assert model.entity.extractor == "auto"
        assert model.reflection.mode == "auto"
        assert model.reflection.micro_reflection is False
        assert model.curator.stale_days == 90
        assert model.curator.effectiveness_threshold == 0.1
        assert model.diagnostics["warnings"]

    def test_unknown_keys_are_reported(self):
        diagnostics = get_config_diagnostics({
            "search": {"extra": 1},
            "context": {"compression": {"spare": 2}},
            "mystery": True,
            "entity": {"odd": 3},
            "reflection": {"extra": 1},
            "curator": {"stale": {"shadow": 4}},
        })

        assert "mystery" in diagnostics["diagnostics"]["unknown_keys"]
        assert "search.extra" in diagnostics["diagnostics"]["unknown_keys"]
        assert "context.compression.spare" in diagnostics["diagnostics"]["unknown_keys"]
        assert "entity.odd" in diagnostics["diagnostics"]["unknown_keys"]
        assert "reflection.extra" in diagnostics["diagnostics"]["unknown_keys"]
        assert "curator.stale.shadow" in diagnostics["diagnostics"]["unknown_keys"]

    def test_empty_config_uses_all_defaults(self):
        """P1: Empty config should produce all default values."""
        model = get_plugin_config_model({})

        assert model.search.cjk_tokenizer == "auto"
        assert model.context.token_budget == 2000
        assert model.context.recall_timeout_ms == 1500
        assert model.context.split_stable_dynamic is True
        assert model.context.compression.enabled is True
        assert model.context.compression.mild_ratio == 0.85
        assert model.checkpoint.enabled is True
        assert model.checkpoint.recover_on_session_start is True
        assert model.checkpoint.max_pending_sessions == 20
        assert model.entity.enabled is True
        assert model.entity.weight == 0.08
        assert model.entity.extractor == "auto"
        assert model.reflection.mode == "auto"
        assert model.reflection.micro_reflection is False
        assert model.curator.enabled is True
        assert model.curator.stale_days == 90
        assert model.curator.effectiveness_threshold == 0.1
        assert model.diagnostics["warnings"] == []
        assert model.diagnostics["unknown_keys"] == []

    def test_valid_values_are_accepted(self):
        """P1: Valid config values should be accepted without warnings."""
        model = get_plugin_config_model({
            "search": {"cjk_tokenizer": "bigram"},
            "context": {
                "token_budget": 3000,
                "recall_timeout_ms": 2000,
                "split_stable_dynamic": False,
                "compression": {"enabled": False, "mild_ratio": 0.9},
            },
            "checkpoint": {"enabled": False, "max_pending_sessions": 10},
            "entity": {"enabled": False, "weight": 0.15, "extractor": "regex"},
            "reflection": {"mode": "llm", "micro_reflection": True},
        })

        assert model.search.cjk_tokenizer == "bigram"
        assert model.context.token_budget == 3000
        assert model.context.recall_timeout_ms == 2000
        assert model.context.split_stable_dynamic is False
        assert model.context.compression.enabled is False
        assert model.context.compression.mild_ratio == 0.9
        assert model.checkpoint.enabled is False
        assert model.checkpoint.max_pending_sessions == 10
        assert model.entity.enabled is False
        assert model.entity.weight == 0.15
        assert model.entity.extractor == "regex"
        assert model.reflection.mode == "llm"
        assert model.reflection.micro_reflection is True
        # No warnings for valid values
        assert model.diagnostics["warnings"] == []

    def test_none_config_uses_defaults(self):
        """P1: None values should fall back to defaults gracefully."""
        model = get_plugin_config_model({
            "search": {"cjk_tokenizer": None},
            "context": {
                "token_budget": None,
                "compression": {"enabled": None, "mild_ratio": None},
            },
        })

        assert model.search.cjk_tokenizer == "auto"
        assert model.context.token_budget == 2000
        assert model.context.compression.enabled is True
        assert model.context.compression.mild_ratio == 0.85

    def test_string_integers_are_parsed(self):
        """P1: String values that look like integers should be parsed."""
        model = get_plugin_config_model({
            "context": {"token_budget": "2500"},
            "checkpoint": {"max_pending_sessions": "15"},
        })

        assert model.context.token_budget == 2500
        assert model.checkpoint.max_pending_sessions == 15


class TestConfigFloatStringParsing:
    """Gap 3: Verify _as_float parses string float values correctly.

    Design intent (FEP §8 config draft): config values like entity.weight,
    compression.mild_ratio accept floats. YAML parsers may deliver these as
    strings. The config model must parse "0.5" → 0.5, not reject it.
    """

    def test_entity_weight_from_float_string(self):
        model = get_plugin_config_model({
            "entity": {"weight": "0.25"},
        })
        assert model.entity.weight == 0.25
        assert model.diagnostics["warnings"] == []

    def test_compression_ratios_from_float_strings(self):
        model = get_plugin_config_model({
            "context": {"compression": {
                "mild_ratio": "0.9",
                "aggressive_ratio": "1.1",
                "emergency_ratio": "1.5",
            }},
        })
        assert model.context.compression.mild_ratio == 0.9
        assert model.context.compression.aggressive_ratio == 1.1
        assert model.context.compression.emergency_ratio == 1.5

    def test_curator_effectiveness_threshold_from_string(self):
        model = get_plugin_config_model({
            "curator": {"stale": {"effectiveness_threshold": "0.15"}},
        })
        assert model.curator.effectiveness_threshold == 0.15

    def test_non_numeric_string_float_falls_back_to_default(self):
        model = get_plugin_config_model({
            "entity": {"weight": "heavy"},
        })
        assert model.entity.weight == 0.08
        assert any("entity.weight" in w for w in model.diagnostics["warnings"])

    def test_integer_value_accepted_as_float(self):
        """Integers (e.g. 1) should be accepted where float is expected."""
        model = get_plugin_config_model({
            "entity": {"weight": 1},
        })
        assert model.entity.weight == 1.0


class TestConfigFeatureFlagIntegration:
    """Gap 4: Verify config flags control behavior, not just config parsing.

    Design intent (FEP §5.4, §5.7):
    - entity.enabled=False → search should skip entity extraction entirely
    - compression.enabled=False → context assembly should not use graded compression
    """

    def test_entity_disabled_means_no_entity_extraction_in_config(self):
        """When entity.enabled=False, the config should reflect that state."""
        model = get_plugin_config_model({"entity": {"enabled": False}})
        assert model.entity.enabled is False

    def test_compression_disabled_means_only_none_level_in_config(self):
        """When compression.enabled=False, only 'none' level should be available."""
        model = get_plugin_config_model({"context": {"compression": {"enabled": False}}})
        assert model.context.compression.enabled is False

    def test_entity_disabled_skips_entity_boost_in_search(self):
        """Design intent: entity.enabled=False means entity_boost should always be 0.

        This tests the full pipeline: config flag → entity_enabled() → search behavior.
        """
        from core import store as _store_mod

        import tempfile
        from pathlib import Path

        tmpdir = tempfile.mkdtemp(prefix="hermes_entity_flag_")
        try:
            root = Path(tmpdir) / "memories"
            root.mkdir(parents=True, exist_ok=True)
            db_path = Path(tmpdir) / "memories.db"
            store = _store_mod.MemoryStore(user_root=root, db_path=db_path)

            fm = _store_mod.MemoryFrontmatter.new(source="test")
            fm.id = "flag-test"
            store.put("user", fm, 'Use file "config.yaml" for entity test')

            # Monkeypatch plugin_config to return entity disabled
            original_config = _store_mod.plugin_config
            _store_mod.plugin_config = lambda: {"entity": {"enabled": False}}

            try:
                # Force search index rebuild to pick up new config
                store._search_index = None
                payload = store.fusion_search_explain("config.yaml", k=1)
                explain = payload["explain"]["flag-test"]
                assert explain["entity_boost"] == 0.0
                assert explain["entity_hits"] == []
            finally:
                _store_mod.plugin_config = original_config
        finally:
            import shutil
            try:
                conn = getattr(store._local, "conn", None)
                if conn:
                    conn.close()
            except Exception:
                pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_compression_disabled_uses_none_level_under_pressure(self):
        """Design intent: compression.enabled=False means context never compresses.

        Even under tight budget, the compression_level should be 'none' or
        'emergency' (budget-exhaustion fallback) but never 'mild'/'aggressive'.
        """
        import importlib.util
        import sys
        from pathlib import Path

        _repo = Path(__file__).resolve().parent.parent

        _spec_store = importlib.util.spec_from_file_location("_store_comp", str(_repo / "core" / "store.py"))
        _store_c = importlib.util.module_from_spec(_spec_store)
        sys.modules["_store_comp"] = _store_c
        _spec_store.loader.exec_module(_store_c)

        _spec_search = importlib.util.spec_from_file_location("_search_comp", str(_repo / "core" / "search.py"))
        _search_c = importlib.util.module_from_spec(_spec_search)
        sys.modules["_search_comp"] = _search_c
        _spec_search.loader.exec_module(_search_c)

        _spec_ctx = importlib.util.spec_from_file_location("_ctx_comp", str(_repo / "memory" / "context.py"))
        _ctx = importlib.util.module_from_spec(_spec_ctx)
        sys.modules["_ctx_comp"] = _ctx
        _spec_ctx.loader.exec_module(_ctx)

        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="hermes_comp_flag_")
        try:
            root = Path(tmpdir) / "memories"
            root.mkdir(parents=True, exist_ok=True)
            db_path = Path(tmpdir) / "memories.db"
            store = _store_c.MemoryStore(user_root=root, db_path=db_path)

            for i in range(5):
                fm = _store_c.MemoryFrontmatter.new(source="test", zone="general")
                store.put("user", fm, f"Overflow memory {i} " + ("content " * 50))

            # Inject config with compression disabled
            store._plugin_config_cache = {
                "mem_reflection_hermes": {"context": {"compression": {"enabled": False}}}
            }

            search = _search_c.SearchIndex(store)

            class _FakeSkills:
                def list(self):
                    return []

            bundle = _ctx.build_context_bundle(store, search, _FakeSkills(), "content", max_tokens=200)

            # With compression disabled, level should be 'none' (or 'emergency' for zero-budget)
            # but never 'mild' or 'aggressive' since those configs are skipped
            assert bundle.debug["compression_level"] in {"none", "emergency"}
        finally:
            import shutil
            try:
                conn = getattr(store._local, "conn", None)
                if conn:
                    conn.close()
            except Exception:
                pass
            shutil.rmtree(tmpdir, ignore_errors=True)
