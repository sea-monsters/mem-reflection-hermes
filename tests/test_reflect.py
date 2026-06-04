"""test_reflect.py — Tests for ReflectionEngine.

Coverage:
- _is_memorable_content content gate
- _extract_facts_from_turn heuristic extraction
- micro reflection: raw_chunk and heuristic modes
- full reflection: raw_chunk and LLM fallback
- audit / log / recent helpers
- Empty / boundary conditions

Run: pytest tests/test_reflect.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

_spec_store = importlib.util.spec_from_file_location("_memory_store_module", str(_REPO / "store.py"))
_store_module = importlib.util.module_from_spec(_spec_store)
sys.modules["_memory_store_module"] = _store_module
_spec_store.loader.exec_module(_store_module)
MemoryStore = _store_module.MemoryStore
MemoryFrontmatter = _store_module.MemoryFrontmatter

_spec_search = importlib.util.spec_from_file_location("_memory_search_module", str(_REPO / "search.py"))
_search_module = importlib.util.module_from_spec(_spec_search)
sys.modules["_memory_search_module"] = _search_module
_spec_search.loader.exec_module(_search_module)
SearchIndex = _search_module.SearchIndex

_spec_graph = importlib.util.spec_from_file_location("_memory_graph_module", str(_REPO / "graph.py"))
_graph_module = importlib.util.module_from_spec(_spec_graph)
sys.modules["_memory_graph_module"] = _graph_module
_spec_graph.loader.exec_module(_graph_module)
GraphIndex = _graph_module.GraphIndex

_spec_reflect = importlib.util.spec_from_file_location("_memory_reflection_module", str(_REPO / "reflect.py"))
_reflection_module = importlib.util.module_from_spec(_spec_reflect)
sys.modules["_memory_reflection_module"] = _reflection_module
_spec_reflect.loader.exec_module(_reflection_module)
ReflectionEngine = _reflection_module.ReflectionEngine
_is_memorable_content = _reflection_module._is_memorable_content
_extract_facts_from_turn = _reflection_module._extract_facts_from_turn
_append_reflect_log = _reflection_module._append_reflect_log
_read_reflect_log = _reflection_module._read_reflect_log


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_engine():
    tmpdir = tempfile.mkdtemp(prefix="hermes_reflect_")
    root = Path(tmpdir) / "memories"
    root.mkdir(parents=True, exist_ok=True)
    db_path = Path(tmpdir) / "memories.db"
    store = MemoryStore(user_root=root, db_path=db_path)
    search = SearchIndex(store)
    graph_db = Path(tmpdir) / "graph.db"
    graph = GraphIndex(graph_db)
    engine = ReflectionEngine(store, search, graph, log_path=Path(tmpdir) / "reflect-log.jsonl")
    yield engine
    try:
        conn = getattr(store._local, "conn", None)
        if conn is not None:
            conn.close()
    except Exception:
        pass
    graph.close()
    import shutil as _shutil
    import time as _time
    for _attempt in range(5):
        try:
            _shutil.rmtree(tmpdir)
            break
        except PermissionError:
            _time.sleep(0.1)
    _shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Content gate
# ---------------------------------------------------------------------------

class TestIsMemorableContent:
    def test_too_short_rejected(self):
        assert not _is_memorable_content("hi")
        assert not _is_memorable_content("short")

    def test_tool_output_rejected(self):
        assert not _is_memorable_content("```python\nprint(1)\n```")
        assert not _is_memorable_content("Exit code: 0\nstdout: hello")

    def test_file_path_rejected(self):
        assert not _is_memorable_content("The file is at /home/user/test.py")
        assert not _is_memorable_content("Check C:\\Users\\Huang\\test.txt")

    def test_code_pattern_rejected(self):
        assert not _is_memorable_content("def hello():\n    pass")
        assert not _is_memorable_content("class Foo:\n    pass")

    def test_repetitive_rejected(self):
        assert not _is_memorable_content("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    def test_normal_text_accepted(self):
        assert _is_memorable_content("User prefers dark mode in all applications.")
        assert _is_memorable_content("The meeting discussed Q4 roadmap and hiring plans.")


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------

class TestExtractFacts:
    def test_explicit_intent(self):
        facts = _extract_facts_from_turn("请记住用户喜欢深色模式", "好的")
        assert len(facts) >= 1
        assert any(f["source"] == "explicit_intent" for f in facts)

    def test_correction(self):
        facts = _extract_facts_from_turn("纠正一下，用户喜欢的是浅色模式", "收到")
        assert len(facts) >= 1
        assert any(f["source"] == "correction" for f in facts)

    def test_preference_pattern(self):
        facts = _extract_facts_from_turn("我喜欢用 Go 因为性能很好", "")
        assert any(f["source"] == "preference" for f in facts)

    def test_no_facts_for_empty(self):
        facts = _extract_facts_from_turn("", "")
        assert facts == []

    def test_deduplication(self):
        facts = _extract_facts_from_turn("请记住深色模式", "请记住深色模式")
        # Two identical sentences should be deduplicated
        texts = [f["text"] for f in facts]
        assert len(texts) == len(set(texts))


# ---------------------------------------------------------------------------
# Micro reflection
# ---------------------------------------------------------------------------

class TestMicroReflection:
    def test_raw_chunk_stores_episode(self, temp_engine):
        engine = temp_engine
        engine._mode = "raw_chunk"
        result = engine.micro(None, "user msg", "assistant reply")
        assert result is not None
        assert result["type"] == "raw_chunk"
        # Should be stored in episode zone
        mems = engine.store.list_active()
        assert any(m.frontmatter.zone == "episode" for m in mems)

    def test_raw_chunk_skips_short_content(self, temp_engine):
        engine = temp_engine
        engine._mode = "raw_chunk"
        result = engine.micro(None, "hi", "ok")
        assert result is None

    def test_raw_chunk_skips_non_memorable(self, temp_engine):
        engine = temp_engine
        engine._mode = "raw_chunk"
        result = engine.micro(None, "```\nprint(1)\n```", "")
        assert result is None

    def test_heuristic_extracts_facts(self, temp_engine):
        engine = temp_engine
        engine._mode = "heuristic"
        result = engine.micro(None, "请记住用户喜欢深色模式", "好的")
        assert result is not None
        assert result["type"] == "fact"

    def test_heuristic_no_facts_returns_none(self, temp_engine):
        engine = temp_engine
        engine._mode = "heuristic"
        result = engine.micro(None, "hello", "hi")
        assert result is None


# ---------------------------------------------------------------------------
# Full reflection
# ---------------------------------------------------------------------------

class TestFullReflection:
    def test_raw_chunk_mode(self, temp_engine):
        engine = temp_engine
        engine._mode = "raw_chunk"
        messages = [
            {"role": "user", "content": "Remember that I like Go"},
            {"role": "assistant", "content": "Got it, you like Go."},
        ]
        result = engine.full(None, messages)
        assert "summary" in result
        assert len(result["accepted"]) >= 1

    def test_raw_chunk_skips_tool_messages(self, temp_engine):
        engine = temp_engine
        engine._mode = "raw_chunk"
        messages = [
            {"role": "tool", "content": "tool output"},
            {"role": "assistant", "content": "Done."},
        ]
        result = engine.full(None, messages)
        assert result["summary"] is not None

    def test_full_llm_fallback_when_no_ctx(self, temp_engine):
        engine = temp_engine
        engine._mode = "full"
        messages = [
            {"role": "user", "content": "Remember that I like dark mode"},
        ]
        # ctx=None should fallback to raw_chunk
        result = engine.full(None, messages)
        assert "summary" in result

    def test_empty_messages(self, temp_engine):
        engine = temp_engine
        result = engine.full(None, [])
        assert result is not None


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

class TestReflectLog:
    def test_append_and_read(self, temp_engine):
        engine = temp_engine
        engine.log({"test": "entry", "value": 42})
        recent = engine.recent(n=5)
        assert len(recent) >= 1
        assert recent[-1]["test"] == "entry"

    def test_recent_n_limit(self, temp_engine):
        engine = temp_engine
        for i in range(10):
            engine.log({"idx": i})
        recent = engine.recent(n=3)
        assert len(recent) == 3
        # Should be the last 3 entries
        assert recent[-1]["idx"] == 9

    def test_audit_structure(self, temp_engine):
        engine = temp_engine
        candidate = {"id": "test-123"}
        audit = engine.audit(candidate, "accepted", "good quality")
        assert audit["candidate_id"] == "test-123"
        assert audit["decision"] == "accepted"
        assert audit["reason"] == "good quality"
        assert "timestamp" in audit
