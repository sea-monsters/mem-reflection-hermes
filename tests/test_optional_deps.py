"""test_optional_deps.py — Tests for optional dependency fallback paths.

Coverage:
- python-frontmatter unavailable (serialize_frontmatter fallback)
- tiktoken unavailable (_estimate_tokens fallback)
- spaCy unavailable (_extract_entities_spacy returns [])
- jieba unavailable (CJK tokenizer fallback to bigram)
- ONNX Runtime unavailable (_get_onnx_session fallback)

Run: pytest tests/test_optional_deps.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


class TestFrontmatterFallback:
    """Test frontmatter serialization when python-frontmatter is not installed."""

    def test_serialize_without_frontmatter_module(self, monkeypatch):
        """_HAS_FRONTMATTER=False should use inline YAML serialization."""
        # Load store module in a namespace where frontmatter is unavailable
        _pkg = "mem_reflection_hermes_no_fm"
        pkg = types.ModuleType(_pkg)
        pkg.__path__ = [str(_REPO)]
        sys.modules[_pkg] = pkg

        core_mod = types.ModuleType(f"{_pkg}.core")
        core_mod.__path__ = [str(_REPO / "core")]
        sys.modules[f"{_pkg}.core"] = core_mod

        spec = importlib.util.spec_from_file_location(f"{_pkg}.core.store", str(_REPO / "core" / "store.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_pkg}.core.store"] = mod

        # Simulate frontmatter being unavailable
        monkeypatch.setitem(sys.modules, "frontmatter", None)
        spec.loader.exec_module(mod)

        fm = mod.MemoryFrontmatter.new(source="test")
        serialized = mod.serialize_frontmatter(
            {"id": fm.id, "created": fm.created, "source": "test"},
            "body text",
        )
        assert "---" in serialized
        assert "id:" in serialized
        assert "body text" in serialized


class TestTiktokenFallback:
    def test_estimate_tokens_without_tiktoken(self, monkeypatch):
        """When tiktoken is unavailable, estimation falls back to byte-length heuristic."""
        spec = importlib.util.spec_from_file_location("_store_tiktoken", str(_REPO / "core" / "store.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_store_tiktoken"] = mod
        monkeypatch.setitem(sys.modules, "tiktoken", None)
        spec.loader.exec_module(mod)

        # CJK text: 3 bytes per token
        cjk_tokens = mod.estimate_tokens("上下文压缩")
        # Latin text: 4 bytes per token
        latin_tokens = mod.estimate_tokens("hello world")

        assert cjk_tokens > 0
        assert latin_tokens > 0
        # CJK should be estimated with lower divisor (more tokens per char)
        assert cjk_tokens >= len("上下文压缩")


class TestSpacyFallback:
    def test_extract_entities_spacy_returns_empty_when_unavailable(self, monkeypatch):
        """_extract_entities_spacy should return [] when spaCy is not installed."""
        spec = importlib.util.spec_from_file_location("_store_spacy", str(_REPO / "core" / "store.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_store_spacy"] = mod
        monkeypatch.setitem(sys.modules, "spacy", None)
        spec.loader.exec_module(mod)

        result = mod._extract_entities_spacy("This should not crash without spaCy")
        assert result == []


class TestJiebaFallback:
    def test_tokenize_without_jieba_uses_bigram(self, monkeypatch):
        """When jieba is unavailable, CJK text should be tokenized as bigrams."""
        spec = importlib.util.spec_from_file_location("_store_jieba", str(_REPO / "core" / "store.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_store_jieba"] = mod
        monkeypatch.setitem(sys.modules, "jieba", None)
        spec.loader.exec_module(mod)

        tokens = mod._tokenise("上下文压缩功能")
        # Without jieba, should still produce some tokens (bigram fallback)
        assert len(tokens) > 0
        # Bigram mode produces 2-char overlapping slices
        assert all(len(t) <= 2 for t in tokens)


class TestOnnxFallback:
    def test_get_onnx_session_returns_none_when_unavailable(self, monkeypatch):
        """_get_onnx_session should return (None, None) when optimum is not installed."""
        spec = importlib.util.spec_from_file_location("_search_onnx", str(_REPO / "core" / "search.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_search_onnx"] = mod
        # Remove optimum from available modules
        monkeypatch.setitem(sys.modules, "optimum", None)
        monkeypatch.setitem(sys.modules, "optimum.onnxruntime", None)
        spec.loader.exec_module(mod)

        session, tokenizer = mod._get_onnx_session()
        assert session is None
        assert tokenizer is None
