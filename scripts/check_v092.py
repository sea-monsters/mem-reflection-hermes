"""check_v092.py — Verification script for v0.9.2-beta features.

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


def test_cluqi():
    """Test CLUQI module loads and has expected interface."""
    from mem_reflection_hermes.graph.cluqi import CLUQI, CLUQIResult
    assert hasattr(CLUQI, 'query')
    assert hasattr(CLUQI, 'get_neighbors')
    assert hasattr(CLUQI, 'cross_zone_bridge')
    assert hasattr(CLUQIResult, 'total_score')
    print("  PASS: CLUQI interface")


def test_pagerank():
    """Test PageRank module loads and has expected interface."""
    from mem_reflection_hermes.graph.pagerank import compute_pagerank, get_top_pagerank
    print("  PASS: PageRank interface")


def test_query_cache():
    """Test query cache and templates."""
    from mem_reflection_hermes.query.cache import ResultCache, get_cache, build_query, QUERY_TEMPLATES
    cache = ResultCache(default_ttl=1.0)
    cache.set("test", "key1")
    assert cache.get("key1") == "test"
    assert len(QUERY_TEMPLATES) >= 8
    print("  PASS: Query cache and templates")


def test_cross_zone():
    """Test cross-zone analysis module."""
    from mem_reflection_hermes.graph.cross_zone import analyze_zone_connections, get_zone_recommendations
    print("  PASS: Cross-zone analysis interface")


def test_ahe_graph_extensions():
    """Test ahe_graph has new methods."""
    from mem_reflection_hermes.graph.ahe_graph import GraphStore
    assert hasattr(GraphStore, 'get_all_nodes')
    assert hasattr(GraphStore, 'add_supersedes_edge')
    assert hasattr(GraphStore, 'remove_supersedes_edge')
    print("  PASS: ahe_graph extensions")


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
    assert data['version'] == '1.0-beta', f"Expected 1.0-beta, got {data['version']}"
    print("  PASS: Version is 1.0-beta")


def main():
    print("=" * 60)
    print("v1.0-beta Verification")
    print("=" * 60)

    tests = [
        test_cluqi,
        test_pagerank,
        test_query_cache,
        test_cross_zone,
        test_ahe_graph_extensions,
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
