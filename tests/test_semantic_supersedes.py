"""Semantic supersedes regression tests for phase C."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests._helpers import make_memory_with_id
from core.store import MemoryFrontmatter
from mem_reflection_hermes.reflection import runtime as _runtime
from mem_reflection_hermes.reflection.supersedes_resolver import resolve_semantic_supersedes


class _FakeResult:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = "{}"


class _FakeLLM:
    def __init__(self, parsed):
        self._parsed = parsed

    def complete_structured(self, **_kwargs):
        return _FakeResult(self._parsed)


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


def _memory(mem_id: str, body: str, *, user_id: str | None = None) -> object:
    loaded = make_memory_with_id(mem_id, body)
    loaded.frontmatter.user_id = user_id
    return loaded


def test_resolver_supersedes_on_correction_in_same_scope():
    conflict = _memory("mem-old", "I prefer dark mode", user_id="u1")

    plan = resolve_semantic_supersedes(
        candidate_text="Actually, I prefer light mode.",
        candidate_kind="correction",
        user_msg="Actually, I was wrong: I prefer light mode.",
        conflict_memory=conflict,
        conflict_similarity=0.93,
        scope_filters={"user_id": "u1"},
    )

    assert plan["action"] == "supersede"
    assert plan["target_ids"] == ["mem-old"]


def test_resolver_merges_decision_in_same_scope():
    conflict = _memory("mem-policy", "Use git add -p for staged changes", user_id="u1")

    plan = resolve_semantic_supersedes(
        candidate_text="We decided to use git status for quick checks.",
        candidate_kind="decision",
        user_msg="我们决定以后统一用 git status 做检查。",
        conflict_memory=conflict,
        conflict_similarity=0.91,
        scope_filters={"user_id": "u1"},
    )

    assert plan["action"] == "merge"
    assert plan["target_ids"] == ["mem-policy"]


def test_resolver_stores_generic_memory_intent_without_replacement():
    conflict = _memory("mem-intent", "I prefer dark mode", user_id="u1")

    plan = resolve_semantic_supersedes(
        candidate_text="Please remember that I prefer dark mode.",
        candidate_kind="intent",
        user_msg="请记住：我喜欢深色模式。",
        conflict_memory=conflict,
        conflict_similarity=0.94,
        scope_filters={"user_id": "u1"},
    )

    assert plan["action"] == "store"
    assert plan["target_ids"] == []


def test_resolver_returns_scope_split_for_cross_scope_conflict():
    conflict = _memory("mem-outside", "I prefer light mode", user_id="u2")

    plan = resolve_semantic_supersedes(
        candidate_text="Actually, I prefer dark mode.",
        candidate_kind="correction",
        user_msg="Actually, I was wrong.",
        conflict_memory=conflict,
        conflict_similarity=0.96,
        scope_filters={"user_id": "u1"},
    )

    assert plan["action"] == "scope_split"
    assert plan["target_ids"] == []


def test_micro_reflection_keeps_generic_intent_without_supersedes(monkeypatch):
    store = SimpleNamespace(
        list_active=lambda filters=None: [_memory("mem-old", "I prefer dark mode", user_id="u1")],
        _embed_index=None,
        put_calls=[],
    )

    def _put(scope, fm, body):
        store.put_calls.append({
            "scope": scope,
            "frontmatter": fm,
            "body": body,
        })
        return SimpleNamespace(path=f"/tmp/{fm.id}.md")

    store.put = _put
    monkeypatch.setattr(_runtime, "_get_mem_store", lambda: store)
    monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)
    monkeypatch.setattr(_runtime, "_find_conflicting_memory", lambda *_args, **_kwargs: (_memory("mem-old", "I prefer dark mode", user_id="u1"), 0.94))

    result = _runtime._run_embedding_micro_reflection(
        "请记住：我喜欢深色模式。",
        "明白。",
        scope_filters={"user_id": "u1"},
    )

    assert result is not None
    assert store.put_calls
    assert store.put_calls[0]["frontmatter"].supersedes == []


def test_store_rejects_cross_scope_supersedes(temp_store):
    old = make_memory_with_id("mem-old", "old body")
    old.frontmatter.user_id = "u2"
    temp_store.put("user", old.frontmatter, old.body)

    new = make_memory_with_id("mem-new", "new body")
    new.frontmatter.user_id = "u1"
    new.frontmatter.supersedes = ["mem-old"]

    with pytest.raises(ValueError, match="different scope"):
        temp_store.put("user", new.frontmatter, new.body)


def test_full_reflection_strips_supersedes_without_replacement_intent(monkeypatch):
    store = _RecordingMemStore()
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
                "fact": "Please remember I prefer dark mode.",
                "scope": "user",
                "confidence": "high",
                "tags": ["preference"],
                "supersedes": ["mem-old"],
            }],
            "skill_candidates": [],
            "conflicts": [],
        }),
        user_id="u1",
        agent_id="a1",
        run_id="r1",
    )

    result = _runtime._run_full_reflection(ctx, [{"role": "user", "content": "记住我的偏好即可"}])

    assert result["accepted_memories"]
    assert store.check_conflict_calls[0]["exclude_ids"] == ["mem-old"]
    assert store.put_calls[0]["frontmatter"].supersedes == []


# ---------------------------------------------------------------------------
# P1-2 regression: merge / scope_split must leave a side-effect, not be
# silently dropped. These tests cover the behaviour the round-3 audit found
# was missing — the resolver returned merge/scope_split but reflection paths
# only handled skip/supersede.
# ---------------------------------------------------------------------------

class _RecordingGraph:
    """Minimal graph stub capturing record/invalidate calls for P1-2 tests."""

    def __init__(self):
        self.recorded = []
        self.invalidated = []

    def record_typed_fact(self, *args, **kwargs):
        self.recorded.append({"args": args, "kwargs": kwargs})
        return "fact-id"

    def record_entity_mentions(self, *args, **kwargs):
        return []

    def invalidate_facts_for_memories(self, memory_ids, invalidated_by):
        self.invalidated.append({"memory_ids": list(memory_ids), "invalidated_by": invalidated_by})
        return len(memory_ids)


class _GraphedStore:
    """Store stub exposing a ``_graph`` so the sidecar writer can reach it."""

    def __init__(self, graph):
        self._graph = graph

    def get(self, _mid):
        return object()


def test_record_semantic_relation_sidecar_writes_merge_edge():
    graph = _RecordingGraph()
    store = _GraphedStore(graph)
    fm = MemoryFrontmatter.new(source="test", confidence="medium", zone="general")

    _runtime._record_semantic_relation_sidecar(
        store, fm,
        action="merge",
        target_ids=["mem-policy"],
        reason="decision merge cue",
        confidence=0.8,
    )

    assert len(graph.recorded) == 1
    rec = graph.recorded[0]["kwargs"]
    assert rec["relation"] == "merges"
    assert rec["kind"] == "semantic_merge"
    assert rec["subject"] == fm.id
    assert rec["object"] == "mem-policy"
    assert rec["target_memory_id"] == "mem-policy"


def test_record_semantic_relation_sidecar_writes_scope_split_edge():
    graph = _RecordingGraph()
    store = _GraphedStore(graph)
    fm = MemoryFrontmatter.new(source="test", confidence="medium", zone="general")

    _runtime._record_semantic_relation_sidecar(
        store, fm,
        action="scope_split",
        target_ids=["mem-u2"],
        reason="cross-scope",
        confidence=0.7,
    )

    assert len(graph.recorded) == 1
    rec = graph.recorded[0]["kwargs"]
    assert rec["relation"] == "scope_split_with"
    assert rec["kind"] == "semantic_scope_split"
    assert rec["target_memory_id"] == "mem-u2"


def test_record_semantic_relation_sidecar_noop_for_store_and_supersede():
    graph = _RecordingGraph()
    store = _GraphedStore(graph)
    fm = MemoryFrontmatter.new(source="test", confidence="medium", zone="general")

    # store / supersede actions must not write a semantic-relation edge;
    # supersede carries its lineage through frontmatter.supersedes instead.
    _runtime._record_semantic_relation_sidecar(
        store, fm, action="store", target_ids=["mem-old"], reason="r",
    )
    _runtime._record_semantic_relation_sidecar(
        store, fm, action="supersede", target_ids=["mem-old"], reason="r",
    )
    assert graph.recorded == []


def test_merge_action_invalidates_merge_target_facts(monkeypatch):
    """P1-2 end-to-end via the embedding micro path: a merge decision must
    invalidate the merge target's typed facts (the resolver named it as a
    semantic update), not just store the new memory silently."""
    old = make_memory_with_id("mem-policy", "Use git add -p")
    old.frontmatter.user_id = "u1"

    graph = _RecordingGraph()
    store = SimpleNamespace(
        list_active=lambda filters=None: [old],
        _embed_index=None,
        _graph=graph,
        put_calls=[],
    )

    def _put(scope, fm, body):
        store.put_calls.append({"frontmatter": fm, "body": body})
        return SimpleNamespace(path=f"/tmp/{fm.id}.md")

    store.put = _put
    monkeypatch.setattr(_runtime, "_get_mem_store", lambda: store)
    monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_a, **_k: 0.9)
    # Force a high-similarity conflict so the resolver sees a decision-kind merge.
    monkeypatch.setattr(
        _runtime, "_find_conflicting_memory",
        lambda *_a, **_k: (old, 0.92),
    )

    result = _runtime._run_embedding_micro_reflection(
        "我们决定以后统一用 git status 做检查。",
        "明白。",
        scope_filters={"user_id": "u1"},
    )

    assert result is not None
    assert store.put_calls
    # The new memory must NOT carry a lineage edge (merge ≠ supersede)...
    assert store.put_calls[0]["frontmatter"].supersedes == []
    # ...but the merge target's facts must be invalidated, and a `merges`
    # relation edge recorded so the decision is not silently dropped.
    assert graph.invalidated, "merge must invalidate the merge target's facts"
    assert graph.invalidated[0]["memory_ids"] == ["mem-policy"]
    assert any(rec["kwargs"]["relation"] == "merges" for rec in graph.recorded)
