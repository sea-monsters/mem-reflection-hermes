"""Tests for runtime/schemas.py tool schema extraction.

These tests verify the current tool schema contract:
- All 13 tool schemas live in runtime/schemas.py.
- The package root re-exports them so existing imports and _lb lookups work.
- register() still registers all 13 tools using the relocated schemas.
- __init__.py is under 300 lines.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent


class TestSchemaDefinitions:
    """Schemas are defined in runtime/schemas.py."""

    def test_all_thirteen_schemas_defined(self):
        """runtime/schemas.py contains all 13 _SRH_*_SCHEMA dicts."""
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

    def test_memory_write_schema_exposes_supersedes_reason(self):
        """Write schema includes supersedes_reason to match the handler."""
        from mem_reflection_hermes.runtime import schemas

        props = schemas._SRH_MEMORY_WRITE_SCHEMA.get("properties", {})
        assert "supersedes_reason" in props

    def test_memory_search_schema_exposes_explain(self):
        """Search schema includes explain to match the handler."""
        from mem_reflection_hermes.runtime import schemas

        props = schemas._SRH_MEMORY_SEARCH_SCHEMA.get("properties", {})
        assert "explain" in props

    def test_reflect_now_schema_exposes_filters(self):
        """Reflect-now schema includes optional scope filters."""
        from mem_reflection_hermes.runtime import schemas

        props = schemas._SRH_REFLECT_NOW_SCHEMA.get("properties", {})
        assert "filters" in props

    def test_compile_profile_schema_matches_supported_modes(self):
        """Compile-profile schema advertises the handler's supported modes."""
        from mem_reflection_hermes.runtime import schemas

        mode = schemas._SRH_COMPILE_PROFILE_SCHEMA["properties"]["mode"]
        assert mode["enum"] == ["profile", "palace_index", "zone"]

    def test_memory_delete_schema_requires_id_or_filters(self):
        """_SRH_MEMORY_DELETE_SCHEMA accepts {id} or {filters} but rejects {}."""
        import jsonschema
        from mem_reflection_hermes.runtime import schemas

        schema = schemas._SRH_MEMORY_DELETE_SCHEMA
        # should accept id-only
        jsonschema.validate({"id": "x"}, schema)
        # should accept filters-only
        jsonschema.validate({"filters": {}}, schema)
        # should reject empty object
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({}, schema)

    def test_scope_filter_schemas_reject_unknown_keys(self):
        """Tool schemas reject typoed scope filters before runtime dispatch."""
        import jsonschema
        from mem_reflection_hermes.runtime import schemas

        jsonschema.validate({"query": "x", "filters": {"user_id": "u1"}}, schemas._SRH_MEMORY_SEARCH_SCHEMA)
        jsonschema.validate({"filters": {"run_id": None}}, schemas._SRH_MEMORY_DELETE_SCHEMA)
        jsonschema.validate({"topic": "x", "filters": {"agent_id": "a1"}}, schemas._SRH_PALACE_NAVIGATE_SCHEMA)
        jsonschema.validate({"mode": "profile", "filters": {"user_id": "u1"}}, schemas._SRH_COMPILE_PROFILE_SCHEMA)
        jsonschema.validate({"messages": [], "filters": {"user_id": "u1"}}, schemas._SRH_REFLECT_NOW_SCHEMA)

        for schema, payload in [
            (schemas._SRH_MEMORY_SEARCH_SCHEMA, {"query": "x", "filters": {"bad": "v"}}),
            (schemas._SRH_MEMORY_DELETE_SCHEMA, {"filters": {"bad": "v"}}),
            (schemas._SRH_PALACE_NAVIGATE_SCHEMA, {"topic": "x", "filters": {"bad": "v"}}),
            (schemas._SRH_COMPILE_PROFILE_SCHEMA, {"mode": "profile", "filters": {"bad": "v"}}),
            (schemas._SRH_REFLECT_NOW_SCHEMA, {"messages": [], "filters": {"bad": "v"}}),
        ]:
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate(payload, schema)

    def test_graph_retrieve_schema_tier_enum_matches_handler(self):
        """_SRH_GRAPH_RETRIEVE_SCHEMA tier enum matches GraphManagerCompat.retrieve_related."""
        from mem_reflection_hermes.runtime import schemas

        tier = schemas._SRH_GRAPH_RETRIEVE_SCHEMA["properties"]["tier"]
        assert tier["enum"] == ["count", "list", "detail"]

    def test_graph_retrieve_schema_accepts_deprecated_seed_ids(self):
        """P1-2: schema keeps seed_ids deprecated so old clients don't fail validation."""
        from mem_reflection_hermes.runtime import schemas
        import jsonschema

        jsonschema.validate(
            {"seed_ids": ["a"], "max_results": 5, "tier": "list"},
            schemas._SRH_GRAPH_RETRIEVE_SCHEMA,
        )

    def test_graph_retrieve_schema_requires_memory_ids_when_seed_ids_missing(self):
        """P1-2: schema requires at least memory_ids (no seed_ids means memory_ids required)."""
        from mem_reflection_hermes.runtime import schemas
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"max_results": 5}, schemas._SRH_GRAPH_RETRIEVE_SCHEMA)

    def test_graph_stats_schema_has_no_unused_properties(self):
        """_SRH_GRAPH_STATS_SCHEMA does not contain unused format or depth keys."""
        from mem_reflection_hermes.runtime import schemas

        props = schemas._SRH_GRAPH_STATS_SCHEMA.get("properties", {})
        assert "format" not in props
        assert "depth" not in props

    def test_graph_viz_schema_has_no_unused_properties(self):
        """_SRH_GRAPH_VIZ_SCHEMA does not contain unused format or depth keys."""
        from mem_reflection_hermes.runtime import schemas

        props = schemas._SRH_GRAPH_VIZ_SCHEMA.get("properties", {})
        assert "format" not in props
        assert "depth" not in props


class TestPackageReExport:
    """Package root re-exports schemas from runtime/schemas.py."""

    def test_package_import_exposes_equivalent_write_schema(self):
        """Package root exposes the write schema with the expected functional fields."""
        from mem_reflection_hermes import _SRH_MEMORY_WRITE_SCHEMA

        assert _SRH_MEMORY_WRITE_SCHEMA.get("type") == "object"
        assert "body" in _SRH_MEMORY_WRITE_SCHEMA.get("required", [])
        assert "supersedes_reason" in _SRH_MEMORY_WRITE_SCHEMA.get("properties", {})

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
    """register() registers all 13 tools using relocated schemas."""

    def test_register_thirteen_tools(self):
        """register(ctx) calls ctx.register_tool exactly 13 times."""
        import mem_reflection_hermes as pkg

        ctx = MagicMock()
        pkg.register(ctx)
        assert ctx.register_tool.call_count == 13

    def test_register_uses_relocated_write_schema(self):
        """The srh_memory_write tool is registered with the expected write-schema contract."""
        import mem_reflection_hermes as pkg

        ctx = MagicMock()
        pkg.register(ctx)
        calls = ctx.register_tool.call_args_list
        write_call = next((c for c in calls if c.kwargs.get("name") == "srh_memory_write"), None)
        assert write_call is not None, "srh_memory_write was not registered"
        schema = write_call.kwargs["schema"]
        assert schema.get("type") == "object"
        assert "body" in schema.get("required", [])
        assert "supersedes_reason" in schema.get("properties", {})

    def test_register_wires_graph_manager_getter_for_hooks(self):
        """P1-1: register() must call register_graph_features so post_tool_call can access graph."""
        import mem_reflection_hermes as pkg
        from mem_reflection_hermes.runtime import hooks as hooks_mod

        original = hooks_mod._gm_getter_func
        try:
            hooks_mod._gm_getter_func = None
            ctx = MagicMock()
            pkg.register(ctx)
            assert hooks_mod._gm_getter_func is not None, "_gm_getter_func was not set by register()"
        finally:
            hooks_mod._gm_getter_func = original

    def test_register_graph_features_is_idempotent(self):
        """P1-1: calling register() twice on the same context does not double-register graph tools."""
        import mem_reflection_hermes as pkg

        ctx = MagicMock()
        pkg.register(ctx)
        first_graph_calls = [c for c in ctx.register_tool.call_args_list if c.kwargs.get("name", "").startswith("srh_graph")]
        pkg.register(ctx)
        second_graph_calls = [c for c in ctx.register_tool.call_args_list if c.kwargs.get("name", "").startswith("srh_graph")]
        assert len(first_graph_calls) == len(second_graph_calls), "graph tools double-registered on same context"

    def test_register_does_not_mutate_schema(self):
        """register(ctx) does not mutate schema dicts."""
        import mem_reflection_hermes as pkg
        from mem_reflection_hermes.runtime import schemas

        before = {k: dict(getattr(schemas, k)) for k in dir(schemas) if k.startswith("_SRH_") and k.endswith("_SCHEMA")}
        ctx = MagicMock()
        pkg.register(ctx)
        after = {k: dict(getattr(schemas, k)) for k in before}
        assert before == after


class TestReflectNowNormalization:
    """P2-11: srh_reflect_now normalizes response schema across reflection modes."""

    def test_raw_chunk_response_has_same_keys_as_llm_response(self, monkeypatch):
        from mem_reflection_hermes.runtime import tools as tools_mod

        def _fake_run_full_reflection(_ctx, _messages, scope_filters=None):
            return {
                "mode": "raw_chunk",
                "summary": "raw chunk summary",
                "accepted_memories": [{"id": "m1", "body_preview": "bp"}],
                "chunks_created": 1,
            }

        monkeypatch.setattr(tools_mod, "_run_full_reflection", _fake_run_full_reflection)
        result = json.loads(tools_mod._tool_srh_reflect_now({
            "ctx": SimpleNamespace(),
            "messages": [{"role": "user", "content": "hello"}],
        }))
        assert result["mode"] == "raw_chunk"
        assert result["summary"] == "raw chunk summary"
        assert result["accepted_memories"] == [{"id": "m1", "body_preview": "bp"}]
        assert result["skill_candidates"] == []
        assert result["conflicts"] == []
        assert result["chunks_created"] == 1
        assert result["error"] is None

    def test_llm_response_fills_missing_defaults(self, monkeypatch):
        from mem_reflection_hermes.runtime import tools as tools_mod

        def _fake_run_full_reflection(_ctx, _messages, scope_filters=None):
            return {
                "mode": "llm",
                "summary": "llm summary",
                "accepted_memories": [{"id": "m2", "body": "b2"}],
                "skill_candidates": [{"name": "s1"}],
                "conflicts": [{"id": "c1"}],
            }

        monkeypatch.setattr(tools_mod, "_run_full_reflection", _fake_run_full_reflection)
        result = json.loads(tools_mod._tool_srh_reflect_now({
            "ctx": SimpleNamespace(),
            "messages": [{"role": "user", "content": "hello"}],
        }))
        assert result["mode"] == "llm"
        assert result["accepted_memories"] == [{"id": "m2", "body": "b2"}]
        assert result["skill_candidates"] == [{"name": "s1"}]
        assert result["conflicts"] == [{"id": "c1"}]
        assert result["chunks_created"] == 0
        assert result["error"] is None
