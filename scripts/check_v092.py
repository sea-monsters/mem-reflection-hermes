"""check_v092.py — Runtime verification for beta3 surfaces.

Run after each batch of fixes to ensure correctness.
"""

import sys
import tempfile
from pathlib import Path

repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

# Register mem_reflection_hermes package so subpackage imports work
if "mem_reflection_hermes" not in sys.modules:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mem_reflection_hermes",
        str(Path(repo_root) / "__init__.py"),
        submodule_search_locations=[repo_root],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mem_reflection_hermes"] = mod
    spec.loader.exec_module(mod)

from mem_reflection_hermes.runtime.graph import GraphStore
from core.store import MemoryStore, MemoryFrontmatter


def test_runtime_graph_surface():
    """Test the runtime graph surface exposes the expected query helpers."""
    tmpdir = Path(tempfile.mkdtemp(prefix="hermes_check_graph_"))
    gm = GraphStore(tmpdir / "graph.db")
    try:
        assert hasattr(gm, "get_neighbors")
        assert hasattr(gm, "pagerank")
        assert hasattr(gm, "cross_zone")
        assert hasattr(gm, "associate_memories")
        print("  PASS: Runtime graph interface")
    finally:
        gm.close()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_pagerank():
    """Test PageRank via the runtime graph surface."""
    tmpdir = Path(tempfile.mkdtemp(prefix="hermes_check_graph_pr_"))
    gm = GraphStore(tmpdir / "graph.db")
    try:
        gm.associate_memories(["hub", "leaf_a", "leaf_b"])
        scores = gm.pagerank()
        assert isinstance(scores, dict)
        print("  PASS: PageRank interface")
    finally:
        gm.close()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_query_cache():
    """Test query cache and templates."""
    import importlib.util
    search_path = Path(repo_root) / "core" / "search.py"
    search_spec = importlib.util.spec_from_file_location("_search", str(search_path))
    search_mod = importlib.util.module_from_spec(search_spec)
    assert search_spec is not None and search_spec.loader is not None
    search_spec.loader.exec_module(search_mod)
    ResultCache = search_mod.ResultCache
    get_cache = search_mod.get_cache
    build_query = search_mod.build_query
    QUERY_TEMPLATES = search_mod.QUERY_TEMPLATES
    cache = ResultCache(default_ttl=1.0)
    cache.set("test", "key1")
    assert cache.get("key1") == "test"
    assert len(QUERY_TEMPLATES) >= 8
    assert build_query("recent")["type"] == "recent"
    assert get_cache().stats()["default_ttl"] > 0
    print("  PASS: Search query templates and cache")


def test_cross_zone():
    """Test cross-zone analysis via the runtime graph surface."""
    tmpdir = Path(tempfile.mkdtemp(prefix="hermes_check_graph_zone_"))
    graph_db = tmpdir / "graph.db"
    mem_dir = tmpdir / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    mem_store = MemoryStore(mem_dir, db_path=tmpdir / "mem.db")
    gm = GraphStore(graph_db)
    try:
        fm1 = MemoryFrontmatter.new(source="test", zone="work")
        fm1.id = "zone-a"
        fm2 = MemoryFrontmatter.new(source="test", zone="general")
        fm2.id = "zone-b"
        mem_store.put("user", fm1, "Zone A memory")
        mem_store.put("user", fm2, "Zone B memory")
        gm.associate_memories(["zone-a", "zone-b"])
        result = gm.cross_zone(mem_store)
        assert "zone_matrix" in result
        print("  PASS: Cross-zone analysis interface")
    finally:
        gm.close()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_runtime_graph_extensions():
    """Test runtime graph exposes the compatibility GraphStore surface."""
    tmpdir = Path(tempfile.mkdtemp(prefix="hermes_check_graph_surface_"))
    gm = GraphStore(tmpdir / "graph.db")
    try:
        assert hasattr(gm, "get_all_nodes")
        assert hasattr(gm, "upsert_edge")
        assert hasattr(gm, "spread_activation")
        assert hasattr(gm, "get_edges")
        print("  PASS: runtime graph extensions")
    finally:
        gm.close()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_dashboard_api():
    """Test dashboard API module has expected endpoints."""
    # Skip when run as standalone script (relative import issue)
    try:
        from mem_reflection_hermes.dashboard.plugin_api import router
        routes = [getattr(r, 'path', str(r)) for r in router.routes]
        assert any("/graph" in r for r in routes), f"No /graph route in {routes}"
        assert any("/query" in r for r in routes), f"No /query route in {routes}"
        assert any("/stats" in r for r in routes), f"No /stats route in {routes}"
        print("  PASS: Dashboard API endpoints")
    except ImportError as e:
        print(f"  SKIP: Dashboard API (import context: {e})")


def test_version():
    """Test version is updated."""
    import yaml
    with open(Path(__file__).parent.parent / "plugin.yaml") as f:
        data = yaml.safe_load(f)
    assert data['version'] == '1.5', f"Expected 1.5, got {data['version']}"
    print("  PASS: Version is 1.5")


def main():
    print("=" * 60)
    print("v1.5 Runtime Verification")
    print("=" * 60)

    tests = [
        test_runtime_graph_surface,
        test_pagerank,
        test_query_cache,
        test_cross_zone,
        test_runtime_graph_extensions,
        test_dashboard_api,
        test_version,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
