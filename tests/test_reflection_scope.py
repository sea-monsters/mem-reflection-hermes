"""Scope propagation tests for reflection and compaction paths."""
from __future__ import annotations

from types import SimpleNamespace

from mem_reflection_hermes.core.store import MemoryFrontmatter
from mem_reflection_hermes.reflection import runtime as _runtime
from mem_reflection_hermes.runtime import hooks as _hooks


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

    def list_by_zone(self, zone, filters=None):
        self.list_active_calls.append({"zone": zone, "filters": filters})
        return []


def test_pre_llm_call_propagates_scope_filters(monkeypatch):
    captured = {"context": None, "reflection": None}

    def _build_context_with_timeout(query, budget, filters=None):
        captured["context"] = {"query": query, "budget": budget, "filters": filters}
        return "stable context"

    def _run_micro_reflection(ctx, user_msg, assistant_msg, scope_filters=None):
        captured["reflection"] = {
            "ctx": ctx,
            "user_msg": user_msg,
            "assistant_msg": assistant_msg,
            "filters": scope_filters,
        }
        return {"ok": True}

    monkeypatch.setattr(_hooks, "_build_context_with_timeout", _build_context_with_timeout)
    monkeypatch.setattr(_hooks, "_run_micro_reflection", _run_micro_reflection)
    monkeypatch.setattr(_hooks, "_is_explicit_memory_intent", lambda _text: True)
    monkeypatch.setattr(_hooks, "_micro_reflection_enabled", lambda: True)
    monkeypatch.setattr(_hooks, "_context_token_budget", lambda: 256)
    monkeypatch.setattr(_hooks, "_estimate_tokens", lambda _text: 1)

    result = _hooks._pre_llm_call(
        ctx=SimpleNamespace(user_id="u1", agent_id="a1", run_id="r1"),
        session_id="sess-1",
        messages=[{"role": "assistant", "content": "noted"}],
        user_message="remember this scoped preference",
    )

    assert result is not None
    assert captured["context"]["filters"] == {"user_id": "u1", "agent_id": "a1", "run_id": "r1"}
    assert captured["reflection"]["filters"] == {"user_id": "u1", "agent_id": "a1", "run_id": "r1"}


def test_full_reflection_uses_scope_filters_for_reads_and_writes(monkeypatch):
    store = _RecordingMemStore()
    monkeypatch.setattr(_runtime, "_get_mem_store", lambda: store)
    monkeypatch.setattr(_runtime, "_get_skill_store", lambda: SimpleNamespace(list=lambda: []))
    monkeypatch.setattr(_runtime, "_reflection_mode", lambda: "llm")
    monkeypatch.setattr(_runtime, "_validate_supersedes_targets", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_save_pending_skill_candidates", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)
    monkeypatch.setattr(_runtime, "_current_session_memory_ids", SimpleNamespace(ids=set()))

    ctx = SimpleNamespace(
        llm=_FakeLLM({
            "summary": "ok",
            "memory_candidates": [{
                "fact": "Scoped deployment preference",
                "scope": "user",
                "confidence": "high",
                "tags": ["deploy"],
                "supersedes": [],
            }],
            "skill_candidates": [],
            "conflicts": [],
        }),
        user_id="u1",
        agent_id="a1",
        run_id="r1",
    )

    result = _runtime._run_full_reflection(ctx, [{"role": "user", "content": "remember this"}])

    assert result["accepted_memories"]
    assert store.list_active_calls[0] == {"user_id": "u1", "agent_id": "a1", "run_id": "r1"}
    assert store.check_conflict_calls[0]["filters"] == {"user_id": "u1", "agent_id": "a1", "run_id": "r1"}
    assert store.put_calls[0]["frontmatter"].user_id == "u1"
    assert store.put_calls[0]["frontmatter"].agent_id == "a1"
    assert store.put_calls[0]["frontmatter"].run_id == "r1"


def test_raw_chunk_reflection_stamps_scope_fields(monkeypatch):
    store = _RecordingMemStore()
    monkeypatch.setattr(_runtime, "_get_mem_store", lambda: store)
    monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_args, **_kwargs: None)

    result = _runtime._run_raw_chunk_reflection(
        [
            {"role": "user", "content": "Scoped user memory"},
            {"role": "assistant", "content": "Acknowledged."},
            {"role": "user", "content": "More scoped detail"},
        ],
        scope_filters={"user_id": "u1", "agent_id": "a1", "run_id": "r1"},
    )

    assert result["accepted_memories"]
    assert store.put_calls[0]["frontmatter"].user_id == "u1"
    assert store.put_calls[0]["frontmatter"].agent_id == "a1"
    assert store.put_calls[0]["frontmatter"].run_id == "r1"


def test_compact_episode_zone_respects_scope_filters(temp_store):
    u2_ids = []
    for idx in range(25):
        fm_u1 = MemoryFrontmatter.new(source="session", confidence="low", tags=["raw_chunk"], zone="episode", user_id="u1")
        temp_store.put("user", fm_u1, f"Scoped entry {idx} for u1")

        fm_u2 = MemoryFrontmatter.new(source="session", confidence="low", tags=["raw_chunk"], zone="episode", user_id="u2")
        temp_store.put("user", fm_u2, f"Scoped entry {idx} for u2")
        u2_ids.append(fm_u2.id)

    result = _runtime._compact_episode_zone(temp_store, filters={"user_id": "u1"})

    assert result["compacted"] >= 1
    summaries = [m for m in temp_store.list_by_zone("episode") if "compacted" in (m.frontmatter.tags or [])]
    assert summaries
    assert all(getattr(m.frontmatter, "user_id", None) == "u1" for m in summaries)
    assert all(getattr(m.frontmatter, "user_id", None) != "u2" for m in summaries)
    assert all(not temp_store.is_superseded(memory_id) for memory_id in u2_ids)
