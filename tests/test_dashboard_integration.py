"""Integration test for dashboard graph manager resolution in package mode.

This test verifies that web/api.py can resolve a real graph manager when the
package is properly imported, without relying on the sys.modules mock used by
test_dashboard.py.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False


def _reset_graph_manager_singleton():
    """Clear the runtime graph manager singleton between tests."""
    try:
        import mem_reflection_hermes.runtime.graph as _rt_graph
        _rt_graph._graph_manager_compat = None
    except Exception:
        pass


def _ensure_package_namespace(repo_root: Path) -> tuple:
    """Make sure mem_reflection_hermes package namespace exists in sys.modules."""
    pkg_name = "mem_reflection_hermes"
    if pkg_name not in sys.modules:
        spec = __import__("importlib.util").util.spec_from_file_location(
            pkg_name, str(repo_root / "__init__.py")
        )
        if spec is None or spec.loader is None:
            pytest.skip("Cannot load package __init__.py")
        pkg = __import__("importlib.util").util.module_from_spec(spec)
        sys.modules[pkg_name] = pkg
        spec.loader.exec_module(pkg)
    from mem_reflection_hermes.core import store as _store_module
    return _store_module


@pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
def test_dashboard_get_graph_interface_in_package_mode():
    """_get_graph_interface() should return a real GraphManagerCompat in package mode."""
    repo_root = Path(__file__).resolve().parent.parent
    _store_module = _ensure_package_namespace(repo_root)

    with tempfile.TemporaryDirectory(prefix="hermes_dash_int_") as tmpdir:
        data_dir = Path(tmpdir)
        MemoryStore = _store_module.MemoryStore
        plugin_data_dir = _store_module.plugin_data_dir
        from mem_reflection_hermes.core.graph import GraphIndex
        from mem_reflection_hermes.core.models import MemoryFrontmatter
        try:
            store = MemoryStore(
                user_root=data_dir / "memories",
                db_path=data_dir / "memories.db",
            )
            graph = GraphIndex(data_dir / "graph.db")
            store.set_graph(graph)

            fm = MemoryFrontmatter.new(source="dashboard-test", zone="general")
            store.put("user", fm, "integration memory")

            # Load web/api.py as a real submodule of the package.
            spec = __import__("importlib.util").util.spec_from_file_location(
                "mem_reflection_hermes.web.api", str(repo_root / "web" / "api.py")
            )
            assert spec is not None and spec.loader is not None
            mod = __import__("importlib.util").util.module_from_spec(spec)
            sys.modules["mem_reflection_hermes.web.api"] = mod
            spec.loader.exec_module(mod)
            # Point plugin data dir to temp dir so _get_graph_interface uses the right db.
            mod.srh._plugin_data_dir = lambda: data_dir
            mod.srh._get_mem_store = lambda: store
            mod.srh._get_skill_store = lambda: object()
            mod.srh.SkillStore = object
            mod.srh._user_skills_dir = lambda: data_dir / "skills"
            mod.srh._project_skills_dir = lambda: data_dir / ".skills"
            mod.srh.LoadedMemory = _store_module.LoadedMemory
            mod.srh.MemoryFrontmatter = _store_module.MemoryFrontmatter
            mod.srh._tool_srh_palace_zones = lambda _: __import__("json").dumps(
                {"zones": ["core", "work"]}
            )

            gm = mod._get_graph_interface()
            assert gm is not None, "_get_graph_interface() returned None in package mode"
            assert hasattr(gm, "get_neighbors")
            assert hasattr(gm, "store")
            gm.close()
        finally:
            graph.close()
            store.close()
            _reset_graph_manager_singleton()


@pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
def test_dashboard_graph_endpoint_in_package_mode():
    """The /graph endpoint should return real hebbian edges when graph is available."""
    repo_root = Path(__file__).resolve().parent.parent
    _store_module = _ensure_package_namespace(repo_root)

    with tempfile.TemporaryDirectory(prefix="hermes_dash_int_") as tmpdir:
        data_dir = Path(tmpdir)
        MemoryStore = _store_module.MemoryStore
        plugin_data_dir = _store_module.plugin_data_dir
        from mem_reflection_hermes.core.graph import GraphIndex
        from mem_reflection_hermes.core.models import MemoryFrontmatter

        store = MemoryStore(
            user_root=data_dir / "memories",
            db_path=data_dir / "memories.db",
        )
        graph = GraphIndex(data_dir / "graph.db")
        store.set_graph(graph)
        try:
            # Seed two memories and a Hebbian edge.
            from mem_reflection_hermes.core.models import MemoryFrontmatter

            fm1 = MemoryFrontmatter.new(source="dashboard-test", zone="work")
            m1 = store.put("user", fm1, "integration memory one")
            fm2 = MemoryFrontmatter.new(source="dashboard-test", zone="work")
            m2 = store.put("user", fm2, "integration memory two")
            graph.associate([fm1.id, fm2.id], context="dashboard-test")

            spec = __import__("importlib.util").util.spec_from_file_location(
                "mem_reflection_hermes.web.api", str(repo_root / "web" / "api.py")
            )
            assert spec is not None and spec.loader is not None
            mod = __import__("importlib.util").util.module_from_spec(spec)
            sys.modules["mem_reflection_hermes.web.api"] = mod
            spec.loader.exec_module(mod)
            mod.srh._plugin_data_dir = lambda: data_dir
            mod.srh._get_mem_store = lambda: store
            mod.srh._get_skill_store = lambda: object()
            mod.srh.SkillStore = object
            mod.srh._user_skills_dir = lambda: data_dir / "skills"
            mod.srh._project_skills_dir = lambda: data_dir / ".skills"
            mod.srh.LoadedMemory = _store_module.LoadedMemory
            mod.srh.MemoryFrontmatter = _store_module.MemoryFrontmatter
            mod.srh._tool_srh_palace_zones = lambda _: __import__("json").dumps(
                {"zones": ["core", "work"]}
            )
            # _get_graph_interface opens its own GraphManagerCompat on the same db.
            # Keep that instance alive only for the request, then close it to release
            # the file handle before temp directory cleanup on Windows.
            original_get_graph_interface = mod._get_graph_interface
            gm = _get_test_graph_manager(data_dir)
            mod._get_graph_interface = lambda: gm

            app = FastAPI()
            app.include_router(mod.router, prefix="/api")
            client = TestClient(app)
            try:
                response = client.get("/api/graph")
                assert response.status_code == 200
                payload = response.json()
                assert payload.get("degraded") is False
                assert any(e.get("type") == "hebbian" for e in payload["edges"])
            finally:
                gm.close()
        finally:
            graph.close()
            store.close()
            _reset_graph_manager_singleton()


def _get_test_graph_manager(data_dir: Path):
    """Build a graph manager that reuses the existing GraphIndex db."""
    from mem_reflection_hermes.runtime.graph import GraphManagerCompat
    return GraphManagerCompat(data_dir / "graph.db")
