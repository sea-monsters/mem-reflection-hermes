"""test_dashboard.py — Tests for dashboard plugin API.

Coverage:
- CRUD endpoints: list, create, update, delete, reorder
- Graph endpoints: graph view, neighbors, zones
- Stats endpoint
- Skills endpoint
- Reflections endpoint

Run: pytest tests/test_dashboard.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

try:
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

_REPO = Path(__file__).resolve().parent.parent

# Load store module
_spec_store = importlib.util.spec_from_file_location("_memory_store_module", str(_REPO / "core" / "store.py"))
_store_module = importlib.util.module_from_spec(_spec_store)
sys.modules["_memory_store_module"] = _store_module
_spec_store.loader.exec_module(_store_module)
MemoryStore = _store_module.MemoryStore
MemoryFrontmatter = _store_module.MemoryFrontmatter

# Load graph module
_spec_graph = importlib.util.spec_from_file_location("_memory_graph_module", str(_REPO / "core" / "graph.py"))
_graph_module = importlib.util.module_from_spec(_spec_graph)
sys.modules["_memory_graph_module"] = _graph_module
_spec_graph.loader.exec_module(_graph_module)
GraphIndex = _graph_module.GraphIndex


def _make_dashboard_app(store, graph, data_dir=None):
    """Build a FastAPI app with the dashboard router for testing."""
    from fastapi import FastAPI
    # Import dashboard router
    spec_api = importlib.util.spec_from_file_location(
        "dashboard_api", str(_REPO / "web" / "api.py")
    )
    mod = importlib.util.module_from_spec(spec_api)
    sys.modules["dashboard_api"] = mod
    # Need to mock srh before exec
    mock_srh = MagicMock()
    mock_srh._get_mem_store.return_value = store
    mock_srh.SkillStore = MagicMock()
    mock_srh._get_skill_store.return_value = MagicMock()
    mock_srh._plugin_data_dir.return_value = data_dir or _store_module.plugin_data_dir()
    mock_srh._user_skills_dir = _store_module.user_skills_dir
    mock_srh._project_skills_dir = _store_module.project_skills_dir
    mock_srh._tool_srh_memory_write.return_value = json.dumps({"id": "test-mem-id"})
    mock_srh._tool_srh_palace_zones.return_value = json.dumps({"zones": ["core", "work"]})
    mock_srh.LoadedMemory = _store_module.LoadedMemory
    mock_srh.MemoryFrontmatter = _store_module.MemoryFrontmatter

    sys.modules["mem_reflection_hermes"] = mock_srh

    # Ensure memory.curator is NOT in sys.modules (for test_curator_not_available)
    sys.modules.pop("mem_reflection_hermes.memory.curator", None)
    sys.modules.pop("mem_reflection_hermes.memory", None)
    # Also register submodules needed by dashboard
    for sub in ["graph", "query"]:
        sub_fqn = f"mem_reflection_hermes.{sub}"
        if sub_fqn not in sys.modules:
            sp = type(sys)(sub_fqn)
            sp.__path__ = [str(_REPO / sub)]
            sys.modules[sub_fqn] = sp

    spec_api.loader.exec_module(mod)
    app = FastAPI()
    app.include_router(mod.router, prefix="/api")
    return app, mod


@pytest.fixture
def temp_dashboard():
    tmpdir = tempfile.mkdtemp(prefix="hermes_dash_")
    root = Path(tmpdir) / "memories"
    root.mkdir(parents=True, exist_ok=True)
    db_path = Path(tmpdir) / "memories.db"
    store = MemoryStore(user_root=root, db_path=db_path)
    graph_db = Path(tmpdir) / "graph.db"
    graph = GraphIndex(graph_db)

    store._test_data_dir = Path(tmpdir)
    _real_srh = sys.modules.get("mem_reflection_hermes")
    if not _HAS_FASTAPI:
        yield None, store, graph
    else:
        app, mod = _make_dashboard_app(store, graph, data_dir=Path(tmpdir))
        client = TestClient(app)
        yield client, store, graph

    # Restore real package to avoid mock pollution in later tests
    if _real_srh is not None:
        sys.modules["mem_reflection_hermes"] = _real_srh
    else:
        sys.modules.pop("mem_reflection_hermes", None)

    try:
        conn = getattr(store._local, "conn", None)
        if conn is not None:
            conn.close()
    except Exception:
        pass
    graph.close()
    import shutil as _shutil
    import time as _time
    for _attempt in range(5):
        try:
            _shutil.rmtree(tmpdir)
            break
        except PermissionError:
            _time.sleep(0.1)
    _shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestMemoriesCRUD:
    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_list_memories(self, temp_dashboard):
        client, store, graph = temp_dashboard
        # Seed some memories
        for i in range(3):
            fm = MemoryFrontmatter.new(source="test", zone="general")
            store.put("user", fm, f"Memory {i}")
        resp = client.get("/api/memories")
        assert resp.status_code == 200
        data = resp.json()
        assert "memories" in data
        assert len(data["memories"]) == 3

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_create_memory(self, temp_dashboard):
        client, store, graph = temp_dashboard
        payload = {
            "body": "New memory content",
            "zone": "work",
            "confidence": "high",
            "tags": ["important"],
            "pinned": True,
            "scope": "user",
        }
        resp = client.post("/api/memories", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_delete_memory(self, temp_dashboard):
        client, store, graph = temp_dashboard
        fm = MemoryFrontmatter.new(source="test")
        store.put("user", fm, "To be deleted")
        resp = client.delete(f"/api/memories/{fm.id}")
        assert resp.status_code == 200
        assert store.get(fm.id) is None

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_reorder_memories(self, temp_dashboard):
        client, store, graph = temp_dashboard
        ids = []
        for i in range(3):
            fm = MemoryFrontmatter.new(source="test")
            store.put("user", fm, f"Memory {i}")
            ids.append(fm.id)
        # Reverse order
        resp = client.post("/api/memories/reorder", json={"memory_ids": list(reversed(ids))})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_zone_filter(self, temp_dashboard):
        client, store, graph = temp_dashboard
        fm_work = MemoryFrontmatter.new(source="test", zone="work")
        fm_general = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", fm_work, "Work item")
        store.put("user", fm_general, "General item")
        resp = client.get("/api/memories?zone=work")
        assert resp.status_code == 200
        data = resp.json()
        assert all(m["zone"] == "work" for m in data["memories"])


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

class TestGraphEndpoints:
    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_graph_empty(self, temp_dashboard):
        client, store, graph = temp_dashboard
        resp = client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stats"]["node_count"] == 0

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_graph_with_memories(self, temp_dashboard):
        client, store, graph = temp_dashboard
        fm = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", fm, "Memory content")
        resp = client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stats"]["node_count"] == 1

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_graph_neighbors_not_found(self, temp_dashboard):
        client, store, graph = temp_dashboard
        resp = client.get("/api/graph/neighbors/nonexistent")
        # Without CLUQI or graph manager, may return 503
        assert resp.status_code in (200, 503)

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_zone_analysis(self, temp_dashboard):
        client, store, graph = temp_dashboard
        resp = client.get("/api/graph/zones")
        assert resp.status_code == 200
        data = resp.json()
        assert "zone_matrix" in data


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

class TestSkillsEndpoint:
    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_list_skills(self, temp_dashboard):
        client, store, graph = temp_dashboard
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert "skills" in data


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStatsEndpoint:
    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_stats_basic(self, temp_dashboard):
        client, store, graph = temp_dashboard
        fm = MemoryFrontmatter.new(source="test")
        store.put("user", fm, "Content")
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["memory_count"] == 1
        assert "zones" in data
        assert "health" in data


# ---------------------------------------------------------------------------
# Curator Dashboard (v1.2)
# ---------------------------------------------------------------------------

class TestCuratorEndpoint:
    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_curator_not_available(self, temp_dashboard):
        """When memory_curator module is missing, curator returns available=False."""
        client, store, graph = temp_dashboard
        resp = client.get("/api/curator")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "error" in data

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_curator_available(self, temp_dashboard):
        """With memory_curator loaded, return config + cold storage stats."""
        client, store, graph = temp_dashboard

        # Set up package namespace for relative imports in curator
        _PKG = "mem_reflection_hermes"
        if _PKG not in sys.modules:
            pkg = types.ModuleType(_PKG)
            pkg.__path__ = [str(_REPO)]
            sys.modules[_PKG] = pkg

        # Register core subpackage
        if f"{_PKG}.core" not in sys.modules:
            core_mod = types.ModuleType(f"{_PKG}.core")
            core_mod.__path__ = [str(_REPO / "core")]
            sys.modules[f"{_PKG}.core"] = core_mod

        # Register core.store
        if f"{_PKG}.core.store" not in sys.modules:
            sys.modules[f"{_PKG}.core.store"] = _store_module

        # Register memory subpackage
        if f"{_PKG}.memory" not in sys.modules:
            memory_mod = types.ModuleType(f"{_PKG}.memory")
            memory_mod.__path__ = [str(_REPO / "memory")]
            sys.modules[f"{_PKG}.memory"] = memory_mod

        # Register memory.curator (now a package)
        _spec_cur = importlib.util.spec_from_file_location(
            f"{_PKG}.memory.curator",
            str(_REPO / "memory" / "curator" / "__init__.py"),
        )
        _cur_mod = importlib.util.module_from_spec(_spec_cur)
        sys.modules[f"{_PKG}.memory.curator"] = _cur_mod
        _spec_cur.loader.exec_module(_cur_mod)  # type: ignore

        try:
            resp = client.get("/api/curator")
            assert resp.status_code == 200
            data = resp.json()
            assert data["available"] is True
            assert "enabled" in data
            assert "config" in data
            assert "cold_storage" in data
            assert "last_run" in data
        finally:
            sys.modules.pop(f"{_PKG}.memory.curator", None)

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_curator_report_persisted(self, temp_dashboard):
        """When _run_curator has saved a report, last_run populates."""
        client, store, graph = temp_dashboard

        # Set up package namespace for relative imports in curator
        _PKG = "mem_reflection_hermes"
        if _PKG not in sys.modules:
            pkg = types.ModuleType(_PKG)
            pkg.__path__ = [str(_REPO)]
            sys.modules[_PKG] = pkg

        if f"{_PKG}.core" not in sys.modules:
            core_mod = types.ModuleType(f"{_PKG}.core")
            core_mod.__path__ = [str(_REPO / "core")]
            sys.modules[f"{_PKG}.core"] = core_mod

        if f"{_PKG}.core.store" not in sys.modules:
            sys.modules[f"{_PKG}.core.store"] = _store_module

        if f"{_PKG}.memory" not in sys.modules:
            memory_mod = types.ModuleType(f"{_PKG}.memory")
            memory_mod.__path__ = [str(_REPO / "memory")]
            sys.modules[f"{_PKG}.memory"] = memory_mod

        # Register memory.curator (now a package)
        _spec_cur = importlib.util.spec_from_file_location(
            f"{_PKG}.memory.curator",
            str(_REPO / "memory" / "curator" / "__init__.py"),
        )
        _cur_mod = importlib.util.module_from_spec(_spec_cur)
        sys.modules[f"{_PKG}.memory.curator"] = _cur_mod
        _spec_cur.loader.exec_module(_cur_mod)  # type: ignore
        try:
            # Write a cached report
            cold_path = _cur_mod._cold_store_path(store)
            report_path = cold_path.with_suffix(".report.json")
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps({
                "timestamp": "2026-06-07T00:00:00Z",
                "report": "curator: stale: 5 archived",
                "stale": 5,
                "archived": 3,
                "superseded": 1,
                "similar": 0,
                "errors": [],
            }), encoding="utf-8")
            resp = client.get("/api/curator")
            assert resp.status_code == 200
            data = resp.json()
            assert data["last_run"] is not None
            assert data["last_run"]["report"] == "curator: stale: 5 archived"
            assert data["last_run"]["stale"] == 5
        finally:
            sys.modules.pop("mem_reflection_hermes.memory.curator", None)


# ---------------------------------------------------------------------------
# Reflections
# ---------------------------------------------------------------------------

class TestReflectionsEndpoint:
    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_list_reflections_empty(self, temp_dashboard):
        client, store, graph = temp_dashboard
        resp = client.get("/api/reflections")
        assert resp.status_code == 200
        data = resp.json()
        assert "reflections" in data
        assert data["reflections"] == []

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_list_reflections_with_entries(self, temp_dashboard):
        client, store, graph = temp_dashboard
        # Write a reflection log entry
        log_path = store._test_data_dir / "reflect-log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"mode": "raw_chunk", "summary": "test"}) + "\n")
        resp = client.get("/api/reflections")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["reflections"]) >= 1

    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_reflections_audit_empty(self, temp_dashboard):
        client, store, graph = temp_dashboard
        resp = client.get("/api/reflections/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert "audit_entries" in data
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------

class TestZonesEndpoint:
    @pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
    def test_zones(self, temp_dashboard):
        client, store, graph = temp_dashboard
        resp = client.get("/api/zones")
        assert resp.status_code == 200
        data = resp.json()
        assert "zones" in data
