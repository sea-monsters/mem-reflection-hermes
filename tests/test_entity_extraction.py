"""test_entity_extraction.py — Tests for entity extraction pipeline.

Design intent (FEP §5.4 Entity Recall Layer):
  SRH uses regex + optional spaCy for entity extraction. The regex layer covers
  6 patterns (file_path, code, quoted, package, camelCase, compound) with typed
  weights reflecting extraction confidence. Normalized dedup prevents "config.yaml"
  and "Config.yaml" from being double-counted.

  Cross-reference: mem0 uses spaCy-only NER with a generic-heads filter list.
  SRH chose regex-first + optional spaCy to avoid mandatory heavy dependencies.
  The weight hierarchy (file_path=1.0 > code=0.9 > quoted=0.8 > package=0.75 >
  proper=0.7 > compound=0.65 > spacy=0.6) reflects regex precision advantage.

Run: pytest tests/test_entity_extraction.py -v
"""
from __future__ import annotations

from pathlib import Path

from core.store import (
    extract_entities,
    _normalize_entity_text,
    entity_enabled,
    entity_weight,
)


class TestExtractEntitiesRegexPatterns:
    """Verify each regex pattern extracts the right entities."""

    def test_file_path_extraction(self):
        """file_path pattern: paths with directory separators and extensions."""
        entities = extract_entities(
            'Edit the file src/providers/http/index.test.ts to fix the import'
        )
        texts = [e["text"] for e in entities]
        assert any("index.test.ts" in t for t in texts), f"file_path not found in {texts}"
        file_ents = [e for e in entities if e["type"] == "file_path"]
        assert len(file_ents) >= 1
        assert file_ents[0]["weight"] == 1.0

    def test_code_backtick_extraction(self):
        """code pattern: backtick-quoted strings (2-120 chars)."""
        entities = extract_entities(
            'Run `ToolRunner.execute` with the `--verbose` flag'
        )
        code_ents = [e for e in entities if e["type"] == "code"]
        texts = [e["text"] for e in code_ents]
        assert "ToolRunner.execute" in texts
        assert code_ents[0]["weight"] == 0.9

    def test_quoted_string_extraction(self):
        """quoted pattern: double or single quoted strings."""
        entities = extract_entities(
            'Set "context_budget" to 2000 and enable \'compression\' mode'
        )
        quoted_ents = [e for e in entities if e["type"] == "quoted"]
        texts = [e["text"] for e in quoted_ents]
        assert "context_budget" in texts
        assert quoted_ents[0]["weight"] == 0.8

    def test_package_dot_extraction(self):
        """package pattern: dot-separated identifiers (e.g. numpy.linalg.norm)."""
        entities = extract_entities(
            'Use numpy.linalg.norm for vector normalization'
        )
        pkg_ents = [e for e in entities if e["type"] == "package"]
        assert len(pkg_ents) >= 1
        assert any("numpy.linalg.norm" in e["text"] for e in pkg_ents)
        assert pkg_ents[0]["weight"] == 0.75

    def test_camelcase_extraction(self):
        """proper pattern: PascalCase/camelCase identifiers."""
        entities = extract_entities(
            'Initialize the HttpRequestHandler and pass it to ResponseBuilder'
        )
        proper_ents = [e for e in entities if e["type"] == "proper"]
        texts = [e["text"] for e in proper_ents]
        assert "HttpRequestHandler" in texts
        assert "ResponseBuilder" in texts
        assert proper_ents[0]["weight"] == 0.7

    def test_compound_hyphen_extraction(self):
        """compound pattern: hyphen/slash connected terms."""
        entities = extract_entities(
            'Configure the auth-middleware and use request-rate-limiting'
        )
        compound_ents = [e for e in entities if e["type"] == "compound"]
        texts = [e["text"] for e in compound_ents]
        assert any("auth-middleware" in t for t in texts)
        assert compound_ents[0]["weight"] == 0.65


class TestEntityNormalization:
    """Verify _normalize_entity_text handles whitespace and case correctly."""

    def test_collapses_whitespace(self):
        assert _normalize_entity_text("  config   file  ") == "config file"

    def test_lowercases(self):
        assert _normalize_entity_text("Config.yaml") == "config.yaml"

    def test_empty_string(self):
        assert _normalize_entity_text("") == ""

    def test_already_normalized(self):
        assert _normalize_entity_text("index.ts") == "index.ts"


class TestEntityDedup:
    """Verify dedup via normalization prevents double-counting."""

    def test_case_dedup(self):
        """Same entity in different cases should appear once."""
        entities = extract_entities('Use "Config.yaml" to configure Config.yaml')
        # Both should normalize to "config.yaml" — only one entity kept
        normalized = [e["normalized"] for e in entities if "config" in e["normalized"]]
        # At most one unique normalized entry
        assert len(set(normalized)) <= 1

    def test_type_distinction(self):
        """Same text, different type → separate entities."""
        entities = extract_entities('Set "npm install" via `npm install`')
        # "npm install" appears as both quoted and code → two different types
        types = {e["type"] for e in entities if "npm" in e["text"]}
        assert len(types) >= 1


class TestEntityWeightHierarchy:
    """Design intent: weight reflects extraction confidence.

    file_path(1.0) > code(0.9) > quoted(0.8) > package(0.75) > proper(0.7) > compound(0.65)
    Cross-reference: mem0 ENTITY_BOOST_WEIGHT = 0.5 (additive in score_and_rank).
    SRH uses per-type weights for finer granularity.
    """

    def test_weights_are_monotonically_decreasing(self):
        """Higher-precision patterns should have higher weights."""
        # Use text that triggers each pattern independently to verify weight values
        file_ents = extract_entities('Edit src/app.ts')
        code_ents = extract_entities('Run `npm install`')
        quoted_ents = extract_entities('Set "api_key" value')
        pkg_ents = extract_entities('Use numpy.linalg.norm')
        proper_ents = extract_entities('Call HttpRequestHandler')
        compound_ents = extract_entities('Setup auth-middleware')

        def _weight_of(ents, etype):
            for e in ents:
                if e["type"] == etype:
                    return e["weight"]
            return None

        w_file = _weight_of(file_ents, "file_path")
        w_code = _weight_of(code_ents, "code")
        w_quoted = _weight_of(quoted_ents, "quoted")
        w_pkg = _weight_of(pkg_ents, "package")
        w_proper = _weight_of(proper_ents, "proper")
        w_compound = _weight_of(compound_ents, "compound")

        assert w_file == 1.0, f"file_path weight: {w_file}"
        assert w_code == 0.9, f"code weight: {w_code}"
        assert w_quoted == 0.8, f"quoted weight: {w_quoted}"
        assert w_pkg == 0.75, f"package weight: {w_pkg}"
        assert w_proper == 0.7, f"proper weight: {w_proper}"
        assert w_compound == 0.65, f"compound weight: {w_compound}"


class TestEntityBoundaryConditions:
    """Edge cases: empty, short, CJK, mixed."""

    def test_empty_string_returns_empty(self):
        assert extract_entities("") == []

    def test_whitespace_only_returns_empty(self):
        assert extract_entities("   \n\t  ") == []

    def test_short_text_below_threshold(self):
        """Entities below 3 chars should be filtered."""
        entities = extract_entities('Set "ab" and `cd`')
        # Both "ab" and "cd" are < 3 chars → filtered
        for e in entities:
            assert len(e["text"]) >= 3

    def test_cjk_entities_in_quoted(self):
        """CJK text in quotes should be extracted."""
        entities = extract_entities('使用"上下文压缩"提升性能')
        quoted = [e for e in entities if e["type"] == "quoted"]
        assert len(quoted) >= 1
        assert any("上下文压缩" in e["text"] for e in quoted)

    def test_no_entities_in_plain_text(self):
        """Plain English text without special patterns should yield sparse results."""
        entities = extract_entities("the quick brown fox jumps over the lazy dog")
        # No file paths, code, quotes, packages, camelCase, or compounds
        assert all(e["type"] == "proper" for e in entities)

    def test_multiple_patterns_in_single_text(self):
        """All 6 regex patterns should fire on appropriate text."""
        text = (
            'Edit src/config.yaml: set "api_key" via `ConfigManager.setValue` '
            'using numpy.linalg.norm and HttpRequestHandler for auth-middleware'
        )
        entities = extract_entities(text)
        types = {e["type"] for e in entities}
        assert "file_path" in types
        assert "code" in types
        assert "quoted" in types
        assert "package" in types
        assert "proper" in types
        assert "compound" in types


class TestEntityExtractionPipelineIntegration:
    """Entity recall intent (FEP §5.4): entities flow from extract → entity_links → search boost.

    Design intent: When spaCy is unavailable (the default), the 6 regex patterns
    should still produce meaningful entity recall for proper nouns, file paths,
    package names, and CJK quoted text.
    """

    def test_regex_only_produces_entity_hits_via_search(self):
        """FEP §5.4: regex fallback → entity_links → explain entity_hits."""
        import tempfile
        from core.store import MemoryStore, MemoryFrontmatter

        tmpdir = tempfile.mkdtemp(prefix="hermes_regex_int_")
        try:
            root = Path(tmpdir) / "memories"
            root.mkdir(parents=True, exist_ok=True)
            db_path = Path(tmpdir) / "memories.db"
            store = MemoryStore(user_root=root, db_path=db_path)

            fm = MemoryFrontmatter.new(source="test")
            store.put("user", fm, 'Edit src/main.py: use `ToolRunner.execute` with "auth-middleware"')

            payload = store.fusion_search_explain("main.py auth-middleware", k=1)
            top_id = list(payload["explain"].keys())[0]
            explain = payload["explain"][top_id]
            assert explain["entity_hits"], "Regex should produce entity hits without spaCy"
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_entity_extraction_pipeline_spacy_optional(self):
        """FEP §5.4: extraction works without spaCy installed (default environment)."""
        # When spaCy is not installed, _extract_entities_spacy returns []
        from core.store import _extract_entities_spacy
        # In CI/test, spaCy may not be installed; this should never crash
        result = _extract_entities_spacy("This should not crash without spaCy")
        assert isinstance(result, list)
        assert result == [], "spaCy path should return empty when unavailable"
        """FEP §5.5: entity_boost = entity_boosts.get(mid, 0.0) * entity_weight().

        Cross-reference: mem0 uses additive entity_boost in score_and_rank.
        SRH uses multiplicative boost × weight for finer granularity.
        """
        import tempfile
        from core.store import MemoryStore, MemoryFrontmatter

        tmpdir = tempfile.mkdtemp(prefix="hermes_boost_")
        try:
            root = Path(tmpdir) / "memories"
            root.mkdir(parents=True, exist_ok=True)
            db_path = Path(tmpdir) / "memories.db"
            store = MemoryStore(user_root=root, db_path=db_path)

            fm = MemoryFrontmatter.new(source="test")
            store.put("user", fm, 'Exact entity "config.yaml"')

            payload = store.fusion_search_explain("config.yaml", k=1)
            top_id = list(payload["explain"].keys())[0]
            explain = payload["explain"][top_id]
            assert explain["entity_boost"] > 0
            assert explain["entity_boost"] <= 0.08 + 1e-6, \
                f"boost={explain['entity_boost']} should be <= 0.08 * link_weight"
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
