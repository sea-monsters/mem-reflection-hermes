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
