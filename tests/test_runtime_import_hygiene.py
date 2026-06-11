from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import mem_reflection_hermes as plugin
from mem_reflection_hermes import memory as memory_pkg
from mem_reflection_hermes.reflection import runtime as reflection_runtime
from mem_reflection_hermes.runtime import hooks as hooks_mod
from mem_reflection_hermes.runtime import tools as tools_mod


def test_runtime_hooks_session_recovery_runs_v15_stage_runners(monkeypatch):
    mem_store = object()
    ctx = object()
    calls = {"reflection": [], "compaction": [], "curator": [], "diagnostic": []}
    curator_mod = importlib.import_module("mem_reflection_hermes.memory.curator")

    monkeypatch.setattr(
        hooks_mod,
        "get_plugin_config_model",
        lambda: SimpleNamespace(
            checkpoint=SimpleNamespace(enabled=True, recover_on_session_start=True)
        ),
    )

    def _fake_lb(name):
        if name == "_get_mem_store":
            return lambda: mem_store
        if name == "_append_reflect_log":
            return lambda entry: calls["diagnostic"].append(entry)
        raise AssertionError(f"unexpected lazy binding: {name}")

    monkeypatch.setattr(hooks_mod, "_lb", _fake_lb)
    monkeypatch.setattr(hooks_mod, "_plugin_ctx", ctx)
    monkeypatch.setattr(
        hooks_mod,
        "_run_full_reflection",
        lambda seen_ctx, messages: calls["reflection"].append((seen_ctx, messages)),
    )
    monkeypatch.setattr(plugin, "_config_compaction", lambda: True)
    monkeypatch.setattr(
        reflection_runtime,
        "_compact_episode_zone",
        lambda seen_store, seen_ctx: calls["compaction"].append((seen_store, seen_ctx)),
    )
    fake_curator_enabled = lambda seen_store: seen_store is mem_store
    fake_run_curator = lambda seen_ctx, seen_store: calls["curator"].append((seen_ctx, seen_store))
    monkeypatch.setattr(curator_mod, "_curator_enabled", fake_curator_enabled)
    monkeypatch.setattr(curator_mod, "_run_curator", fake_run_curator)
    monkeypatch.setattr(memory_pkg, "_curator_enabled", fake_curator_enabled)
    monkeypatch.setattr(memory_pkg, "_run_curator", fake_run_curator)

    def _fake_recover(**kwargs):
        kwargs["reflection_runner"]("session-1", {"messages": [{"role": "user", "content": "hi"}]})
        kwargs["compaction_runner"]("session-1", {"messages": []})
        kwargs["curator_runner"]("session-1", {"messages": []})
        kwargs["diagnostic_logger"]({"stage": "recovered"})
        return {"reflection": 1, "compaction": 1, "curator": 1, "diagnostic": 1}

    monkeypatch.setattr(hooks_mod, "_checkpoint_recover_pending_work", _fake_recover)

    hooks_mod._on_session_start(session_id="session-1")

    assert calls["reflection"] == [(ctx, [{"role": "user", "content": "hi"}])]
    assert calls["compaction"] == [(mem_store, ctx)]
    assert calls["curator"] == [(ctx, mem_store)]
    assert calls["diagnostic"] == [{"stage": "recovered"}]


def test_runtime_tools_compile_profile_uses_runtime_bindings_and_writes_profile(monkeypatch, temp_dir):
    output_dir = temp_dir / "plugin-data"
    llm_calls = []

    class _FakeResult:
        content_type = "json"
        parsed = {"ok": True}
        text = "# Profile\n- keeps shipping\n"

    class _FakeLLM:
        def complete_structured(self, **kwargs):
            llm_calls.append(kwargs)
            return _FakeResult()

    class _FakeStore:
        def list_active(self):
            return [
                SimpleNamespace(
                    frontmatter=SimpleNamespace(pinned=False, confidence=0.8, zone="general"),
                    body="Prefers concise test summaries",
                    id=lambda: "mem-1",
                )
            ]

    def _fake_lb(name):
        mapping = {
            "_profile_mode_enabled": lambda: True,
            "_get_mem_store": lambda: _FakeStore(),
            "_palace_index_path": lambda: output_dir / "palace-index.md",
            "_sanitize_zone_filename": lambda zone: zone.replace(":", "_"),
            "save_zone_summary": lambda zone, content: None,
        }
        if name not in mapping:
            raise AssertionError(f"unexpected lazy binding: {name}")
        return mapping[name]

    monkeypatch.setattr(tools_mod, "_lb", _fake_lb)
    monkeypatch.setattr(tools_mod, "_plugin_data_dir", lambda: output_dir)

    result = tools_mod._compile_profile_via_llm(SimpleNamespace(llm=_FakeLLM()), mode="profile")

    expected_path = output_dir / "profile.md"
    assert result["success"] is True
    assert Path(result["path"]) == expected_path
    assert expected_path.read_text(encoding="utf-8") == "# Profile\n- keeps shipping"
    assert llm_calls and llm_calls[0]["purpose"] == "compile_profile"


def test_runtime_tools_search_handler_uses_late_binding_and_returns_json(monkeypatch, temp_dir):
    """Tool handler _tool_srh_memory_search dispatches through _lb and returns valid JSON."""
    import json

    class _FakeMemory:
        def __init__(self, mid, body, zone="general"):
            self._id = mid
            self.body = body
            self.scope = "user"
            self.frontmatter = SimpleNamespace(
                id=lambda: mid, zone=zone, pinned=False, confidence="medium",
                tags=[], supersedes=[], supersedes_reason="", valid_until="",
                rank=1, created="2026-01-01T00:00:00+00:00",
            )
        def id(self):
            return self._id

    class _FakeSearchIndex:
        def search(self, query, k=5, **kwargs):
            return [_FakeMemory("mem-1", "test result body")]

    class _FakeStore:
        def fusion_search(self, *args, **kwargs):
            return [_FakeMemory("mem-1", "test result body")]

        def is_superseded(self, mid):
            return False

    fake_store = _FakeStore()

    def _fake_lb(name):
        if name == "_get_mem_store":
            return lambda: fake_store
        if name == "_get_graph_neighbors":
            return lambda ids, **kw: []
        if name == "record_memory_stat":
            return lambda *a, **kw: None
        raise AssertionError(f"unexpected lazy binding: {name}")

    monkeypatch.setattr(tools_mod, "_lb", _fake_lb)

    result = tools_mod._tool_srh_memory_search({"query": "test query", "k": 3})
    parsed = json.loads(result)

    assert parsed.get("success") is True or "memories" in parsed or "results" in parsed
