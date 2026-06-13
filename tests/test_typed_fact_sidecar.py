"""Typed fact sidecar regression tests for phase D."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent

_spec_graph = importlib.util.spec_from_file_location("_typed_graph", str(_REPO / "core" / "graph.py"))
_graph = importlib.util.module_from_spec(_spec_graph)
sys.modules["_typed_graph"] = _graph
_spec_graph.loader.exec_module(_graph)
GraphIndex = _graph.GraphIndex

_spec_store = importlib.util.spec_from_file_location("_typed_store", str(_REPO / "core" / "store.py"))
_store = importlib.util.module_from_spec(_spec_store)
sys.modules["_typed_store"] = _store
_spec_store.loader.exec_module(_store)
MemoryFrontmatter = _store.MemoryFrontmatter
MemoryStore = _store.MemoryStore
from mem_reflection_hermes.reflection import runtime as _runtime


@pytest.fixture
def temp_graph_index():
    with tempfile.TemporaryDirectory(prefix="hermes_graph_") as tmpdir:
        db_path = Path(tmpdir) / "test_graph.db"
        gi = GraphIndex(db_path)
        yield gi
        gi.close()


@pytest.fixture
def temp_mem_store():
    tmpdir = tempfile.mkdtemp(prefix="hermes_store_")
    root = Path(tmpdir) / "memories"
    root.mkdir(parents=True, exist_ok=True)
    db_path = Path(tmpdir) / "memories.db"
    store = MemoryStore(user_root=root, db_path=db_path)
    yield store
    try:
        conn = getattr(store._local, "conn", None)
        if conn is not None:
            conn.close()
    except Exception:
        pass
    import shutil as _shutil
    import time as _time
    for _attempt in range(5):
        try:
            _shutil.rmtree(tmpdir)
            break
        except PermissionError:
            _time.sleep(0.1)
    _shutil.rmtree(tmpdir, ignore_errors=True)


def test_record_typed_fact_persists_query_and_invalidation(temp_graph_index):
    fact_id = temp_graph_index.record_typed_fact(
        "mem-1",
        "User prefers dark mode.",
        relation="prefers",
        subject="user",
        object="dark mode",
        kind="preference",
        target_memory_id="mem-2",
        episode_id="episode-1",
        zone="general",
        confidence=0.9,
        source="reflection",
    )

    rows = temp_graph_index.typed_facts(source_memory_id="mem-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["fact_id"] == fact_id
    assert row["relation"] == "prefers"
    assert row["target_memory_id"] == "mem-2"
    assert row["episode_id"] == "episode-1"
    assert row["invalidated_by"] is None

    temp_graph_index.invalidate_typed_fact(fact_id, invalidated_by="mem-3")
    invalidated = temp_graph_index.typed_facts(source_memory_id="mem-1")
    assert invalidated[0]["invalidated_by"] == "mem-3"
    assert invalidated[0]["valid_until"] is not None
    assert temp_graph_index.typed_facts(source_memory_id="mem-1", include_invalidated=False) == []


def test_distill_records_semantic_sidecar_rows(temp_graph_index, temp_mem_store):
    gi = temp_graph_index
    store = temp_mem_store

    hub = MemoryFrontmatter.new(source="test", zone="general")
    store.put("user", hub, "Central architecture design pattern with repeated cues.")
    for idx in range(4):
        fm = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", fm, f"Leaf memory about design pattern {idx}")

    mems = store.list_active()
    hub_id = mems[0].id()
    for m in mems[1:]:
        gi.associate([hub_id, m.id()])
    for m in mems:
        gi.ensure_meta(m.id())

    result = gi.distill(store, hub_threshold=0.01, min_neighbors=2, max_neighbors=10)

    assert result
    semantic_id = result[0]["semantic_id"]
    typed_rows = gi.typed_facts(target_memory_id=semantic_id)
    assert typed_rows
    assert any(row["relation"] == "summarizes" for row in typed_rows)
    assert any(row["relation"] == "member_of" for row in typed_rows)
    assert any(row["episode_id"] == hub_id for row in typed_rows)


def test_embedding_micro_reflection_populates_typed_sidecar(monkeypatch):
    class _RecordingGraph:
        def __init__(self):
            self.records = []
            self.entity_records = []

        def record_typed_fact(self, *args, **kwargs):
            self.records.append({"args": args, "kwargs": kwargs})
            return "typed-1"

        def record_entity_mentions(self, *args, **kwargs):
            self.entity_records.append({"args": args, "kwargs": kwargs})
            return ["entity-1"]

    class _RecordingStore:
        def __init__(self, graph):
            self._graph = graph
            self.put_calls = []

        def list_active(self, filters=None):
            return []

        def check_conflict(self, body, exclude_ids=None, filters=None):
            return None

        def put(self, scope, fm, body):
            self.put_calls.append((scope, fm.id, body))
            return SimpleNamespace(path=f"/tmp/{fm.id}.md")

        def get(self, _memory_id):
            return object()

    graph = _RecordingGraph()
    store = _RecordingStore(graph)
    monkeypatch.setattr(_runtime, "_get_mem_store", lambda: store)
    monkeypatch.setattr(_runtime, "_append_reflect_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_runtime, "_compute_novelty_score", lambda *_args, **_kwargs: 0.9)
    monkeypatch.setattr(_runtime, "_find_conflicting_memory", lambda *_args, **_kwargs: None)

    result = _runtime._run_embedding_micro_reflection(
        "请记住：我喜欢深色模式。",
        "明白。",
        scope_filters={"user_id": "u1"},
    )

    assert result is not None
    assert store.put_calls
    assert graph.records
    assert graph.records[0]["kwargs"]["relation"] == "describes"
    assert graph.records[0]["kwargs"]["source"] == "micro_reflection"
    assert graph.entity_records
    assert graph.entity_records[0]["kwargs"]["source"] == "micro_reflection"


def test_record_entity_mentions_extracts_and_persists_entities(temp_graph_index):
    fact_ids = temp_graph_index.record_entity_mentions(
        "mem-entity-1",
        'Use "Config.yaml" with src/app.ts and HttpRequestHandler.',
        episode_id="episode-entity-1",
        zone="general",
        source="reflection",
    )

    rows = temp_graph_index.typed_facts(source_memory_id="mem-entity-1", relation="mentions")
    assert rows
    assert set(fact_ids) <= {row["fact_id"] for row in rows}
    assert any(row["kind"] == "entity" for row in rows)
    assert any(row["subject"] == "mem-entity-1" for row in rows)
    assert any(row["episode_id"] == "episode-entity-1" for row in rows)
