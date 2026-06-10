"""Tests for runtime/schemas.py tool schema extraction.

These tests are written before implementation (RED phase) and verify the
design intent:
- All 12 tool schemas live in runtime/schemas.py.
- The package root re-exports them so existing imports and _lb lookups work.
- register() still registers all 12 tools using the relocated schemas.
- __init__.py is under 300 lines.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent


class TestSchemaDefinitions:
    """Schemas are defined in runtime/schemas.py."""

    def test_all_twelve_schemas_defined(self):
        """runtime/schemas.py contains all 12 _SRH_*_SCHEMA dicts."""
        from mem_reflection_hermes.runtime import schemas

        expected = [
            "_SRH_MEMORY_WRITE_SCHEMA",
            "_SRH_MEMORY_SEARCH_SCHEMA",
            "_SRH_MEMORY_DELETE_SCHEMA",
            "_SRH_PALACE_NAVIGATE_SCHEMA",
            "_SRH_REFLECT_NOW_SCHEMA",
            "_SRH_SKILL_QUERY_SCHEMA",
            "_SRH_COMPILE_PROFILE_SCHEMA",
            "_SRH_ASSOCIATE_SCHEMA",
            "_SRH_GRAPH_RETRIEVE_SCHEMA",
            "_SRH_GRAPH_STATS_SCHEMA",
            "_SRH_GRAPH_VIZ_SCHEMA",
            "_SRH_MEMORY_HEALTH_SCHEMA",
        ]
        missing = [name for name in expected if not hasattr(schemas, name)]
        assert not missing, f"Missing schemas in runtime/schemas.py: {missing}"

    def test_schemas_are_dicts(self):
        """Each schema is a dict with 'type' == 'object' (or empty for health)."""
        from mem_reflection_hermes.runtime import schemas

        for name in dir(schemas):
            if name.startswith("_SRH_") and name.endswith("_SCHEMA"):
                value = getattr(schemas, name)
                assert isinstance(value, dict), f"{name} is not a dict"

    def test_memory_write_schema_has_required_body(self):
        """_SRH_MEMORY_WRITE_SCHEMA requires 'body'."""
        from mem_reflection_hermes.runtime import schemas

        schema = schemas._SRH_MEMORY_WRITE_SCHEMA
        assert schema.get("type") == "object"
        assert "body" in schema.get("required", [])
        assert "properties" in schema


class TestPackageReExport:
    """Package root re-exports schemas from runtime/schemas.py."""

    def test_package_import_returns_same_object(self):
        """from mem_reflection_hermes import X returns the same dict as runtime.schemas.X."""
        from mem_reflection_hermes import _SRH_MEMORY_WRITE_SCHEMA
        from mem_reflection_hermes.runtime import schemas

        assert _SRH_MEMORY_WRITE_SCHEMA is schemas._SRH_MEMORY_WRITE_SCHEMA

    def test_package_has_all_schema_names(self):
        """mem_reflection_hermes exposes all 12 schema names."""
        import mem_reflection_hermes as pkg

        expected = [
            "_SRH_MEMORY_WRITE_SCHEMA",
            "_SRH_MEMORY_SEARCH_SCHEMA",
            "_SRH_MEMORY_DELETE_SCHEMA",
            "_SRH_PALACE_NAVIGATE_SCHEMA",
            "_SRH_REFLECT_NOW_SCHEMA",
            "_SRH_SKILL_QUERY_SCHEMA",
            "_SRH_COMPILE_PROFILE_SCHEMA",
            "_SRH_ASSOCIATE_SCHEMA",
            "_SRH_GRAPH_RETRIEVE_SCHEMA",
            "_SRH_GRAPH_STATS_SCHEMA",
            "_SRH_GRAPH_VIZ_SCHEMA",
            "_SRH_MEMORY_HEALTH_SCHEMA",
        ]
        missing = [name for name in expected if not hasattr(pkg, name)]
        assert not missing, f"Missing package-level schema attributes: {missing}"


class TestLateBinding:
    """_lb lookups for schemas continue to work."""

    def test_lb_resolves_schema_symbol(self):
        """_lb('_SRH_MEMORY_WRITE_SCHEMA') returns the schema dict."""
        from mem_reflection_hermes.runtime._lb import _lb
        from mem_reflection_hermes import _SRH_MEMORY_WRITE_SCHEMA

        assert _lb("_SRH_MEMORY_WRITE_SCHEMA") is _SRH_MEMORY_WRITE_SCHEMA


class TestRegisterBehavior:
    """register() registers all 12 tools using relocated schemas."""

    def test_register_twelve_tools(self):
        """register(ctx) calls ctx.register_tool exactly 12 times."""
        import mem_reflection_hermes as pkg

        ctx = MagicMock()
        pkg.register(ctx)
        assert ctx.register_tool.call_count == 12

    def test_register_uses_relocated_write_schema(self):
        """The srh_memory_write tool is registered with the runtime.schemas dict."""
        import mem_reflection_hermes as pkg
        from mem_reflection_hermes.runtime import schemas

        ctx = MagicMock()
        pkg.register(ctx)
        calls = ctx.register_tool.call_args_list
        write_call = next((c for c in calls if c.kwargs.get("name") == "srh_memory_write"), None)
        assert write_call is not None, "srh_memory_write was not registered"
        assert write_call.kwargs["schema"] is schemas._SRH_MEMORY_WRITE_SCHEMA

    def test_register_does_not_mutate_schema(self):
        """register(ctx) does not mutate schema dicts."""
        import mem_reflection_hermes as pkg
        from mem_reflection_hermes.runtime import schemas

        before = {k: dict(getattr(schemas, k)) for k in dir(schemas) if k.startswith("_SRH_") and k.endswith("_SCHEMA")}
        ctx = MagicMock()
        pkg.register(ctx)
        after = {k: dict(getattr(schemas, k)) for k in before}
        assert before == after


class TestEntrypointSize:
    """__init__.py stays under 300 lines."""

    def test_init_under_300_lines(self):
        """Package entrypoint is under 300 lines after extraction."""
        init_path = _REPO / "__init__.py"
        lines = init_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) < 300, f"__init__.py has {len(lines)} lines; must be under 300"
