"""test_checkpoint_backup_failure.py — Test corrupt checkpoint backup failure path.

Coverage:
- load_checkpoint() when os.replace() itself fails on corrupt file

Run: pytest tests/test_checkpoint_backup_failure.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PKG = "mem_reflection_hermes_ckpt_bak_test"


def _load_module():
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

    spec = importlib.util.spec_from_file_location(
        f"{_PKG}.runtime.checkpoint",
        str(_REPO / "runtime" / "checkpoint.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = f"{_PKG}.runtime"
    sys.modules[f"{_PKG}.runtime.checkpoint"] = mod
    spec.loader.exec_module(mod)
    return mod


_checkpoint = _load_module()


class TestCorruptBackupFailure:
    def test_corrupt_file_backup_failure_returns_defaults(self, monkeypatch, caplog):
        """When checkpoint is corrupt AND os.replace fails, return defaults and log warning."""
        import logging
        tmpdir = Path(tempfile.mkdtemp(prefix="hermes_ckpt_bakfail_"))
        try:
            checkpoint_file = tmpdir / "runtime-checkpoint.json"
            checkpoint_file.write_text("{not valid json", encoding="utf-8")
            monkeypatch.setattr(_checkpoint, "checkpoint_path", lambda: checkpoint_file)

            with patch("os.replace", side_effect=PermissionError("access denied")):
                with caplog.at_level(logging.WARNING, logger="mem_reflection_hermes.runtime.checkpoint"):
                    payload = _checkpoint.load_checkpoint()

            assert payload == _checkpoint._default_checkpoint()
            assert "could not be backed up" in caplog.text
            # Corrupt file should still be in place (replace failed)
            assert checkpoint_file.exists()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
