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
import sys
import types
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# ---------------------------------------------------------------------------
# Load reflection.engine functions via importlib to handle relative imports
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parent.parent

# Ensure core is importable (no relative imports)
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import core as _core_mod

# Set up minimal package namespace for reflection.engine's relative imports
_PKG = "mem_reflection_hermes_reflection_test"


def _restore_modules(previous: Dict[str, Optional[types.ModuleType]]) -> None:
    for name, module in previous.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _load_reflection_engine() -> tuple[object | None, Exception | None, Exception | None]:
    touched_modules = [
        _PKG,
        f"{_PKG}.core",
        f"{_PKG}.search",
        f"{_PKG}.search.embed",
        "search.embed",
        f"{_PKG}.reflection",
        f"{_PKG}.reflection.engine",
    ]
    previous_modules = {name: sys.modules.get(name) for name in touched_modules}
    embed_load_error = None
    engine_load_error = None
    engine = None

    try:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_REPO)]
        sys.modules[_PKG] = pkg
        sys.modules[f"{_PKG}.core"] = _core_mod

        embed_path = _REPO / "search" / "embed.py"
        if embed_path.exists():
            search_pkg = types.ModuleType(f"{_PKG}.search")
            search_pkg.__path__ = [str(_REPO / "search")]
            sys.modules[f"{_PKG}.search"] = search_pkg
            spec = importlib.util.spec_from_file_location(
                f"{_PKG}.search.embed", str(embed_path))
            if spec and spec.loader:
                embed_module = importlib.util.module_from_spec(spec)
                embed_module.__package__ = f"{_PKG}.search"
                sys.modules[f"{_PKG}.search.embed"] = embed_module
                sys.modules["search.embed"] = embed_module
                try:
                    spec.loader.exec_module(embed_module)
                except Exception as exc:
                    embed_load_error = exc

        engine_path = _REPO / "reflection" / "engine.py"
        if engine_path.exists():
            reflection_pkg = types.ModuleType(f"{_PKG}.reflection")
            reflection_pkg.__path__ = [str(_REPO / "reflection")]
            sys.modules[f"{_PKG}.reflection"] = reflection_pkg

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

        return engine, embed_load_error, engine_load_error
    finally:
        _restore_modules(previous_modules)


def _load_module_from_repo(module_name: str, relative_path: str) -> object:
    touched_modules = [
        _PKG,
        f"{_PKG}.core",
        f"{_PKG}.search",
        f"{_PKG}.search.embed",
        "search.embed",
        f"{_PKG}.reflection",
        f"{_PKG}.reflection.engine",
        f"{_PKG}.hooks",
        f"{_PKG}.hooks.lifecycle",
    ]
    previous_modules = {name: sys.modules.get(name) for name in touched_modules}

    try:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_REPO)]
        sys.modules[_PKG] = pkg
        sys.modules[f"{_PKG}.core"] = _core_mod

        search_pkg = types.ModuleType(f"{_PKG}.search")
        search_pkg.__path__ = [str(_REPO / "search")]
        sys.modules[f"{_PKG}.search"] = search_pkg
        embed_spec = importlib.util.spec_from_file_location(
            f"{_PKG}.search.embed", str(_REPO / "search" / "embed.py"))
        if embed_spec and embed_spec.loader:
            embed_module = importlib.util.module_from_spec(embed_spec)
            embed_module.__package__ = f"{_PKG}.search"
            sys.modules[f"{_PKG}.search.embed"] = embed_module
            sys.modules["search.embed"] = embed_module
            embed_spec.loader.exec_module(embed_module)

        reflection_pkg = types.ModuleType(f"{_PKG}.reflection")
        reflection_pkg.__path__ = [str(_REPO / "reflection")]
        sys.modules[f"{_PKG}.reflection"] = reflection_pkg
        reflection_spec = importlib.util.spec_from_file_location(
            f"{_PKG}.reflection.engine", str(_REPO / "reflection" / "engine.py"))
        if reflection_spec and reflection_spec.loader:
            reflection_module = importlib.util.module_from_spec(reflection_spec)
            reflection_module.__package__ = f"{_PKG}.reflection"
            sys.modules[f"{_PKG}.reflection.engine"] = reflection_module
            reflection_spec.loader.exec_module(reflection_module)

        hooks_pkg = types.ModuleType(f"{_PKG}.hooks")
        hooks_pkg.__path__ = [str(_REPO / "hooks")]
        sys.modules[f"{_PKG}.hooks"] = hooks_pkg
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


_engine, _embed_load_error, _engine_load_error = _load_reflection_engine()
_lifecycle_mod = _load_module_from_repo(f"{_PKG}.hooks.lifecycle", "hooks/lifecycle.py")

if _embed_load_error is not None:
    raise RuntimeError("Could not load search.embed for reflection tests") from _embed_load_error
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

    def list_active(self):
        return []

    def check_conflict(self, body: str, exclude_ids=None):
        self.check_conflict_calls.append({
            "body": body,
            "exclude_ids": list(exclude_ids or []),
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
    def test_pre_llm_call_keeps_counter_when_llm_reflection_skips_without_ctx(self, monkeypatch):
        monkeypatch.setattr(_lifecycle_mod, "_micro_reflection_enabled", lambda: True)
        monkeypatch.setattr(_lifecycle_mod, "_is_explicit_memory_intent", lambda _text: False)
        monkeypatch.setattr(_lifecycle_mod, "_reflection_mode", lambda: "llm")
        monkeypatch.setattr(_lifecycle_mod, "_build_context_block", lambda _query: None)
        monkeypatch.setattr(_lifecycle_mod, "_run_micro_reflection", lambda *_args, **_kwargs: pytest.fail("should not run"))
        monkeypatch.setattr(_lifecycle_mod, "_plugin_ctx", None)
        with _lifecycle_mod._turns_since_reflect_lock:
            _lifecycle_mod._turns_since_reflect = 3

        result = _lifecycle_mod._pre_llm_call(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            session_id="test-session",
        )

        assert result is None
        with _lifecycle_mod._turns_since_reflect_lock:
            assert _lifecycle_mod._turns_since_reflect == 3


@skip_no_engine
class TestReflectionSupersedesRegression:
    def test_full_reflection_excludes_superseded_ids_from_conflict_check(self, monkeypatch):
        mem_store = _RecordingMemStore()
        monkeypatch.setattr(_engine, "_reflection_mode", lambda: "llm")
        monkeypatch.setattr(_engine, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_engine, "_get_skill_store", lambda: _EmptySkillStore())
        monkeypatch.setattr(_engine, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_engine, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_engine, "_save_pending_skill_candidates", lambda *_: None)
        monkeypatch.setattr(_engine, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)

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

        result = _engine._run_full_reflection(ctx, [{"role": "user", "content": "remember this"}])

        assert result["accepted_memories"]
        assert mem_store.check_conflict_calls[0]["exclude_ids"] == ["mem-old"]
        assert mem_store.put_calls[0]["frontmatter"].supersedes == ["mem-old"]

    def test_full_reflection_remembers_new_memory_ids_for_session_exclusion(self, monkeypatch):
        mem_store = _RecordingMemStore()
        monkeypatch.setattr(_engine, "_reflection_mode", lambda: "llm")
        monkeypatch.setattr(_engine, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_engine, "_get_skill_store", lambda: _EmptySkillStore())
        monkeypatch.setattr(_engine, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_engine, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_engine, "_save_pending_skill_candidates", lambda *_: None)
        monkeypatch.setattr(_engine, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)
        _engine._reset_current_session_memory_ids()

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
        assert accepted_id in _engine._get_current_session_memory_ids()
        _engine._reset_current_session_memory_ids()

    def test_micro_reflection_excludes_superseded_ids_from_conflict_check(self, monkeypatch):
        mem_store = _RecordingMemStore()
        monkeypatch.setattr(_engine, "_reflection_mode", lambda: "llm")
        monkeypatch.setattr(_engine, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_engine, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_engine, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_engine, "_compute_novelty_score", lambda *_args, **_kwargs: 0.8)

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

        parsed = _engine._run_micro_reflection(ctx, "remember my new editor", "noted")

        assert parsed is not None
        assert mem_store.check_conflict_calls[0]["exclude_ids"] == ["mem-editor-old"]
        assert mem_store.put_calls[0]["frontmatter"].supersedes == ["mem-editor-old"]

    def test_embedding_reflection_uses_summary_fallback_for_skill_metadata(self, monkeypatch):
        mem_store = _RecordingMemStore()
        monkeypatch.setattr(_engine, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_engine, "_get_skill_store", lambda: _EmptySkillStore())
        monkeypatch.setattr(_engine, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_engine, "_save_pending_skill_candidates", lambda *_: None)
        monkeypatch.setattr(_engine, "_extract_facts_from_turn", lambda *_: [])
        monkeypatch.setattr(_engine, "_compute_novelty_score", lambda *_args, **_kwargs: 0.8)
        monkeypatch.setattr(_engine, "_extract_keywords", lambda text, top_k=3: ["deploy", "cache", "warmup"] if "summary" in text else ["assistant", "steps"])
        monkeypatch.setattr(_engine, "_generate_session_summary", lambda _text: "summary fallback for session generated memory")
        monkeypatch.setattr(_engine, "_is_procedure", lambda _text: True)
        monkeypatch.setattr(_engine, "_generate_skill_name", lambda _text: "deploy-cache-warmup")
        monkeypatch.setattr(_engine, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_engine, "_embed_single", lambda *_: [1.0, 0.0])
        monkeypatch.setattr(_engine, "_cosine_sim", lambda *_: 0.0)
        _engine._current_session_memory_ids.ids = set()

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
        monkeypatch.setattr(_engine, "_get_mem_store", lambda: mem_store)
        monkeypatch.setattr(_engine, "_get_skill_store", lambda: _EmptySkillStore())
        monkeypatch.setattr(_engine, "_append_reflect_log", lambda *_: None)
        monkeypatch.setattr(_engine, "_save_pending_skill_candidates", lambda *_: None)
        monkeypatch.setattr(_engine, "_extract_facts_from_turn", lambda *_: [{
            "text": "I now prefer ripgrep over grep for repository search.",
            "confidence": "high",
            "rationale": "preference",
            "source": "explicit_intent",
        }])
        monkeypatch.setattr(_engine, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)
        monkeypatch.setattr(_engine, "_find_conflicting_memory", lambda *_args, **_kwargs: (_core_mod.LoadedMemory(
            frontmatter=_core_mod.MemoryFrontmatter(
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
        monkeypatch.setattr(_engine, "_extract_keywords", lambda *_args, **_kwargs: ["ripgrep", "search", "tools"])
        monkeypatch.setattr(_engine, "_validate_supersedes_targets", lambda *_: None)
        monkeypatch.setattr(_engine, "_embed_single", lambda *_: [1.0, 0.0])
        monkeypatch.setattr(_engine, "_cosine_sim", lambda *_: 0.0)
        monkeypatch.setattr(_engine, "_is_procedure", lambda _text: False)
        _engine._current_session_memory_ids.ids = {"mem-session"}

        result = _engine._run_embedding_reflection([
            {"role": "user", "content": "Actually remember that I prefer ripgrep over grep now."},
            {"role": "assistant", "content": "Got it."},
        ])

        assert result["accepted_memories"]
        assert mem_store.check_conflict_calls[-1]["exclude_ids"] == ["mem-old", "mem-session"] or mem_store.check_conflict_calls[-1]["exclude_ids"] == ["mem-session", "mem-old"]
        assert mem_store.put_calls[0]["frontmatter"].supersedes == ["mem-old"]
