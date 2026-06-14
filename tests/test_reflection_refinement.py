"""Phase B tests for refined reflection extraction."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from mem_reflection_hermes.reflection import runtime as _runtime


class _RecordingMemStore:
    def __init__(self):
        self.list_active_calls = []
        self.check_conflict_calls = []
        self.put_calls = []

    def list_active(self, filters=None):
        self.list_active_calls.append(filters)
        return []

    def check_conflict(self, body, exclude_ids=None, filters=None):
        self.check_conflict_calls.append({
            "body": body,
            "exclude_ids": list(exclude_ids or []),
            "filters": filters,
        })
        return None

    def put(self, scope, fm, body):
        self.put_calls.append({
            "scope": scope,
            "frontmatter": fm,
            "body": body,
        })
        return SimpleNamespace(path=f"/tmp/{fm.id}.md")

    def get(self, _memory_id):
        return object()


def _load_refined_extractor():
    try:
        from mem_reflection_hermes.reflection import extraction as _extraction
    except ImportError as exc:  # pragma: no cover - explicit red failure path
        pytest.fail(f"refined extraction module is missing: {exc}")
    return _extraction


def test_refined_extraction_classifies_decision_and_todo():
    extraction = _load_refined_extractor()

    candidates = extraction.extract_refined_memory_candidates(
        "我们决定以后统一用 git status 做检查。TODO: 补上 scope tests。",
        "",
    )

    kinds = [c.get("kind") for c in candidates]
    assert "decision" in kinds
    assert "todo" in kinds
    assert candidates[0]["priority"] <= candidates[1]["priority"]


def test_embedding_reflection_preserves_refined_candidate_kinds(monkeypatch):
    store = _RecordingMemStore()
    monkeypatch.setattr(_runtime, "_get_mem_store", lambda: store)
    monkeypatch.setattr(_runtime, "_get_skill_store", lambda: SimpleNamespace(list=lambda: []))
    monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_save_pending_skill_candidates", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)
    monkeypatch.setattr(_runtime, "_reflection_mode", lambda: "embedding")

    result = _runtime._run_embedding_reflection([
        {"role": "user", "content": "我们决定以后统一用 git status 做检查。TODO: 补上 scope tests。"},
        {"role": "assistant", "content": "收到。"},
    ])

    accepted = result["accepted_memories"]
    assert len(accepted) >= 2
    kinds = [item.get("kind") for item in accepted]
    assert "decision" in kinds
    assert "todo" in kinds


def test_micro_reflection_returns_richer_fact_metadata(monkeypatch):
    extraction = _load_refined_extractor()
    candidates = extraction.extract_refined_memory_candidates(
        "我们决定以后统一用 git status 做检查。",
        "并且 TODO: 补上 scope tests。",
    )

    assert any(c.get("kind") == "decision" for c in candidates)
    assert any(c.get("kind") == "todo" for c in candidates)
    assert all("priority" in c for c in candidates)


# ---------------------------------------------------------------------------
# P2-1: kind vocabulary normalization + LLM-mode kind propagation.
# ---------------------------------------------------------------------------

def test_normalize_memory_kind_canonicalizes_known_and_unknown():
    extraction = _load_refined_extractor()

    assert extraction.normalize_memory_kind("preference") == "preference"
    assert extraction.normalize_memory_kind("Decision") == "decision"
    # Unknown / missing kinds fall back to "fact" rather than poisoning the
    # typed sidecar's kind column.
    assert extraction.normalize_memory_kind("weather") == "fact"
    assert extraction.normalize_memory_kind(None) == "fact"
    assert extraction.normalize_memory_kind("") == "fact"


def test_reflect_schema_includes_kind_enum():
    schema = _runtime._build_reflect_schema()
    mem_item = schema["properties"]["memory_candidates"]["items"]
    assert "kind" in mem_item["properties"]
    enum = set(mem_item["properties"]["kind"]["enum"])
    # LLM-facing enum must cover the typed categories but exclude the
    # pipeline-internal kinds (summary/raw_chunk) the model cannot emit.
    assert {"fact", "preference", "decision", "policy", "todo", "correction", "intent"} <= enum
    assert "summary" not in enum
    assert "raw_chunk" not in enum


def test_llm_full_reflection_propagates_kind_to_sidecar(monkeypatch):
    """P2-1: in LLM mode the candidate's kind must reach the typed sidecar
    instead of silently defaulting to "fact". Verifies the prompt+schema+path
    fix end to end."""
    class _RecordingGraph:
        def __init__(self):
            self.records = []

        def record_typed_fact(self, *args, **kwargs):
            self.records.append(kwargs)
            return "fid"

        def record_entity_mentions(self, *args, **kwargs):
            return []

        def invalidate_facts_for_memories(self, *_args, **_kwargs):
            return 0

    class _GraphedStore(_RecordingMemStore):
        def __init__(self, graph):
            super().__init__()
            self._graph = graph

    class _FakeResult:
        def __init__(self, parsed):
            self.parsed = parsed
            self.text = "{}"

    class _FakeLLM:
        def __init__(self, parsed):
            self._parsed = parsed

        def complete_structured(self, **_kwargs):
            return _FakeResult(self._parsed)

    graph = _RecordingGraph()
    store = _GraphedStore(graph)
    monkeypatch.setattr(_runtime, "_get_mem_store", lambda: store)
    monkeypatch.setattr(_runtime, "_get_skill_store", lambda: SimpleNamespace(list=lambda: []))
    monkeypatch.setattr(_runtime, "_reflection_mode", lambda: "llm")
    monkeypatch.setattr(_runtime, "_validate_supersedes_targets", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_save_pending_skill_candidates", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)

    ctx = SimpleNamespace(
        llm=_FakeLLM({
            "summary": "ok",
            "memory_candidates": [{
                "fact": "从现在起代码必须先跑 lint。",
                "scope": "user",
                "kind": "policy",
                "confidence": "high",
                "tags": ["lint"],
            }],
            "skill_candidates": [],
            "conflicts": [],
        }),
    )

    _runtime._run_full_reflection(ctx, [{"role": "user", "content": "定个规则"}])

    assert store.put_calls
    # The sidecar must record the real kind from the LLM, not "fact".
    assert any(rec.get("kind") == "policy" for rec in graph.records)


def test_llm_full_reflection_falls_back_unknown_kind_to_fact(monkeypatch):
    """P2-1: an out-of-vocabulary kind from the model must normalize to "fact"
    so the sidecar kind column never carries an arbitrary string."""
    class _RecordingGraph:
        def __init__(self):
            self.records = []

        def record_typed_fact(self, *args, **kwargs):
            self.records.append(kwargs)
            return "fid"

        def record_entity_mentions(self, *args, **kwargs):
            return []

        def invalidate_facts_for_memories(self, *_args, **_kwargs):
            return 0

    class _GraphedStore(_RecordingMemStore):
        def __init__(self, graph):
            super().__init__()
            self._graph = graph

    class _FakeResult:
        def __init__(self, parsed):
            self.parsed = parsed
            self.text = "{}"

    class _FakeLLM:
        def __init__(self, parsed):
            self._parsed = parsed

        def complete_structured(self, **_kwargs):
            return _FakeResult(self._parsed)

    graph = _RecordingGraph()
    store = _GraphedStore(graph)
    monkeypatch.setattr(_runtime, "_get_mem_store", lambda: store)
    monkeypatch.setattr(_runtime, "_get_skill_store", lambda: SimpleNamespace(list=lambda: []))
    monkeypatch.setattr(_runtime, "_reflection_mode", lambda: "llm")
    monkeypatch.setattr(_runtime, "_validate_supersedes_targets", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_save_pending_skill_candidates", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)

    ctx = SimpleNamespace(
        llm=_FakeLLM({
            "summary": "ok",
            "memory_candidates": [{
                "fact": "some statement",
                "scope": "user",
                "kind": "weather_report",  # not in vocabulary
                "confidence": "medium",
                "tags": [],
            }],
            "skill_candidates": [],
            "conflicts": [],
        }),
    )

    _runtime._run_full_reflection(ctx, [{"role": "user", "content": "note"}])

    assert store.put_calls
    assert graph.records, "sidecar must still be written"
    assert all(rec.get("kind") == "fact" for rec in graph.records)
