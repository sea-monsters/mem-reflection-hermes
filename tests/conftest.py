"""conftest.py — Shared test fixtures for mem-reflection-hermes test suite."""
from __future__ import annotations

import importlib.util
import sys
import types
import tempfile
from pathlib import Path

import pytest

# Ensure project root is importable
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import LoadedMemory, MemoryEffectiveness, MemoryFrontmatter
from graph.ahe_graph import GraphStore

# Import helpers (exposed for fixtures)
from tests._helpers import make_memory, make_memory_with_id, effectiveness_for


# ---------------------------------------------------------------------------
# Package setup — load __init__.py as mem_reflection_hermes
# ---------------------------------------------------------------------------

_PKG = "mem_reflection_hermes"
_MemoryStore = None


def _ensure_package():
    """Load the hermes package into sys.modules with proper namespace."""
    global _MemoryStore
    if _MemoryStore is not None:
        return _MemoryStore

    import core as _core

    # Register core under package namespace (so `from .core import` works)
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_REPO)]
    pkg.__package__ = _PKG
    pkg.__file__ = str(_REPO / "__init__.py")
    sys.modules[_PKG] = pkg
    sys.modules[f"{_PKG}.core"] = _core

    # Register subpackages (needed for relative imports in submodules)
    _subpackages = ["graph", "reflection", "tools", "hooks", "search", "query", "dashboard"]
    for sub in _subpackages:
        sub_fqn = f"{_PKG}.{sub}"
        if sub_fqn not in sys.modules:
            sp = types.ModuleType(sub_fqn)
            sp.__path__ = [str(_REPO / sub)]
            sp.__package__ = sub_fqn
            sys.modules[sub_fqn] = sp

    # Load __init__.py — its own relative imports handle submodule loading
    spec = importlib.util.spec_from_file_location(
        _PKG, str(_REPO / "__init__.py"),
        submodule_search_locations=[str(_REPO)]
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[_PKG] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pass  # Late imports may fail; MemoryStore is already defined

    _MemoryStore = getattr(sys.modules[_PKG], "MemoryStore", None)
    return _MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory(prefix="hermes_test_") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_store(temp_dir):
    """MemoryStore with temp root, cache pre-primed for injection."""
    StoreClass = _ensure_package()
    return StoreClass(user_root=temp_dir)


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
    store._cache.setdefault("active", [])
    store._cache.setdefault("pinned", [])
    store._cache.setdefault("all", [])
    store._cache.setdefault("superseded", set())
    mems = [
        make_memory_with_id("mem-1", "User prefers dark mode in all applications", zone="general", age_days=0),
        make_memory_with_id("mem-2", "User likes golang for backend development", zone="general", age_days=10),
        make_memory_with_id("mem-3", "Meeting notes from Tuesday standup about project deadlines", zone="work", age_days=30),
        make_memory_with_id("mem-4", "Python is the preferred language for data science tasks", zone="general", age_days=60),
        make_memory_with_id("mem-5", "The project uses React for frontend and Go for backend", zone="work", age_days=90),
    ]
    for m in mems:
        store._cache["active"].append(m)
        store._id_to_mem[m.id()] = m
    store._cache_valid = True
    return store
