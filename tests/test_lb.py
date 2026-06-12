"""Tests for runtime/_lb.py late-binding helper and curator import refactor.

These tests are written before the implementation (RED phase) and verify the
design intent:
- A single _lb(name) helper resolves project modules, returning None on failure.
- Curator modules use this helper instead of bare except-pass blocks.
- Standalone module loading continues to work without raising.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _load_lb_directly():
    """Load runtime/_lb.py directly to avoid runtime/__init__.py side effects."""
    spec = importlib.util.spec_from_file_location(
        "_runtime_lb", str(_REPO / "runtime" / "_lb.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_runtime_lb"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestLateBindingHelper:
    """Tests for _lb(name) helper behavior."""

    def test_resolves_existing_module(self):
        """_lb returns the module object for an existing dotted name."""
        lb_mod = _load_lb_directly()
        mod = lb_mod._lb("builtins")
        assert mod is not None
        import builtins
        assert mod is builtins

    def test_returns_none_for_missing_module(self):
        """_lb returns None when the module cannot be imported."""
        lb_mod = _load_lb_directly()
        assert lb_mod._lb("definitely.nonexistent.module_xyz") is None

    def test_returns_none_for_invalid_name(self):
        """_lb returns None for syntactically invalid module names."""
        lb_mod = _load_lb_directly()
        assert lb_mod._lb("not a valid module name") is None

    def test_caches_successful_lookup(self):
        """Repeated lookups of the same module return the same object."""
        lb_mod = _load_lb_directly()
        first = lb_mod._lb("builtins")
        second = lb_mod._lb("builtins")
        assert first is second

    def test_returns_none_for_none_input(self):
        """_lb(None) returns None gracefully."""
        lb_mod = _load_lb_directly()
        assert lb_mod._lb(None) is None  # type: ignore[arg-type]


class TestCuratorStandaloneLoading:
    """Tests that curator modules load safely via importlib when used standalone."""

    def _load_standalone(self, rel_path: str):
        spec = importlib.util.spec_from_file_location(
            "_standalone_under_test", str(_REPO / rel_path)
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_standalone_under_test"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_helpers_load_standalone(self):
        """memory/curator/helpers.py loads without raising in standalone mode."""
        mod = self._load_standalone("memory/curator/helpers.py")
        assert hasattr(mod, "is_protected")
        assert hasattr(mod, "archive_and_delete")

    def test_cold_store_loads_standalone(self):
        """memory/curator/cold_store.py loads without raising in standalone mode."""
        mod = self._load_standalone("memory/curator/cold_store.py")
        assert hasattr(mod, "_load_cold_store")
        assert hasattr(mod, "_append_to_cold_store")

    def test_actions_load_standalone(self):
        """memory/curator/actions.py loads without raising in standalone mode."""
        mod = self._load_standalone("memory/curator/actions.py")
        assert hasattr(mod, "CuratorAction")
        assert hasattr(mod, "ArchiveStale")

    def test_report_loads_standalone(self):
        """memory/curator/report.py loads without raising in standalone mode."""
        mod = self._load_standalone("memory/curator/report.py")
        assert hasattr(mod, "generate_report")
