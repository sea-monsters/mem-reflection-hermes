"""test_reflection.py — Reflection engine: JSON repair, raw_chunk, audit.

Tests:
- JSON parsing (valid, code-fenced, truncated)
- JSON repair (missing brackets, nested truncation)
- Audit entry structure

Run: pytest tests/test_reflection.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Load reflection.engine functions via importlib to handle relative imports
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parent.parent

# Ensure store is importable (no relative imports)
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import store as _store_mod
from core import search as _search_mod

# Set up minimal package namespace for reflection.engine's relative imports
_PKG = "mem_reflection_hermes_reflection_test"


def _restore_modules(previous: Dict[str, Optional[types.ModuleType]]) -> None:
    for name, module in previous.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _setup_pkg_namespace():
    """Set up package namespace so relative imports in submodules resolve."""
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_REPO)]
    sys.modules[_PKG] = pkg

    # Register core subpackage with proper __path__
    core_mod = types.ModuleType(f"{_PKG}.core")
    core_mod.__path__ = [str(_REPO / "core")]
    core_mod.__package__ = f"{_PKG}.core"
    sys.modules[f"{_PKG}.core"] = core_mod
    sys.modules[f"{_PKG}.core.store"] = _store_mod
    sys.modules[f"{_PKG}.core.search"] = _search_mod
    sys.modules[f"{_PKG}.store"] = _store_mod

    # Register reflection subpackage
    reflection_pkg = types.ModuleType(f"{_PKG}.reflection")
    reflection_pkg.__path__ = [str(_REPO / "reflection")]
    reflection_pkg.__package__ = f"{_PKG}.reflection"
    sys.modules[f"{_PKG}.reflection"] = reflection_pkg

    # Register runtime subpackage
    runtime_pkg = types.ModuleType(f"{_PKG}.runtime")
    runtime_pkg.__path__ = [str(_REPO / "runtime")]
    runtime_pkg.__package__ = f"{_PKG}.runtime"
    sys.modules[f"{_PKG}.runtime"] = runtime_pkg


def _load_reflection_engine() -> tuple[object | None, object | None, Exception | None, Exception | None]:
    # Modules to keep alive for engine's delegate calls
    _persistent = [
        _PKG, f"{_PKG}.core", f"{_PKG}.core.store", f"{_PKG}.core.search",
        f"{_PKG}.reflection", f"{_PKG}.reflection.runtime",
    ]
    touched_modules = [
        f"{_PKG}.reflection.engine",
        f"{_PKG}.runtime", f"{_PKG}.runtime.hooks",
    ]
    previous_modules = {name: sys.modules.get(name) for name in touched_modules}
    engine_load_error = None
    engine = None
    runtime = None

    try:
        _setup_pkg_namespace()

        # Load reflection.runtime (needed by engine's delegate)
        runtime_path = _REPO / "reflection" / "runtime.py"
        if runtime_path.exists():
            rt_spec = importlib.util.spec_from_file_location(
                f"{_PKG}.reflection.runtime", str(runtime_path))
            if rt_spec and rt_spec.loader:
                rt_mod = importlib.util.module_from_spec(rt_spec)
                rt_mod.__package__ = f"{_PKG}.reflection"
                sys.modules[f"{_PKG}.reflection.runtime"] = rt_mod
                # Also register as .runtime for engine's "from . import runtime"
                sys.modules[f"{_PKG}.reflection.runtime"] = rt_mod
                rt_spec.loader.exec_module(rt_mod)
                runtime = rt_mod

        engine_path = _REPO / "reflection" / "engine.py"
        if engine_path.exists():
            spec = importlib.util.spec_from_file_location(
                f"{_PKG}.reflection.engine", str(engine_path))
            if spec and spec.loader:
                engine = importlib.util.module_from_spec(spec)
                engine.__package__ = f"{_PKG}.reflection"
                sys.modules[f"{_PKG}.reflection.engine"] = engine
                try:
                    spec.loader.exec_module(engine)
                except Exception as exc:
                    engine = None
                    engine_load_error = exc

        return engine, runtime, None, engine_load_error
    finally:
        _restore_modules(previous_modules)


def _load_module_from_repo(module_name: str, relative_path: str) -> object:
    touched_modules = [
        _PKG, f"{_PKG}.core", f"{_PKG}.core.store", f"{_PKG}.core.search",
        f"{_PKG}.store", f"{_PKG}.search",
        f"{_PKG}.reflection", f"{_PKG}.reflection.engine", f"{_PKG}.reflection.runtime",
        f"{_PKG}.runtime", f"{_PKG}.runtime.hooks",
    ]
    previous_modules = {name: sys.modules.get(name) for name in touched_modules}

    try:
        _setup_pkg_namespace()

        # Load reflection.engine first (needed by hooks)
        engine_path = _REPO / "reflection" / "engine.py"
        if engine_path.exists():
            engine_spec = importlib.util.spec_from_file_location(
                f"{_PKG}.reflection.engine", str(engine_path))
            if engine_spec and engine_spec.loader:
                engine_mod = importlib.util.module_from_spec(engine_spec)
                engine_mod.__package__ = f"{_PKG}.reflection"
                sys.modules[f"{_PKG}.reflection.engine"] = engine_mod
                engine_spec.loader.exec_module(engine_mod)

        # Load reflection.runtime
        runtime_path = _REPO / "reflection" / "runtime.py"
        if runtime_path.exists():
            rt_spec = importlib.util.spec_from_file_location(
                f"{_PKG}.reflection.runtime", str(runtime_path))
            if rt_spec and rt_spec.loader:
                rt_mod = importlib.util.module_from_spec(rt_spec)
                rt_mod.__package__ = f"{_PKG}.reflection"
                sys.modules[f"{_PKG}.reflection.runtime"] = rt_mod
                rt_spec.loader.exec_module(rt_mod)

        module_spec = importlib.util.spec_from_file_location(
            module_name, str(_REPO / relative_path))
        if not module_spec or not module_spec.loader:
            raise RuntimeError(f"Could not load {module_name}")
        module = importlib.util.module_from_spec(module_spec)
        package_name = module_name.rsplit(".", 1)[0]
        module.__package__ = package_name
        sys.modules[module_name] = module
        module_spec.loader.exec_module(module)
        return module
    finally:
        _restore_modules(previous_modules)


_engine, _runtime, _embed_load_error, _engine_load_error = _load_reflection_engine()
_lifecycle_mod = _load_module_from_repo(f"{_PKG}.runtime.hooks", "runtime/hooks.py")

if _engine_load_error is not None:
    raise RuntimeError("Could not load reflection.engine for reflection tests") from _engine_load_error
if _engine is None:
    raise RuntimeError("reflection.engine was not loaded for reflection tests")

# Extract the functions we need (they're pure, no module-level deps)
_parse_reflect_output = getattr(_engine, "_parse_reflect_output", None)
_repair_truncated_json = getattr(_engine, "_repair_truncated_json", None)
_build_audit_entry = getattr(_engine, "_build_audit_entry", None)

skip_no_engine = pytest.mark.skipif(
    _engine is None, reason="Could not load reflection.engine"
)


class _FakeResult:
    def __init__(self, parsed: Dict[str, Any], text: str = "{}"):
        self.parsed = parsed
        self.text = text


class _FakeLLM:
    def __init__(self, parsed: Dict[str, Any]):
        self._parsed = parsed

    def complete_structured(self, **_: Any) -> _FakeResult:
        return _FakeResult(self._parsed, json.dumps(self._parsed))


class _RecordingMemStore:
    def __init__(self):
        self._embed_index = None
        self.check_conflict_calls = []
        self.put_calls = []

    def list_active(self, filters=None):
        return []

    def check_conflict(self, body: str, exclude_ids=None, filters=None):
        self.check_conflict_calls.append({
            "body": body,
            "exclude_ids": list(exclude_ids or []),
            "filters": filters,
        })
        return None

    def put(self, scope: str, fm: Any, body: str) -> Path:
        self.put_calls.append({
            "scope": scope,
            "frontmatter": fm,
            "body": body,
        })
        return Path(f"/tmp/{fm.id}.md")

    def get(self, _memory_id: str) -> object:
        return object()


class _EmptySkillStore:
    def list(self):
        return []


# ---------------------------------------------------------------------------
# JSON parsing tests
# ---------------------------------------------------------------------------

@skip_no_engine
class TestParseReflectOutput:
    def test_valid_json(self):
        text = '{"facts": ["user prefers dark mode"], "supersedes": []}'
        result = _parse_reflect_output(text)
        assert result is not None
        assert "facts" in result
        assert len(result["facts"]) == 1

    def test_code_fence_json(self):
        text = '```json\n{"facts": ["test"], "supersedes": []}\n```'
        result = _parse_reflect_output(text)
        assert result is not None
        assert "facts" in result

    def test_code_fence_with_surrounding_text(self):
        text = 'Here is the result:\n```json\n{"facts": ["x"], "supersedes": []}\n```\nDone.'
        result = _parse_reflect_output(text)
        assert result is not None
        assert "facts" in result

    def test_empty_input_returns_none(self):
        result = _parse_reflect_output("")
        assert result is None

    def test_non_json_returns_none(self):
        result = _parse_reflect_output("This is just plain text with no JSON at all")
        assert result is None


@skip_no_engine
class TestRepairTruncatedJSON:
    def test_missing_closing_bracket(self):
        s = '{"facts": ["a", "b"'
        repaired = _repair_truncated_json(s)
        assert repaired is not None
        obj = json.loads(repaired)
        assert obj["facts"] == ["a", "b"]

    def test_missing_closing_brace(self):
        s = '{"facts": ["a"], "supersedes": []'
        repaired = _repair_truncated_json(s)
        assert repaired is not None
        obj = json.loads(repaired)
        assert obj == {"facts": ["a"], "supersedes": []}

    def test_nested_truncation_preserves_completed_nested_fields(self):
        s = '{"facts": [{"text": "user likes python", "confidence": 0.9}]'
        repaired = _repair_truncated_json(s)
        assert repaired is not None
        obj = json.loads(repaired)
        assert obj == {
            "facts": [{"text": "user likes python", "confidence": 0.9}]
        }

    def test_partial_tail_drops_incomplete_field_but_keeps_completed_prefix(self):
        s = '{"summary": "ok", "facts": [{"text": "x"}], "conflicts": ['
        repaired = _repair_truncated_json(s)
        assert repaired is not None
        obj = json.loads(repaired)
        assert obj == {"summary": "ok", "facts": [{"text": "x"}]}

    def test_nested_partial_member_keeps_completed_nested_prefix(self):
        s = '{"summary": "ok", "facts": [{"text": "x", "meta": {"a": 1, "b": '
        repaired = _repair_truncated_json(s)
        assert repaired is not None
        obj = json.loads(repaired)
        assert obj == {"summary": "ok", "facts": [{"text": "x", "meta": {"a": 1}}]}

    def test_unterminated_string_returns_none(self):
        s = '{"summary": "unterminated'
        repaired = _repair_truncated_json(s)
        assert repaired is None

    def test_partial_scalar_salvages_previous_complete_field(self):
        s = '{"a": 1, "b": tru'
        repaired = _repair_truncated_json(s)
        assert repaired is not None
        obj = json.loads(repaired)
        assert obj == {"a": 1}

    def test_non_brace_start_returns_none(self):
        result = _repair_truncated_json("not json")
        assert result is None

    def test_complete_json_no_repair_needed(self):
        s = '{"facts": ["a"], "supersedes": []}'
        repaired = _repair_truncated_json(s)
        assert repaired is None


@skip_no_engine
class TestAuditEntry:
    def test_structure(self):
        """_build_audit_entry produces expected dict structure."""
        entry = _build_audit_entry(
            candidate_id="mem-123",
            decision="accepted",
            decision_reason="novel fact",
            novelty_score=0.8,
            assigned_zone="general",
        )
        assert isinstance(entry, dict)
        assert entry.get("candidate_id") == "mem-123"
        assert entry.get("decision") == "accepted"
        assert entry.get("novelty_score") == 0.8

    def test_audit_entry_defaults_optional_fields(self):
        entry = _build_audit_entry(
            candidate_id="mem-456",
            decision="rejected",
            decision_reason="duplicate",
        )
        assert isinstance(entry, dict)
        assert entry["candidate_id"] == "mem-456"
        assert entry["decision"] == "rejected"
        assert entry["novelty_score"] == 0.0
        assert entry["supersedes_ids"] == []
        assert entry["graph_migration"] == {}


@skip_no_engine
class TestHookReflectionCadence:
    def test_on_session_start_runs_pending_recovery(self, monkeypatch):
        monkeypatch.setattr(_lifecycle_mod, "_plugin_ctx", None)
        monkeypatch.setattr(_lifecycle_mod, "_lb", lambda name: (lambda: object()) if name == "_get_mem_store" else (lambda *_args, **_kwargs: None))
        seen = {}

        def _fake_recover(**kwargs):
            seen.update(kwargs)
            return {"reflection": 1, "compaction": 0, "curator": 0, "diagnostic": 0}

        monkeypatch.setattr(_lifecycle_mod, "_checkpoint_recover_pending_work", _fake_recover)

        _lifecycle_mod._on_session_start(session_id="start-session")

        assert "reflection_runner" in seen
        assert "diagnostic_logger" in seen

    def test_pre_llm_call_skips_micro_reflection_when_ctx_unavailable(self, monkeypatch):
        """When build_context_bundle returns None and mode is 'llm', micro reflection is not attempted.

        Observable intent: pre_llm_call returns None (no context injection) and does not
        invoke _run_micro_reflection. The turn counter is an internal detail — the functional
        contract is that no reflection side effects occur.
        """
        monkeypatch.setattr(_lifecycle_mod, "_micro_reflection_enabled", lambda: True)
        monkeypatch.setattr(_lifecycle_mod, "_is_explicit_memory_intent", lambda _text: False)
        monkeypatch.setattr(_lifecycle_mod, "_reflection_mode", lambda: "llm")
        monkeypatch.setattr(_lifecycle_mod, "_build_context_bundle", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(_lifecycle_mod, "_run_micro_reflection", lambda *_args, **_kwargs: pytest.fail("should not run"))
        monkeypatch.setattr(_lifecycle_mod, "_plugin_ctx", None)

        result = _lifecycle_mod._pre_llm_call(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            session_id="test-session",
        )

        assert result is None

    def test_pre_llm_call_uses_stable_fallback_on_timeout(self, monkeypatch):
        monkeypatch.setattr(_lifecycle_mod, "_micro_reflection_enabled", lambda: False)
        monkeypatch.setattr(_lifecycle_mod, "_plugin_ctx", None)
        monkeypatch.setattr(_lifecycle_mod, "_context_timeout_ms", lambda: 10)
        monkeypatch.setattr(_lifecycle_mod, "_estimate_tokens", lambda _text: 1)

        def _fake_bundle(_query, max_tokens=4000, stable_only=False, filters=None):
            if stable_only:
                return SimpleNamespace(
                    append_system_context="## Pinned Memories\n- [core] stable",
                    prepend_context="",
                )
            time.sleep(0.05)
            return SimpleNamespace(
                append_system_context="## Pinned Memories\n- [core] stable",
                prepend_context="## Relevant Memories\n- [general] dynamic",
            )

        import time
        monkeypatch.setattr(_lifecycle_mod, "_build_context_bundle", _fake_bundle)

        result = _lifecycle_mod._pre_llm_call(
            messages=[{"role": "user", "content": "hello"}],
            session_id="timeout-session",
        )

        assert result is not None
        assert "Pinned Memories" in result["context"]
        assert "Relevant Memories" not in result["context"]

    def test_pre_llm_call_timeout_produces_valid_stable_fallback(self, monkeypatch):
        """P0: Timeout path returns stable-only context, and subsequent calls still work correctly.

        Design intent: a timeout on one call must not corrupt the hook's ability to serve
        subsequent calls. Verified by making two consecutive calls — the second must also
        produce valid output.
        """
        monkeypatch.setattr(_lifecycle_mod, "_micro_reflection_enabled", lambda: False)
        monkeypatch.setattr(_lifecycle_mod, "_plugin_ctx", None)
        monkeypatch.setattr(_lifecycle_mod, "_context_timeout_ms", lambda: 10)
        monkeypatch.setattr(_lifecycle_mod, "_estimate_tokens", lambda _text: 1)

        call_count = {"n": 0}

        def _fake_slow_bundle(_query, max_tokens=4000, stable_only=False, filters=None):
            call_count["n"] += 1
            if stable_only:
                return SimpleNamespace(
                    append_system_context="## Pinned\n- stable fallback",
                    prepend_context="",
                )
            import time
            time.sleep(0.05)
            return SimpleNamespace(
                append_system_context="## Pinned\n- full",
                prepend_context="## Relevant\n- dynamic",
            )

        monkeypatch.setattr(_lifecycle_mod, "_build_context_bundle", _fake_slow_bundle)

        # First call: will timeout and fall back to stable
        result1 = _lifecycle_mod._pre_llm_call(
            messages=[{"role": "user", "content": "hello"}],
            session_id="timeout-session",
        )
        assert result1 is not None
        assert "Pinned" in result1["context"]
        assert "dynamic" not in result1["context"]

        # Second call: must also produce valid output (no corruption from first call)
        result2 = _lifecycle_mod._pre_llm_call(
            messages=[{"role": "user", "content": "world"}],
            session_id="timeout-session",
        )
        assert result2 is not None
        assert isinstance(result2.get("context"), str)
        assert "Pinned" in result2["context"]

    def test_on_session_end_marks_reflection_pending_when_reflection_fails(self, monkeypatch):
        pending_calls = []
        completed_calls = []
        snapshot_calls = []

        monkeypatch.setattr(_lifecycle_mod, "_plugin_ctx", object())
        monkeypatch.setattr(_lifecycle_mod, "_get_graph_mgr", lambda: None)
        monkeypatch.setattr(_lifecycle_mod, "_checkpoint_snapshot_session_state", lambda sid, state: snapshot_calls.append((sid, dict(state))))
        monkeypatch.setattr(_lifecycle_mod, "_checkpoint_mark_pending_stage", lambda sid, stage, payload: pending_calls.append((sid, stage, payload)))
        monkeypatch.setattr(_lifecycle_mod, "_checkpoint_mark_stage_completed", lambda sid, stage, payload=None: completed_calls.append((sid, stage, payload)))
        monkeypatch.setattr(_lifecycle_mod, "_checkpoint_clear_session_state", lambda _sid: None)
        monkeypatch.setattr(_lifecycle_mod, "_run_full_reflection", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

        _lifecycle_mod._on_session_end(
            session_id="end-session",
            reason="shutdown",
            messages=[{"role": "user", "content": "remember this"}],
        )

        assert snapshot_calls
        assert pending_calls
        assert pending_calls[0][1] == "reflection"
        assert completed_calls == []

    def test_pre_llm_call_zero_timeout_uses_fallback(self, monkeypatch):
        """P0: timeout_ms=0 should immediately trigger stable fallback without blocking."""
        monkeypatch.setattr(_lifecycle_mod, "_micro_reflection_enabled", lambda: False)
        monkeypatch.setattr(_lifecycle_mod, "_plugin_ctx", None)
        monkeypatch.setattr(_lifecycle_mod, "_context_timeout_ms", lambda: 0)

        call_log = []

        def _fake_bundle(_query, max_tokens=4000, stable_only=False, filters=None):
            call_log.append(("stable" if stable_only else "full"))
            if stable_only:
                return SimpleNamespace(
                    append_system_context="## Pinned\n- fallback",
                    prepend_context="",
                )
            # Slow full path
            import time
            time.sleep(0.02)
            return SimpleNamespace(
                append_system_context="## Pinned\n- full",
                prepend_context="## Relevant\n- dynamic",
            )

        monkeypatch.setattr(_lifecycle_mod, "_build_context_bundle", _fake_bundle)

        result = _lifecycle_mod._pre_llm_call(
            messages=[{"role": "user", "content": "hi"}],
            session_id="zero-timeout-session",
        )

        assert result is not None
        # Should have called stable fallback at least once
        assert "stable" in call_log
        assert "fallback" in result["context"] or result["context"] == ""


@skip_no_engine
class TestReflectionSupersedesRegression:
    def test_full_reflection_excludes_current_session_ids_from_conflict_check(self, monkeypatch):
        """P2-9: LLM full reflection must exclude current session memory IDs from conflict check."""
        mem_store = _RecordingMemStore()
        monkeypatch.setattr(_runtime, "_reflection_mode", lambda: "llm")
        monkeypatch.setattr(_runtime, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_runtime, "_get_skill_store", lambda: _EmptySkillStore())
        monkeypatch.setattr(_runtime, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_runtime, "_save_pending_skill_candidates", lambda *_: None)
        monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)
        _runtime._current_session_memory_ids.ids = {"mem-session"}

        ctx = types.SimpleNamespace(llm=_FakeLLM({
            "summary": "ok",
            "memory_candidates": [{
                "fact": "Updated deployment preference",
                "scope": "user",
                "confidence": "high",
                "tags": ["deploy"],
                "supersedes": ["mem-old"],
            }],
            "skill_candidates": [],
            "conflicts": [],
        }))

        result = _engine._run_full_reflection(ctx, [{"role": "user", "content": "Update my deployment preference."}])

        assert result["accepted_memories"]
        excluded = set(mem_store.check_conflict_calls[0]["exclude_ids"])
        assert excluded == {"mem-old", "mem-session"}
        _runtime._reset_current_session_memory_ids()

    def test_micro_reflection_excludes_current_session_ids_from_conflict_check(self, monkeypatch):
        """P2-9: LLM micro reflection must exclude current session memory IDs from conflict check."""
        mem_store = _RecordingMemStore()
        monkeypatch.setattr(_runtime, "_reflection_mode", lambda: "llm")
        monkeypatch.setattr(_runtime, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_runtime, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.8)
        _runtime._current_session_memory_ids.ids = {"mem-micro-session"}

        ctx = types.SimpleNamespace(llm=_FakeLLM({
            "summary": "ok",
            "memory_candidates": [{
                "fact": "Updated editor preference",
                "scope": "user",
                "confidence": "medium",
                "tags": ["editor"],
                "supersedes": ["mem-editor-old"],
            }],
            "skill_candidates": [],
            "conflicts": [],
        }))

        parsed = _engine._run_micro_reflection(ctx, "Actually, I was wrong about my editor preference", "noted")

        assert parsed is not None
        excluded = set(mem_store.check_conflict_calls[0]["exclude_ids"])
        assert excluded == {"mem-editor-old", "mem-micro-session"}
        _runtime._reset_current_session_memory_ids()

    def test_full_reflection_excludes_superseded_ids_from_conflict_check(self, monkeypatch):
        mem_store = _RecordingMemStore()
        # These functions are called from runtime, so patch runtime module
        monkeypatch.setattr(_runtime, "_reflection_mode", lambda: "llm")
        monkeypatch.setattr(_runtime, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_runtime, "_get_skill_store", lambda: _EmptySkillStore())
        monkeypatch.setattr(_runtime, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_runtime, "_save_pending_skill_candidates", lambda *_: None)
        # _compute_novelty_score is only in runtime
        monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)

        ctx = types.SimpleNamespace(llm=_FakeLLM({
            "summary": "ok",
            "memory_candidates": [{
                "fact": "Updated deployment preference",
                "scope": "user",
                "confidence": "high",
                "tags": ["deploy"],
                "supersedes": ["mem-old"],
            }],
            "skill_candidates": [],
            "conflicts": [],
        }))

        result = _engine._run_full_reflection(ctx, [{"role": "user", "content": "Actually, I was wrong. Update my deployment preference."}])

        assert result["accepted_memories"]
        assert mem_store.check_conflict_calls[0]["exclude_ids"] == ["mem-old"]
        assert mem_store.put_calls[0]["frontmatter"].supersedes == ["mem-old"]

    def test_full_reflection_remembers_new_memory_ids_for_session_exclusion(self, monkeypatch):
        mem_store = _RecordingMemStore()
        monkeypatch.setattr(_runtime, "_reflection_mode", lambda: "llm")
        monkeypatch.setattr(_runtime, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_runtime, "_get_skill_store", lambda: _EmptySkillStore())
        monkeypatch.setattr(_runtime, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_runtime, "_save_pending_skill_candidates", lambda *_: None)
        monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)
        _runtime._reset_current_session_memory_ids()

        ctx = types.SimpleNamespace(llm=_FakeLLM({
            "summary": "ok",
            "memory_candidates": [{
                "fact": "Remember my preferred deployment check order.",
                "scope": "user",
                "confidence": "high",
                "tags": ["deploy"],
                "supersedes": [],
            }],
            "skill_candidates": [],
            "conflicts": [],
        }))

        result = _engine._run_full_reflection(ctx, [{"role": "user", "content": "remember this"}])

        accepted_id = result["accepted_memories"][0]["id"]
        assert accepted_id in _runtime._get_current_session_memory_ids()
        _runtime._reset_current_session_memory_ids()

    def test_micro_reflection_excludes_superseded_ids_from_conflict_check(self, monkeypatch):
        mem_store = _RecordingMemStore()
        monkeypatch.setattr(_runtime, "_reflection_mode", lambda: "llm")
        monkeypatch.setattr(_runtime, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_runtime, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.8)

        ctx = types.SimpleNamespace(llm=_FakeLLM({
            "summary": "ok",
            "memory_candidates": [{
                "fact": "Updated editor preference",
                "scope": "user",
                "confidence": "medium",
                "tags": ["editor"],
                "supersedes": ["mem-editor-old"],
            }],
            "skill_candidates": [],
            "conflicts": [],
        }))

        parsed = _engine._run_micro_reflection(ctx, "Actually, I was wrong about my editor preference", "noted")

        assert parsed is not None
        assert mem_store.check_conflict_calls[0]["exclude_ids"] == ["mem-editor-old"]
        assert mem_store.put_calls[0]["frontmatter"].supersedes == ["mem-editor-old"]

    def test_embedding_reflection_uses_summary_fallback_for_skill_metadata(self, monkeypatch):
        mem_store = _RecordingMemStore()
        # Patch runtime functions
        monkeypatch.setattr(_runtime, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_runtime, "_get_skill_store", lambda: _EmptySkillStore())
        monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_runtime, "_save_pending_skill_candidates", lambda *_: None)
        monkeypatch.setattr(_runtime, "_extract_facts_from_turn", lambda *_: [])
        monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.8)
        # _extract_keywords is imported into runtime from search, so patch it on runtime
        monkeypatch.setattr(_runtime, "_extract_keywords", lambda text, top_k=3: ["deploy", "cache", "warmup"] if "summary" in text else ["assistant", "steps"])
        # _is_procedure is imported into runtime from search, so patch it on runtime
        monkeypatch.setattr(_runtime, "_generate_session_summary", lambda _text: "summary fallback for session generated memory")
        monkeypatch.setattr(_runtime, "_is_procedure", lambda _text: True)
        monkeypatch.setattr(_runtime, "_generate_skill_name", lambda _text: "deploy-cache-warmup")
        monkeypatch.setattr(_runtime, "_validate_supersedes_targets", lambda *_: None)
        # _embed_single and _cosine_sim are imported into runtime from search
        monkeypatch.setattr(_runtime, "_embed_single", lambda *_: [1.0, 0.0])
        monkeypatch.setattr(_runtime, "_cosine_sim", lambda *_: 0.0)
        _runtime._current_session_memory_ids.ids = set()

        messages = [
            {"role": "user", "content": "Please summarize the deployment procedure for later reuse."},
            {"role": "assistant", "content": "First verify the environment. Then warm caches. Then restart workers. Finally run smoke tests. " * 4},
        ]

        result = _engine._run_embedding_reflection(messages)

        assert result["skill_candidates"]
        skill = result["skill_candidates"][0]
        assert skill["description"].startswith("Procedure extracted from session: summary fallback")
        assert skill["triggers"] == ["deploy", "cache", "warmup"]

    def test_embedding_reflection_excludes_session_and_supersedes_ids_from_final_conflict_check(self, monkeypatch):
        mem_store = _RecordingMemStore()
        # Patch runtime functions
        monkeypatch.setattr(_runtime, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_runtime, "_get_skill_store", lambda: _EmptySkillStore())
        monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_runtime, "_save_pending_skill_candidates", lambda *_: None)
        monkeypatch.setattr(_runtime, "_extract_facts_from_turn", lambda *_: [{
            "text": "I now prefer ripgrep over grep for repository search.",
            "confidence": "high",
            "rationale": "preference",
            "source": "explicit_intent",
        }])
        monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)
        monkeypatch.setattr(_runtime, "_find_conflicting_memory", lambda *_args, **_kwargs: (_store_mod.LoadedMemory(
            frontmatter=_store_mod.MemoryFrontmatter(
                id="mem-old",
                created="2026-06-02T00:00:00+00:00",
                source="reflection",
                confidence="high",
            ),
            body="Old preference",
            source_path=Path("/tmp/old.md"),
            scope="user",
        ), 0.92))
        monkeypatch.setattr(_engine, "_is_correction", lambda _text: True)
        monkeypatch.setattr(_runtime, "_extract_keywords", lambda *_args, **_kwargs: ["ripgrep", "search", "tools"])
        monkeypatch.setattr(_runtime, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_runtime, "_embed_single", lambda *_: [1.0, 0.0])
        monkeypatch.setattr(_runtime, "_cosine_sim", lambda *_: 0.0)
        monkeypatch.setattr(_runtime, "_is_procedure", lambda _text: False)
        _runtime._current_session_memory_ids.ids = {"mem-session"}

        result = _engine._run_embedding_reflection([
            {"role": "user", "content": "Actually remember that I prefer ripgrep over grep now."},
            {"role": "assistant", "content": "Got it."},
        ])

        assert result["accepted_memories"]
        assert mem_store.check_conflict_calls[-1]["exclude_ids"] == ["mem-old", "mem-session"] or mem_store.check_conflict_calls[-1]["exclude_ids"] == ["mem-session", "mem-old"]
        assert mem_store.put_calls[0]["frontmatter"].supersedes == ["mem-old"]


def test_pending_skills_archive_cleanup(monkeypatch, tmp_path):
    """P3-6: pending-skills archive files are pruned by age and by count."""
    pending_path = tmp_path / "pending-skills.json"
    monkeypatch.setattr(_runtime, "PENDING_SKILLS_PATH", pending_path)

    now = datetime.now(timezone.utc).timestamp()
    old_time = now - 31 * 86400

    # Pre-create 12 recent archives with staggered mtimes.
    recent_archives = []
    for i in range(12):
        p = pending_path.with_suffix(f".202501{i + 1:02d}-000000.json")
        p.write_text("[]")
        recent_archives.append(p)
    for idx, p in enumerate(recent_archives):
        t = now - (11 - idx) * 60
        os.utime(p, (t, t))

    # Pre-create 2 old archives (>30 days).
    for i in range(2):
        p = pending_path.with_suffix(f".202410{i + 1:02d}-000000.json")
        p.write_text("[]")
        os.utime(p, (old_time, old_time))

    # Trigger an archive by writing more than the max pending items.
    pending_path.write_text(json.dumps([{"name": f"skill{i}"} for i in range(201)]))
    _runtime._save_pending_skill_candidates([])

    remaining = sorted(
        pending_path.parent.glob("pending-skills.*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Old archives must be removed regardless of count.
    assert all("202410" not in a.name for a in remaining)
    # Most-recent-10 cap must be enforced.
    assert len(remaining) == 10
