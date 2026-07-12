"""conftest.py — Shared test fixtures for mem-reflection-hermes test suite.

NOTE: Some test files load modules via importlib with unique names
(e.g. _store_mod, _memory_store_module), creating SEPARATE singleton
instances (_effectiveness_cache, _write_queue, etc.). Cross-instance
state bugs are invisible to these tests. Fix: use canonical module
names across all importlib loads.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable

import pytest


def pytest_configure(config):
    """Override basetemp to avoid Windows ACL issues with default pytest-of-<user> dir.

    On Windows, the default pytest temp directory (pytest-of-<user>) can accumulate
    stale ACLs from other processes, causing PermissionError during test setup.
    Using a per-run plugin-specific basetemp avoids this entirely and works on all platforms.
    """
    if config.option.basetemp is None:
        unique = f"hermes_pytest_{os.getpid()}_{time.time_ns()}"
        config.option.basetemp = str(Path(tempfile.gettempdir()) / unique)


_FILE_MARKERS = {
    # ── Store & data layer ──────────────────────────────────────────────
    "test_store.py": ("store", "compatibility"),
    "test_core_data.py": ("store", "compatibility"),
    "test_store_module_split.py": ("store", "compatibility"),
    "test_async_writer.py": ("store", "v14"),
    # ── Retrieval, ranking, entities ───────────────────────────────────
    "test_search.py": ("search", "retrieval"),
    "test_bm25.py": ("search", "retrieval", "cjk"),
    "test_fusion_rerank.py": ("search", "retrieval"),
    "test_wave3_retrieval.py": ("search", "retrieval", "graph"),
    "test_reranker.py": ("search", "retrieval", "reranker"),
    "test_reranker_exceptions.py": ("search", "retrieval", "reranker", "v14"),
    "test_entity_extraction.py": ("search", "retrieval", "v14"),
    "test_palace_recall.py": ("search", "retrieval", "tools"),
    # ── Graph layer ────────────────────────────────────────────────────
    "test_graph.py": ("graph",),
    "test_graph_operations.py": ("graph", "compatibility"),
    "test_graph_distil_failure.py": ("graph", "v14"),
    "test_runtime_graph_aliases.py": ("graph", "runtime", "compatibility"),
    # ── Scope & filters (v1.6 + round-3 ScopeIntent) ───────────────────
    "test_scope_filters.py": ("scope", "store", "search"),
    # ── Reflection, extraction, supersedes, sidecar ────────────────────
    "test_reflect.py": ("reflection", "compatibility"),
    "test_reflection.py": ("reflection", "runtime"),
    "test_reflection_refinement.py": ("reflection", "extraction", "v17"),
    "test_reflection_scope.py": ("reflection", "scope", "runtime"),
    "test_semantic_supersedes.py": ("reflection", "supersedes", "v17"),
    "test_typed_fact_sidecar.py": ("graph", "reflection", "sidecar", "v17"),
    "test_compaction.py": ("compaction", "runtime", "reflection"),
    # ── Context, runtime, hooks, config ────────────────────────────────
    "test_context.py": ("context", "runtime"),
    "test_hooks.py": ("runtime", "v14"),
    "test_runtime_import_hygiene.py": ("runtime", "compatibility"),
    "test_checkpoint.py": ("runtime", "config"),
    "test_checkpoint_backup_failure.py": ("runtime", "v14"),
    "test_config.py": ("config",),
    "test_optional_deps.py": ("config", "v14"),
    "test_backend.py": ("backend",),
    "test_schema_module.py": ("runtime", "tools", "contract"),
    "test_lb.py": ("runtime", "compatibility"),
    # ── Curation & lifecycle ───────────────────────────────────────────
    "test_memory_curator.py": ("curator",),
    "test_curator_pipeline.py": ("curator", "integration"),
    "test_effectiveness_snapshot.py": ("curator", "store", "v17"),
    "test_memory_events.py": ("curator", "events", "v16"),
    # ── Integration & host surfaces ────────────────────────────────────
    "test_bridge.py": ("bridge", "integration"),
    "test_dashboard.py": ("dashboard", "integration"),
    "test_dashboard_integration.py": ("dashboard", "integration"),
    "test_tool_handlers.py": ("tools", "runtime"),
    "test_e2e.py": ("e2e", "integration"),
    "test_host_contract_smoke.py": ("contract", "smoke", "integration"),
}

_V14_NODE_MARKERS = {
    "test_context.py::TestBuildContextPriority::test_context_bundle_splits_stable_and_dynamic_sections": ("v14_context",),
    "test_context.py::TestBuildContextPriority::test_context_bundle_stable_only_omits_dynamic_sections": ("v14_context",),
    "test_context.py::TestTokenBudget::test_bundle_records_compression_level_under_pressure": ("v14_context",),
    "test_context.py::TestTokenBudget::test_emergency_compression_keeps_pinned_stable_context": ("v14_context",),
    "test_reflection.py::TestHookReflectionCadence::test_pre_llm_call_uses_stable_fallback_on_timeout": ("v14_context",),
    "test_bm25.py::TestTokenise::test_jieba_search_mode_uses_search_tokens": ("v14_retrieval",),
    "test_bm25.py::TestTokenise::test_auto_mode_falls_back_to_bigram_without_jieba": ("v14_retrieval",),
    "test_search.py::TestStoreSearchGraphWiring::test_store_fusion_search_explain_exposes_score_components": ("v14_retrieval",),
    "test_checkpoint.py::TestCheckpointPersistence::test_corrupt_checkpoint_is_backed_up_and_defaults_returned": ("v14_runtime",),
    "test_checkpoint.py::TestCheckpointPersistence::test_recover_pending_work_runs_available_stages_and_clears_them": ("v14_runtime",),
    "test_reflection.py::TestHookReflectionCadence::test_on_session_start_runs_pending_recovery": ("v14_runtime",),
    "test_reflection.py::TestHookReflectionCadence::test_on_session_end_marks_reflection_pending_when_reflection_fails": ("v14_runtime",),
    "test_config.py::TestConfigModel::test_invalid_types_fall_back_to_defaults": ("v14_runtime",),
    "test_config.py::TestConfigModel::test_unknown_keys_are_reported": ("v14_runtime",),
    "test_search.py::TestStoreSearchGraphWiring::test_entity_links_are_indexed_and_deleted_without_orphans": ("v14_entity",),
    "test_search.py::TestStoreSearchGraphWiring::test_entity_boost_and_hits_appear_in_explain": ("v14_entity",),
    "test_backend.py::TestBackendCapabilities::test_sqlite_backend_capabilities_are_partial": ("v14_entity",),
    "test_backend.py::TestBackendCapabilities::test_fake_backend_can_report_full_capabilities": ("v14_entity",),
    "test_host_contract_smoke.py::test_host_contract_smoke_script_passes": ("v14_contract",),
    "test_hooks.py::TestSessionStateManagement::test_ensure_session_state_creates_default_bag": ("v14_hooks",),
    "test_hooks.py::TestSessionStateManagement::test_ensure_session_state_returns_existing": ("v14_hooks",),
    "test_hooks.py::TestSessionStateManagement::test_cleanup_session_state_removes_from_memory": ("v14_hooks",),
    "test_hooks.py::TestApiRequestErrorHook::test_no_session_id_is_noop": ("v14_hooks",),
    "test_hooks.py::TestApiRequestErrorHook::test_error_count_increments": ("v14_hooks",),
    "test_hooks.py::TestApiRequestErrorHook::test_threshold_crossing_logged": ("v14_hooks",),
    "test_hooks.py::TestApiRequestErrorHook::test_non_threshold_values_not_logged": ("v14_hooks",),
    "test_hooks.py::TestSubagentLifecycleHooks::test_start_increments_active_count": ("v14_hooks",),
    "test_hooks.py::TestSubagentLifecycleHooks::test_multiple_start_increments": ("v14_hooks",),
    "test_hooks.py::TestSubagentLifecycleHooks::test_stop_increments_total_count": ("v14_hooks",),
    "test_hooks.py::TestSubagentLifecycleHooks::test_no_session_id_is_noop": ("v14_hooks",),
    "test_hooks.py::TestSessionResetHook::test_logs_rotation_parameters": ("v14_hooks",),
    "test_hooks.py::TestSessionResetHook::test_missing_ids_use_unknown": ("v14_hooks",),
}


def _add_markers(item, markers: Iterable[str]) -> None:
    for marker in markers:
        item.add_marker(getattr(pytest.mark, marker))


def pytest_collection_modifyitems(config, items):
    """Apply functional and v1.4-specific markers automatically."""
    for item in items:
        filename = Path(str(item.fspath)).name
        _add_markers(item, _FILE_MARKERS.get(filename, ()))
        normalized = item.nodeid.replace("\\", "/")
        for suffix, markers in _V14_NODE_MARKERS.items():
            if normalized.endswith(suffix):
                _add_markers(item, markers)


# Ensure project root is importable
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Register mem_reflection_hermes package so relative imports in submodules work
if "mem_reflection_hermes" not in sys.modules:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mem_reflection_hermes",
        str(_REPO / "__init__.py"),
        submodule_search_locations=[str(_REPO)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mem_reflection_hermes"] = mod
    spec.loader.exec_module(mod)

from mem_reflection_hermes.runtime.graph import GraphStore
from mem_reflection_hermes.core.store import LoadedMemory, MemoryEffectiveness, MemoryFrontmatter, MemoryStore

# Import helpers (exposed for fixtures)
from tests._helpers import make_memory, make_memory_with_id, effectiveness_for


# ---------------------------------------------------------------------------
# Cross-platform temp directory cleanup
# ---------------------------------------------------------------------------

def _rmtree_safe(path: Path, retries: int = 8, delay: float = 0.15) -> None:
    """Remove a directory tree with Windows-compatible retry logic.

    Windows holds file handles on SQLite WAL/SHM files transiently.
    Retry with exponential backoff to avoid PermissionError on cleanup.
    """
    for attempt in range(retries):
        try:
            shutil.rmtree(str(path))
            return
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                shutil.rmtree(str(path), ignore_errors=True)
        except FileNotFoundError:
            return


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    """Temp directory with guaranteed cleanup on both Windows and Linux."""
    tmpdir = Path(tempfile.mkdtemp(prefix="hermes_test_"))
    yield tmpdir
    _rmtree_safe(tmpdir)


@pytest.fixture
def safe_tmp_path(tmp_path):
    """Drop-in replacement for tmp_path that wraps cleanup for Windows.

    Use this instead of bare ``tmp_path`` when the test creates SQLite files
    or other files that may hold handles on Windows.  The fixture yields the
    original tmp_path unchanged; cleanup is handled by the conftest-level
    temp_dir fixture pattern for tests that need it.

    For tests that only need a writable temp directory (no pytest-of
    permission issues), prefer ``temp_dir`` instead.
    """
    return tmp_path


@pytest.fixture
def hermes_tmp(tmp_path):
    """Cross-platform temp path for tests that need monkeypatch-able dirs.

    Yields a fresh temp directory (via pytest's tmp_path) and does no
    extra cleanup — pytest handles it.  Use when the test only needs a
    Path to write to and won't hold SQLite handles past teardown.
    """
    return tmp_path


@pytest.fixture
def temp_store(temp_dir):
    """MemoryStore with temp root for isolated testing."""
    memories_root = temp_dir / "memories"
    memories_root.mkdir(parents=True, exist_ok=True)
    db_path = temp_dir / "test_memories.db"
    store = MemoryStore(user_root=memories_root, db_path=db_path)
    yield store
    try:
        store.close()
    except Exception:
        pass


@pytest.fixture
def temp_graph():
    """GraphStore backed by a temporary SQLite database."""
    tmpdir = Path(tempfile.mkdtemp(prefix="hermes_graph_"))
    db_path = tmpdir / "test_graph.db"
    store = GraphStore(db_path)
    yield store
    store.close()
    _rmtree_safe(tmpdir)


@pytest.fixture(autouse=True)
def _reset_graph_singletons():
    """Reset module-level graph singletons before each test.

    runtime/graph.get_graph_manager_compat() and runtime/state._get_graph_mgr()
    both cache a singleton GraphManagerCompat. Without a reset, a test that
    populates graph state leaks into later tests (e.g. CleanOrphanEdges'
    "no graph" path would see a non-empty singleton from a prior test). This
    fixture clears those caches so every test starts from a clean graph state.

    Scope: global autouse. We deliberately do NOT redirect HERMES_HOME here --
    other tests (checkpoint persistence, reflect-log) depend on the real home
    dir semantics, and a global env override broke them. The narrower
    graph.db isolation that curator tests need is provided by
    _isolated_curator_graph_db below (marker-scoped).
    """
    import mem_reflection_hermes.runtime.graph as _rt_graph
    import mem_reflection_hermes.runtime.state as _rt_state

    _rt_graph._graph_manager_compat = None
    _rt_state._graph_mgr = None
    yield
    if _rt_graph._graph_manager_compat is not None:
        try:
            _rt_graph._graph_manager_compat.close()
        except Exception:
            pass
        _rt_graph._graph_manager_compat = None
    _rt_state._graph_mgr = None


@pytest.fixture(autouse=True)
def _isolated_curator_graph_db(request, monkeypatch, tmp_path):
    """Isolate the graph manager for curator tests.

    Curator's CleanOrphanEdges calls get_graph_manager_compat(), which returns
    a process-wide singleton backed by the real ~/.hermes/memory/graph.db. That
    singleton (and its db) persists across tests and even across runs, so a
    prior test that wrote graph edges leaks into CleanOrphanEdges' "no graph"
    assertions.

    Rather than chasing the db path through several re-export layers (which
    breaks depending on import order), we patch the resolver itself to build a
    fresh GraphManagerCompat against a per-test temp db. Tests that need a
    specific fake manager (e.g. test_counts_cleaned_edges_when_graph_available)
    still monkeypatch get_graph_manager_compat themselves and override this.

    Scope: tests marked "curator" only; other suites keep real home semantics.
    """
    markers = {m.name for m in request.node.iter_markers()}
    if "curator" not in markers:
        yield
        return

    import mem_reflection_hermes.runtime.graph as _rt_graph

    db_path = tmp_path / "curator_graph.db"

    def _fresh_isolated_manager(_db_path=None):
        # Always rebuild against the isolated path so no state leaks in or out.
        return _rt_graph.GraphManagerCompat(db_path)

    # Patch BOTH the runtime.graph symbol (used by state._get_graph_mgr) and
    # the curator.actions resolver, since they resolve via different paths.
    monkeypatch.setattr(_rt_graph, "get_graph_manager_compat", _fresh_isolated_manager)
    try:
        import mem_reflection_hermes.memory.curator.actions as _cur_actions
        monkeypatch.setattr(_cur_actions, "get_graph_manager_compat", _fresh_isolated_manager)
    except Exception:
        pass

    yield


@pytest.fixture
def seeded_store(temp_store):
    """MemoryStore with 5 pre-loaded memories for ranking tests."""
    store = temp_store
    mems = [
        make_memory_with_id("mem-1", "User prefers dark mode in all applications", zone="general", age_days=0),
        make_memory_with_id("mem-2", "User likes golang for backend development", zone="general", age_days=10),
        make_memory_with_id("mem-3", "Meeting notes from Tuesday standup about project deadlines", zone="work", age_days=30),
        make_memory_with_id("mem-4", "Python is the preferred language for data science tasks", zone="general", age_days=60),
        make_memory_with_id("mem-5", "The project uses React for frontend and Go for backend", zone="work", age_days=90),
    ]
    for m in mems:
        store.put("user", m.frontmatter, m.body)
    return store
