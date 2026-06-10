"""test_hooks.py — Tests for v0.16.0 enhanced runtime hooks.

Coverage:
- _on_api_request_error: error counting and threshold logging
- _on_subagent_start / _on_subagent_stop: lifecycle tracking
- _on_session_reset: session rotation logging
- _ensure_session_state / _cleanup_session_state: state bag management

Run: pytest tests/test_hooks.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

# Build a minimal namespace so hooks.py can import .checkpoint and ..core.store
_PKG = "mem_reflection_hermes_hooks_test"


def _setup_namespace():
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_REPO)]
    sys.modules[_PKG] = pkg

    # core.store
    core_mod = types.ModuleType(f"{_PKG}.core")
    core_mod.__path__ = [str(_REPO / "core")]
    core_mod.__package__ = f"{_PKG}.core"
    sys.modules[f"{_PKG}.core"] = core_mod

    _spec_store = importlib.util.spec_from_file_location(f"{_PKG}.core.store", str(_REPO / "core" / "store.py"))
    _store = importlib.util.module_from_spec(_spec_store)
    sys.modules[f"{_PKG}.core.store"] = _store
    _spec_store.loader.exec_module(_store)

    # reflection.engine
    reflection_pkg = types.ModuleType(f"{_PKG}.reflection")
    reflection_pkg.__path__ = [str(_REPO / "reflection")]
    reflection_pkg.__package__ = f"{_PKG}.reflection"
    sys.modules[f"{_PKG}.reflection"] = reflection_pkg

    _spec_engine = importlib.util.spec_from_file_location(f"{_PKG}.reflection.engine", str(_REPO / "reflection" / "engine.py"))
    _engine = importlib.util.module_from_spec(_spec_engine)
    sys.modules[f"{_PKG}.reflection.engine"] = _engine
    _spec_engine.loader.exec_module(_engine)

    # runtime.checkpoint
    runtime_pkg = types.ModuleType(f"{_PKG}.runtime")
    runtime_pkg.__path__ = [str(_REPO / "runtime")]
    runtime_pkg.__package__ = f"{_PKG}.runtime"
    sys.modules[f"{_PKG}.runtime"] = runtime_pkg

    _spec_ckpt = importlib.util.spec_from_file_location(f"{_PKG}.runtime.checkpoint", str(_REPO / "runtime" / "checkpoint.py"))
    _ckpt = importlib.util.module_from_spec(_spec_ckpt)
    sys.modules[f"{_PKG}.runtime.checkpoint"] = _ckpt
    _spec_ckpt.loader.exec_module(_ckpt)

    # reflection.runtime
    _spec_rt = importlib.util.spec_from_file_location(f"{_PKG}.reflection.runtime", str(_REPO / "reflection" / "runtime.py"))
    _rt = importlib.util.module_from_spec(_spec_rt)
    sys.modules[f"{_PKG}.reflection.runtime"] = _rt
    _spec_rt.loader.exec_module(_rt)

    return _store, _ckpt


_setup_namespace()

_spec_hooks = importlib.util.spec_from_file_location(f"{_PKG}.runtime.hooks", str(_REPO / "runtime" / "hooks.py"))
_hooks = importlib.util.module_from_spec(_spec_hooks)
sys.modules[f"{_PKG}.runtime.hooks"] = _hooks
_spec_hooks.loader.exec_module(_hooks)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_session_states(monkeypatch):
    """Clear global session state before each test."""
    with _hooks._session_states_lock:
        _hooks._session_states.clear()
    yield
    with _hooks._session_states_lock:
        _hooks._session_states.clear()


# ---------------------------------------------------------------------------
# _ensure_session_state / _cleanup_session_state
# ---------------------------------------------------------------------------

class TestSessionStateManagement:
    def test_ensure_session_state_creates_default_bag(self):
        state = _hooks._ensure_session_state("sess-1")
        assert state["api_error_count"] == 0
        assert state["subagent_count"] == 0
        assert state["rewind_count"] == 0
        assert "created_at" in state

    def test_ensure_session_state_returns_existing(self):
        s1 = _hooks._ensure_session_state("sess-2")
        s1["api_error_count"] = 5
        s2 = _hooks._ensure_session_state("sess-2")
        assert s2["api_error_count"] == 5
        assert s1 is s2

    def test_cleanup_session_state_removes_from_memory(self):
        _hooks._ensure_session_state("sess-3")
        _hooks._cleanup_session_state("sess-3")
        assert "sess-3" not in _hooks._session_states


# ---------------------------------------------------------------------------
# _on_api_request_error
# ---------------------------------------------------------------------------

class TestApiRequestErrorHook:
    def test_no_session_id_is_noop(self, caplog):
        """Without session_id the hook returns immediately."""
        _hooks._on_api_request_error(session_id="", error={"type": "timeout"})
        # Should not crash; no state created
        assert not _hooks._session_states

    def test_error_count_increments(self):
        _hooks._on_api_request_error(session_id="sess-a", error={"type": "timeout"})
        state = _hooks._session_states["sess-a"]
        assert state["api_error_count"] == 1

        _hooks._on_api_request_error(session_id="sess-a", error={"type": "rate_limit"})
        assert state["api_error_count"] == 2

    def test_threshold_crossing_logged(self, caplog):
        """At thresholds 1, 5, 10, 25, 50 an info log is emitted."""
        import logging
        with caplog.at_level(logging.INFO):
            for i in range(6):
                _hooks._on_api_request_error(session_id="sess-b", error={"type": "timeout"})
        # Thresholds crossed: 1 and 5
        assert "hit 1 API errors" in caplog.text
        assert "hit 5 API errors" in caplog.text
        # 2,3,4 should not log
        assert "hit 2 API errors" not in caplog.text

    def test_non_threshold_values_not_logged(self, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            for i in range(4):
                _hooks._on_api_request_error(session_id="sess-c", error={"type": "timeout"})
        # Only threshold 1 should be logged
        assert caplog.text.count("API errors") == 1


# ---------------------------------------------------------------------------
# _on_subagent_start / _on_subagent_stop
# ---------------------------------------------------------------------------

class TestSubagentLifecycleHooks:
    def test_start_increments_active_count(self):
        _hooks._on_subagent_start(session_id="sess-d")
        state = _hooks._session_states["sess-d"]
        assert state["_subagent_active"] == 1
        assert "_subagent_start_time" in state

    def test_multiple_start_increments(self):
        _hooks._on_subagent_start(session_id="sess-e")
        _hooks._on_subagent_start(session_id="sess-e")
        assert _hooks._session_states["sess-e"]["_subagent_active"] == 2

    def test_stop_increments_total_count(self):
        _hooks._on_subagent_start(session_id="sess-f")
        _hooks._on_subagent_stop(session_id="sess-f")
        state = _hooks._session_states["sess-f"]
        assert state["subagent_count"] == 1

    def test_no_session_id_is_noop(self):
        """Missing session_id should not crash."""
        _hooks._on_subagent_start(session_id="")
        _hooks._on_subagent_stop(session_id="")
        assert not _hooks._session_states


# ---------------------------------------------------------------------------
# _on_session_reset
# ---------------------------------------------------------------------------

class TestSessionResetHook:
    def test_logs_rotation_parameters(self, caplog):
        import logging
        with caplog.at_level(logging.DEBUG):
            _hooks._on_session_reset(
                old_session_id="old-123",
                new_session_id="new-456",
                reason="token_limit",
            )
        assert "old-123" in caplog.text
        assert "new-456" in caplog.text
        assert "token_limit" in caplog.text

    def test_missing_ids_use_unknown(self, caplog):
        import logging
        with caplog.at_level(logging.DEBUG):
            _hooks._on_session_reset(reason="manual")
        # When IDs are missing, empty strings are logged (not "unknown")
        assert "session rotated" in caplog.text
        assert "reason=manual" in caplog.text
