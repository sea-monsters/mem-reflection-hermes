"""conftest.py — Shared test fixtures for mem-reflection-hermes test suite."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest


def pytest_configure(config):
    """Override basetemp to avoid Windows ACL issues with default pytest-of-<user> dir.

    On Windows, the default pytest temp directory (pytest-of-<user>) can accumulate
    stale ACLs from other processes, causing PermissionError during test setup.
    Using a plugin-specific basetemp avoids this entirely and works on all platforms.
    """
    if config.option.basetemp is None:
        config.option.basetemp = str(Path(tempfile.gettempdir()) / "hermes_pytest")


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
