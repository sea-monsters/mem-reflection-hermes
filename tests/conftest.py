"""conftest.py — Shared test fixtures for mem-reflection-hermes test suite."""
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
    Using a plugin-specific basetemp avoids this entirely and works on all platforms.
    """
    if config.option.basetemp is None:
        config.option.basetemp = str(Path(tempfile.gettempdir()) / "hermes_pytest")


_FILE_MARKERS = {
    "test_store.py": ("store", "compatibility"),
    "test_core_data.py": ("store", "compatibility"),
    "test_search.py": ("search", "retrieval"),
    "test_bm25.py": ("search", "retrieval", "cjk"),
    "test_fusion_rerank.py": ("search", "retrieval"),
    "test_wave3_retrieval.py": ("search", "retrieval", "graph"),
    "test_reranker.py": ("search", "retrieval", "reranker"),
    "test_reranker_exceptions.py": ("search", "retrieval", "reranker", "v14"),
    "test_graph.py": ("graph",),
    "test_graph_operations.py": ("graph", "compatibility"),
    "test_graph_distil_failure.py": ("graph", "v14"),
    "test_reflect.py": ("reflection", "compatibility"),
    "test_reflection.py": ("reflection", "runtime"),
    "test_context.py": ("context", "runtime"),
    "test_hooks.py": ("runtime", "v14"),
    "test_memory_curator.py": ("curator",),
    "test_bridge.py": ("bridge", "integration"),
    "test_dashboard.py": ("dashboard", "integration"),
    "test_tool_handlers.py": ("tools", "runtime"),
    "test_compaction.py": ("compaction", "runtime", "reflection"),
    "test_e2e.py": ("e2e", "integration"),
    "test_host_contract_smoke.py": ("contract", "smoke", "integration"),
    "test_checkpoint.py": ("runtime", "config"),
    "test_checkpoint_backup_failure.py": ("runtime", "v14"),
    "test_config.py": ("config",),
    "test_backend.py": ("backend",),
    "test_entity_extraction.py": ("search", "retrieval", "v14"),
    "test_async_writer.py": ("store", "v14"),
    "test_optional_deps.py": ("config", "v14"),
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
        conn = getattr(store._local, "conn", None)
        if conn is not None:
            conn.close()
            store._local.conn = None
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
