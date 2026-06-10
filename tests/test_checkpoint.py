from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path
import shutil


_REPO = Path(__file__).resolve().parent.parent
_PKG = "mem_reflection_hermes_checkpoint_test"


def _setup_pkg_namespace():
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_REPO)]
    sys.modules[_PKG] = pkg

    core_mod = types.ModuleType(f"{_PKG}.core")
    core_mod.__path__ = [str(_REPO / "core")]
    core_mod.__package__ = f"{_PKG}.core"
    sys.modules[f"{_PKG}.core"] = core_mod

    import core.store as store_mod
    sys.modules[f"{_PKG}.core.store"] = store_mod

    runtime_pkg = types.ModuleType(f"{_PKG}.runtime")
    runtime_pkg.__path__ = [str(_REPO / "runtime")]
    runtime_pkg.__package__ = f"{_PKG}.runtime"
    sys.modules[f"{_PKG}.runtime"] = runtime_pkg


def _load_checkpoint_module():
    _setup_pkg_namespace()
    spec = importlib.util.spec_from_file_location(
        f"{_PKG}.runtime.checkpoint",
        str(_REPO / "runtime" / "checkpoint.py"),
    )
    if not spec or not spec.loader:
        raise RuntimeError("Could not load runtime.checkpoint")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = f"{_PKG}.runtime"
    sys.modules[f"{_PKG}.runtime.checkpoint"] = module
    spec.loader.exec_module(module)
    return module


_checkpoint = _load_checkpoint_module()


class TestCheckpointPersistence:
    def test_corrupt_checkpoint_is_backed_up_and_defaults_returned(self, monkeypatch):
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_checkpoint_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            checkpoint_file.write_text("{not valid json", encoding="utf-8")
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)

            payload = _checkpoint.load_checkpoint()

            assert payload["pending_reflections"] == {}
            backups = list(tmpdir.glob("runtime-checkpoint.corrupt-*.json"))
            assert backups, "Corrupt checkpoint should be backed up"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_recover_pending_work_runs_available_stages_and_clears_them(self, monkeypatch):
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_checkpoint_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)
            _checkpoint.write_checkpoint({
                "session_states": {"s1": {"api_error_count": 2}},
                "pending_reflections": {
                    "s1": {"messages": [{"role": "user", "content": "remember"}]},
                    "s2": {},
                },
                "pending_compactions": {"s1": {"message_count": 3}},
                "pending_curator_runs": {"s1": {"message_count": 3}},
                "last_completed": {},
            })

            calls = {"reflection": [], "compaction": [], "curator": [], "diagnostic": []}

            recovered = _checkpoint.recover_pending_work(
                reflection_runner=lambda sid, entry: calls["reflection"].append((sid, entry)),
                compaction_runner=lambda sid, entry: calls["compaction"].append((sid, entry)),
                curator_runner=lambda sid, entry: calls["curator"].append((sid, entry)),
                diagnostic_logger=lambda entry: calls["diagnostic"].append(entry),
            )

            assert recovered["reflection"] == 1
            assert recovered["compaction"] == 1
            assert recovered["curator"] == 1
            assert recovered["diagnostic"] == 1
            assert calls["reflection"][0][0] == "s1"

            final_payload = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            assert final_payload["pending_reflections"] == {}
            assert final_payload["pending_compactions"] == {}
            assert final_payload["pending_curator_runs"] == {}
            assert final_payload["last_completed"]["reflection"]["recovered"] is True
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_atomic_write_is_not_corrupt_on_read(self, monkeypatch):
        """P0: Atomic write via tempfile + os.replace should never expose half-written state."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_atomic_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)

            payload = {"session_states": {"s1": {"state": "valid"}}, "pending_reflections": {}}
            _checkpoint.write_checkpoint(payload)

            loaded = _checkpoint.load_checkpoint()
            assert loaded["session_states"]["s1"]["state"] == "valid"

            # Verify the tempfile was cleaned up (no .tmp files left)
            tmp_files = list(tmpdir.glob("*.tmp"))
            assert not tmp_files, f"Temp file leak: {tmp_files}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_load_checkpoint_empty_file_returns_defaults(self, monkeypatch):
        """P0: Empty file should be treated as corrupt and return defaults."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_empty_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            checkpoint_file.write_text("", encoding="utf-8")
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)

            loaded = _checkpoint.load_checkpoint()
            assert loaded == _checkpoint._default_checkpoint()
            backups = list(tmpdir.glob("runtime-checkpoint.corrupt-*.json"))
            assert backups, "Empty file should be backed up as corrupt"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_load_checkpoint_empty_object_returns_defaults(self, monkeypatch):
        """P0: Empty object {} should merge with defaults, preserving empty dict buckets."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_emptyobj_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            checkpoint_file.write_text("{}", encoding="utf-8")
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)

            loaded = _checkpoint.load_checkpoint()
            assert loaded["session_states"] == {}
            assert loaded["pending_reflections"] == {}
            assert loaded["pending_compactions"] == {}
            assert loaded["pending_curator_runs"] == {}
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_recover_pending_work_keeps_pending_on_runner_failure(self, monkeypatch):
        """P0: When runner throws, pending should NOT be cleared so next recovery can retry."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_recover_fail_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)
            _checkpoint.write_checkpoint({
                "session_states": {},
                "pending_reflections": {
                    "s1": {"messages": [{"role": "user", "content": "test"}]},
                },
                "pending_compactions": {},
                "pending_curator_runs": {},
                "last_completed": {},
            })

            def _failing_runner(_sid, _entry):
                raise RuntimeError("simulated failure")

            recovered = _checkpoint.recover_pending_work(
                reflection_runner=_failing_runner,
            )

            # Should report 0 recovered, not crash
            assert recovered["reflection"] == 0

            # Pending should still be in checkpoint (not cleared)
            loaded = _checkpoint.load_checkpoint()
            assert "s1" in loaded["pending_reflections"]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_mark_stage_completed_records_last_completed(self, monkeypatch):
        """P0: Completing a stage should record it in last_completed and clear pending."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_completed_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)
            _checkpoint.write_checkpoint(_checkpoint._default_checkpoint())

            _checkpoint.mark_pending_stage("s1", "reflection", {"messages": []})
            _checkpoint.mark_stage_completed("s1", "reflection", {"result": "ok"})

            loaded = _checkpoint.load_checkpoint()
            assert loaded["pending_reflections"] == {}
            assert loaded["last_completed"]["reflection"]["stage"] == "reflection"
            assert loaded["last_completed"]["reflection"]["result"] == "ok"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_json_safe_handles_non_serializable_values(self):
        """P0: _json_safe should convert non-JSON values to strings without crashing."""
        result = _checkpoint._json_safe({
            "valid": "string",
            "nested": {"bytes": b"raw", "set": {1, 2}},
            "list": [1, b"item"],
        })
        assert result["valid"] == "string"
        assert isinstance(result["nested"]["bytes"], str)
        assert isinstance(result["nested"]["set"], str)
        assert isinstance(result["list"][1], str)

    def test_snapshot_and_clear_session_state_roundtrip(self, monkeypatch):
        """P0: snapshot -> load -> clear should produce clean state."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_snap_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)

            _checkpoint.snapshot_session_state("s1", {"api_error_count": 3, "subagent_count": 2})
            loaded = _checkpoint.load_checkpoint()
            assert loaded["session_states"]["s1"]["api_error_count"] == 3

            _checkpoint.clear_session_state("s1")
            loaded2 = _checkpoint.load_checkpoint()
            assert "s1" not in loaded2["session_states"]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestClearPendingStage:
    """Gap D: clear_pending_stage is a standalone public API with zero test coverage.

    Design intent: cancel a pending stage without marking it completed.
    Used when a stage should be abandoned (e.g., stale data, skip recovery).
    """

    def test_clear_existing_pending_stage(self, monkeypatch):
        """Clearing an existing pending stage should remove it from checkpoint."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_clear_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)
            _checkpoint.write_checkpoint({
                "session_states": {},
                "pending_reflections": {
                    "s1": {"messages": [{"role": "user", "content": "test"}]},
                },
                "pending_compactions": {
                    "s1": {"message_count": 5},
                },
                "pending_curator_runs": {},
                "last_completed": {},
            })

            result = _checkpoint.clear_pending_stage("s1", "reflection")

            loaded = _checkpoint.load_checkpoint()
            assert "s1" not in loaded["pending_reflections"]
            # Compaction should be untouched
            assert "s1" in loaded["pending_compactions"]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_clear_nonexistent_pending_stage_is_noop(self, monkeypatch):
        """Clearing a stage that doesn't exist should not crash."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_clear_noop_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)
            _checkpoint.write_checkpoint(_checkpoint._default_checkpoint())

            result = _checkpoint.clear_pending_stage("nonexistent", "reflection")

            loaded = _checkpoint.load_checkpoint()
            assert loaded["pending_reflections"] == {}
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_clear_compaction_and_curator_stages(self, monkeypatch):
        """Clear pending stages for compaction and curator (not just reflection)."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_clear_multi_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)
            _checkpoint.write_checkpoint({
                "session_states": {},
                "pending_reflections": {},
                "pending_compactions": {"s1": {"message_count": 3}},
                "pending_curator_runs": {"s2": {"message_count": 7}},
                "last_completed": {},
            })

            _checkpoint.clear_pending_stage("s1", "compaction")
            _checkpoint.clear_pending_stage("s2", "curator")

            loaded = _checkpoint.load_checkpoint()
            assert loaded["pending_compactions"] == {}
            assert loaded["pending_curator_runs"] == {}
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestCheckpointMaxPendingEnforcement:
    """Gap 2: Verify recover_pending_work respects max_pending_sessions cap.

    Design intent (FEP §8 config draft): max_pending_sessions defaults to 20.
    When more sessions have pending work than the cap allows, only the most
    recent N should be recovered — preventing unbounded recovery storms after
    a long outage.
    """

    def _write_many_pending(self, checkpoint_file, count, stage="reflection"):
        """Write a checkpoint with N pending sessions for the given stage."""
        bucket = f"pending_{stage}s"
        pending = {}
        for i in range(count):
            pending[f"s-{i:03d}"] = {
                "messages": [{"role": "user", "content": f"msg-{i}"}],
                "updated_at": f"2026-06-09T00:{i % 60:02d}:00+00:00",
            }
        _checkpoint.write_checkpoint({
            "session_states": {},
            "pending_reflections": pending if stage == "reflection" else {},
            "pending_compactions": pending if stage == "compaction" else {},
            "pending_curator_runs": pending if stage == "curator" else {},
            "last_completed": {},
        })

    def test_recovery_caps_at_max_pending_sessions_default_20(self, monkeypatch):
        """With 25 pending, only 20 should be recovered (default cap)."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_maxpend_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)
            self._write_many_pending(checkpoint_file, count=25, stage="reflection")

            recovered_ids = []

            def _recording_runner(sid, entry):
                recovered_ids.append(sid)

            result = _checkpoint.recover_pending_work(
                reflection_runner=_recording_runner,
                max_pending_sessions=20,
            )

            assert result["reflection"] == 20
            assert len(recovered_ids) == 20
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_recovery_all_when_under_cap(self, monkeypatch):
        """With 15 pending and cap=20, all 15 should be recovered."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_maxpend_under_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)
            self._write_many_pending(checkpoint_file, count=15, stage="reflection")

            recovered_ids = []

            def _recording_runner(sid, entry):
                recovered_ids.append(sid)

            result = _checkpoint.recover_pending_work(
                reflection_runner=_recording_runner,
                max_pending_sessions=20,
            )

            assert result["reflection"] == 15
            assert len(recovered_ids) == 15
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_recovery_prefers_most_recent_pending_sessions(self, monkeypatch):
        """When capped, sessions with latest updated_at should be recovered first."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_maxpend_recent_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)

            # Write 5 pending with ascending timestamps
            pending = {}
            for i in range(5):
                pending[f"s-{i:03d}"] = {
                    "messages": [{"role": "user", "content": f"msg-{i}"}],
                    "updated_at": f"2026-06-09T00:{i * 10:02d}:00+00:00",
                }
            _checkpoint.write_checkpoint({
                "session_states": {},
                "pending_reflections": pending,
                "pending_compactions": {},
                "pending_curator_runs": {},
                "last_completed": {},
            })

            recovered_ids = []

            def _recording_runner(sid, entry):
                recovered_ids.append(sid)

            result = _checkpoint.recover_pending_work(
                reflection_runner=_recording_runner,
                max_pending_sessions=3,
            )

            assert result["reflection"] == 3
            # Should recover the 3 most recent (s-002, s-003, s-004)
            assert "s-004" in recovered_ids
            assert "s-003" in recovered_ids
            assert "s-002" in recovered_ids
            # Oldest should NOT be recovered
            assert "s-000" not in recovered_ids
            assert "s-001" not in recovered_ids
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_no_cap_when_max_pending_is_zero(self, monkeypatch):
        """max_pending_sessions=0 should recover all (cap disabled)."""
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_maxpend_nocap_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)
            self._write_many_pending(checkpoint_file, count=25, stage="reflection")

            recovered_ids = []

            def _recording_runner(sid, entry):
                recovered_ids.append(sid)

            result = _checkpoint.recover_pending_work(
                reflection_runner=_recording_runner,
                max_pending_sessions=0,
            )

            assert result["reflection"] == 25
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
