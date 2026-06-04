"""conftest.py — Shared test fixtures for mem-reflection-hermes test suite."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is importable
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import LoadedMemory, MemoryEffectiveness, MemoryFrontmatter
from graph.ahe_graph import GraphStore
from store import MemoryStore

# Import helpers (exposed for fixtures)
from tests._helpers import make_memory, make_memory_with_id, effectiveness_for


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory(prefix="hermes_test_") as tmpdir:
        yield Path(tmpdir)


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
    import time as _time
    import shutil as _shutil
    tmpdir = Path(tempfile.mkdtemp(prefix="hermes_graph_"))
    db_path = tmpdir / "test_graph.db"
    store = GraphStore(db_path)
    yield store
    store.close()
    # Windows: retry cleanup — SQLite WAL files may hold handles transiently
    for _attempt in range(5):
        try:
            _shutil.rmtree(tmpdir)
            return
        except PermissionError:
            _time.sleep(0.1)
    _shutil.rmtree(tmpdir, ignore_errors=True)

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
