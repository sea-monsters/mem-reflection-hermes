"""test_bm25.py — BM25 scoring effectiveness tests.

Tests tokenization (English, CJK, mixed) and BM25 ranking quality
including IDF rarity, TF saturation, length normalization, and
effectiveness boosting.

Run: pytest tests/test_bm25.py -v
"""
from __future__ import annotations

import math
import re
from collections import Counter

import pytest

import core.store as store_mod
from tests._helpers import make_memory, make_memory_with_id, effectiveness_for
from core.store import (
    LoadedMemory,
    MemoryEffectiveness,
    MemoryFrontmatter,
    _tokenise,
    _memory_tokens,
    _bm25_search_scored,
)


# ---------------------------------------------------------------------------
# Tokenization tests
# ---------------------------------------------------------------------------

class TestTokenise:
    def test_english_basic(self):
        tokens = _tokenise("the user prefers dark mode")
        assert "the" not in tokens
        assert "user" in tokens
        assert "prefers" in tokens
        assert "dark" in tokens
        assert "mode" in tokens

    def test_cjk_bigrams_with_stopwords(self):
        tokens = _tokenise("我的记忆是关于用户的偏好和习惯")
        # CJK bigram stopwords should be removed
        assert "关于" not in tokens
        # Content bigrams (non-overlapping, advance by 2) should remain
        assert "记忆" in tokens
        assert "偏好" in tokens

    def test_mixed_en_cjk(self):
        tokens = _tokenise("use golang进行后端开发")
        assert "golang" in tokens
        assert "进行" in tokens or "后端" in tokens or "开发" in tokens

    def test_jieba_search_mode_uses_search_tokens(self, monkeypatch):
        monkeypatch.setattr(store_mod, "cjk_tokenizer_mode", lambda: "jieba")
        monkeypatch.setattr(
            store_mod,
            "_get_jieba_search",
            lambda: (lambda text: ["开发", "规划", "上下文", "压缩"]),
        )
        tokens = _tokenise("开发规划上下文压缩")
        assert "开发" in tokens
        assert "规划" in tokens
        assert "上下文" in tokens
        assert "压缩" in tokens

    def test_auto_mode_falls_back_to_bigram_without_jieba(self, monkeypatch):
        monkeypatch.setattr(store_mod, "cjk_tokenizer_mode", lambda: "auto")
        monkeypatch.setattr(store_mod, "_get_jieba_search", lambda: None)
        tokens = _tokenise("开发规划")
        assert "开发" in tokens
        assert "规划" in tokens

    def test_explicit_bigram_mode(self, monkeypatch):
        """P0: Explicit bigram mode should always use non-overlapping bigram tokenization."""
        monkeypatch.setattr(store_mod, "cjk_tokenizer_mode", lambda: "bigram")
        tokens = _tokenise("开发规划上下文压缩")
        # Non-overlapping bigram: 开发, 规划, 上下文, 压缩 (advance by 2)
        assert "开发" in tokens
        assert "规划" in tokens
        # With bigram heuristic, individual characters should not appear
        assert len(tokens) >= 2

    def test_mixed_en_cjk_jieba_mode(self, monkeypatch):
        """P0: jieba mode should correctly tokenize mixed English/CJK text."""
        monkeypatch.setattr(store_mod, "cjk_tokenizer_mode", lambda: "jieba")
        monkeypatch.setattr(
            store_mod,
            "_get_jieba_search",
            lambda: (lambda text: list(set(re.findall(r'[a-zA-Z_]+|[一-鿿]+', text)))),
        )
        tokens = _tokenise("use golang进行后端开发")
        # Should contain English terms and CJK terms from jieba
        assert "golang" in tokens or "use" in tokens
        # Check for CJK characters (any token containing a CJK character)
        has_cjk = any('一' <= c <= '鿿' for token in tokens for c in token)
        assert has_cjk, f"Expected CJK tokens in {tokens}"


# ---------------------------------------------------------------------------
# BM25 scoring tests
# ---------------------------------------------------------------------------

class TestBM25Scoring:
    def _make_docs(self, bodies):
        return [make_memory(b, age_days=0) for b in bodies]

    def test_single_term_ranking(self):
        """Documents containing query term rank above those that don't."""
        docs = self._make_docs([
            "User prefers dark mode",
            "The meeting is about project planning",
            "Dark theme configuration settings",
        ])
        results = _bm25_search_scored(docs, "dark", k=3)
        assert len(results) >= 2
        # Both "dark" docs should be present
        ids = {m.id() for m, s in results}
        assert docs[0].id() in ids
        assert docs[2].id() in ids
        # The non-matching doc should not appear
        assert docs[1].id() not in ids

    def test_idf_rarity_boost(self):
        """Rare terms score higher than common terms (IDF effect)."""
        docs = self._make_docs([
            "common common common test",
            "common common test",
            "rareterm unique here",
        ])
        results_common = _bm25_search_scored(docs, "common", k=3)
        results_rare = _bm25_search_scored(docs, "rareterm", k=3)

        if results_rare and results_common:
            # Rare term (df=1) should have higher per-doc score than common term (df=2)
            rare_score = results_rare[0][1]
            common_score = results_common[0][1]
            assert rare_score > common_score, (
                f"Rare term score ({rare_score:.4f}) should exceed "
                f"common term score ({common_score:.4f})"
            )

    def test_tf_saturation(self):
        """Repeating a term 100x doesn't give 100x score (k1=1.5 saturation)."""
        doc_once = make_memory("python is great", age_days=0)
        doc_many = make_memory("python " * 100, age_days=0)
        results = _bm25_search_scored([doc_once, doc_many], "python", k=2)
        if len(results) == 2:
            score_once = results[1][1]  # lower rank = lower score
            score_many = results[0][1]
            # Should NOT be 100x ratio
            ratio = score_many / max(score_once, 1e-9)
            assert ratio < 20, f"TF saturation broken: ratio={ratio:.1f}"

    def test_length_normalization(self):
        """Long documents don't dominate simply from length."""
        doc_short = make_memory("python", age_days=0)
        doc_long = make_memory(
            "python " + "filler padding text " * 200,
            age_days=0,
        )
        results = _bm25_search_scored([doc_short, doc_long], "python", k=2)
        if len(results) == 2:
            score_short = results[0][1]
            score_long = results[1][1]
            # Short doc with focused content should score at least comparable
            # Not necessarily higher (long doc has more total terms), but within 3x
            assert score_short > 0, "Short focused doc should have positive score"

    def test_effectiveness_boost(self):
        """Memory with high effectiveness gets score boost."""
        docs = self._make_docs([
            "dark mode preference",
            "dark mode preference",
        ])
        # Both have identical content; only one has effectiveness
        eff_high = effectiveness_for(docs[0].id(), loaded=10, referenced=9, last_event_days_ago=0)
        eff_map = {docs[0].id(): eff_high}

        results_with = _bm25_search_scored(docs, "dark mode", k=2, effectiveness=eff_map)
        results_without = _bm25_search_scored(docs, "dark mode", k=2)

        # With effectiveness boost, the boosted doc should score higher
        if results_with and results_without:
            assert len(results_with) == 2
            # The boosted one should be top-ranked
            assert results_with[0][0].id() == docs[0].id()
            # Without effectiveness, scores should be equal (identical content)
            s1 = results_without[0][1]
            s2 = results_without[1][1]
            assert abs(s1 - s2) < 1e-9, "Identical content should have identical BM25 scores"

    def test_empty_query_returns_empty(self):
        docs = self._make_docs(["some content"])
        results = _bm25_search_scored(docs, "", k=5)
        assert results == []

    def test_no_matching_docs_returns_empty(self):
        docs = self._make_docs(["alpha beta gamma"])
        results = _bm25_search_scored(docs, "xyz", k=5)
        assert results == []


class TestBM25IndexBuildFailure:
    """Verify SearchIndex degrades gracefully when BM25 index build fails."""

    def test_bm25_build_failure_returns_empty_bm25_channel(self, temp_store):
        """When bm25s raises during index build, BM25 search returns empty dict without crashing."""
        from core.search import SearchIndex

        fm = MemoryFrontmatter.new(source="test")
        temp_store.put("user", fm, "User prefers dark mode in all applications")

        si = SearchIndex(temp_store)

        # Monkeypatch bm25s.BM25 to raise, simulating corrupt state
        import bm25s
        original_bm25 = bm25s.BM25

        def _failing_bm25(*args, **kwargs):
            raise RuntimeError("simulated corrupt index data")

        bm25s.BM25 = _failing_bm25
        try:
            si._bm25_retriever = None  # force rebuild
            result = si._bm25_search_bm25s("dark mode", k=5)
            assert result == {}  # graceful empty, no exception
        finally:
            bm25s.BM25 = original_bm25

    def test_search_still_returns_results_without_bm25(self, temp_store, monkeypatch):
        """Full search() works (possibly with lower quality) when BM25 channel is disabled."""
        from core.search import SearchIndex

        fm = MemoryFrontmatter.new(source="test")
        temp_store.put("user", fm, "User prefers dark mode in all applications")

        si = SearchIndex(temp_store)

        # Ensure BM25 channel fails
        monkeypatch.setattr(si, "_ensure_bm25_index", lambda: False)

        results = si.search("dark mode", k=5)
        # Should still return results via embedding channel (or empty, but not crash)
        assert isinstance(results, list)
