"""test_graph.py — Tests for GraphIndex.

Coverage:
- step_decay (HeLa-Mem per-step decay)
- distill (Hebbian Distillation §3.2)
- cross_zone (cross-zone bridge analysis)

Run: pytest tests/test_graph.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

# Import modules directly to avoid namespace clash with graph/ package
_spec_graph = importlib.util.spec_from_file_location("_graph", str(_REPO / "core" / "graph.py"))
_graph = importlib.util.module_from_spec(_spec_graph)
sys.modules["_graph"] = _graph
_spec_graph.loader.exec_module(_graph)
GraphIndex = _graph.GraphIndex

_spec_store = importlib.util.spec_from_file_location("_store", str(_REPO / "core" / "store.py"))
_store = importlib.util.module_from_spec(_spec_store)
sys.modules["_store"] = _store
_spec_store.loader.exec_module(_store)
MemoryStore = _store.MemoryStore
MemoryFrontmatter = _store.MemoryFrontmatter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_graph_index():
    """GraphIndex backed by a temporary SQLite database."""
    with tempfile.TemporaryDirectory(prefix="hermes_graph_") as tmpdir:
        db_path = Path(tmpdir) / "test_graph.db"
        gi = GraphIndex(db_path)
        yield gi
        gi.close()


@pytest.fixture
def temp_mem_store():
    """MemoryStore backed by a temporary directory and database."""
    tmpdir = tempfile.mkdtemp(prefix="hermes_store_")
    root = Path(tmpdir) / "memories"
    root.mkdir(parents=True, exist_ok=True)
    db_path = Path(tmpdir) / "memories.db"
    store = MemoryStore(user_root=root, db_path=db_path)
    yield store
    # Close SQLite connections before cleanup (Windows file locking)
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


# ---------------------------------------------------------------------------
# Step decay
# ---------------------------------------------------------------------------

class TestStepDecay:
    def test_empty_graph_noop(self, temp_graph_index):
        """step_decay on empty graph returns zero counts."""
        gi = temp_graph_index
        result = gi.step_decay()
        assert result["edges_pruned"] == 0
        assert result["edges_updated"] == 0
        assert result["steps_since_last_decay"] == 1

    def test_decay_reduces_weight(self, temp_graph_index):
        """After spreading activation, step_decay reduces edge weights."""
        gi = temp_graph_index
        gi.associate(["a", "b", "c"])
        # Trigger a spreading activation step
        gi.spread(["a"])
        # step_counter is now 1
        result = gi.step_decay()
        assert result["edges_updated"] == 6  # a<->b, a<->c, b<->c both directions
        assert result["steps_since_last_decay"] == 1
        assert result["decay_factor"] == pytest.approx(0.995, abs=1e-6)

    def test_prune_threshold(self, temp_graph_index):
        """Edges whose weight drops below 0.05 after decay are pruned."""
        gi = temp_graph_index
        gi.associate(["a", "b"])
        # Manually lower weight to near threshold
        conn = gi._get_conn()
        conn.execute(
            "UPDATE edges SET weight = 0.06 WHERE source_id = 'a' AND target_id = 'b'"
        )
        conn.commit()
        # Run many spreading steps to accumulate decay
        for _ in range(100):
            gi.spread(["a"])
        result = gi.step_decay()
        # After 100 steps at λ=0.995, factor ≈ 0.606; 0.06 * 0.606 = 0.036 < 0.05
        assert result["edges_pruned"] > 0

    def test_spread_accumulates_decay_steps(self, temp_graph_index):
        """Each spread() call adds steps that step_decay reports as steps_since_last_decay."""
        gi = temp_graph_index
        gi.associate(["a", "b"])

        # spread once → decay should report at least 1 step
        gi.spread(["a"])
        r1 = gi.step_decay()
        assert r1["steps_since_last_decay"] >= 1

        # spread twice more → next decay should report 2 steps (reset after first decay)
        gi.spread(["a"])
        gi.spread(["a"])
        r2 = gi.step_decay()
        assert r2["steps_since_last_decay"] == 2

    def test_read_only_spread_does_not_increment_step_counter(self, temp_graph_index):
        """P2-7: search-time spreading activation must not advance per-step decay."""
        gi = temp_graph_index
        gi.associate(["a", "b"])

        gi.spread(["a"])  # default: increments counter
        assert gi._step_counter == 1

        gi.spread(["a"], increment_step=False)
        assert gi._step_counter == 1  # unchanged

        gi.spread(["a"], increment_step=True)
        assert gi._step_counter == 2

    def test_spread_allowed_nodes_restricts_activation(self, temp_graph_index):
        """P2-5/P2-7: allowed_nodes keeps search-time expansion inside scope."""
        gi = temp_graph_index
        gi.associate(["a", "b"])
        gi.associate(["b", "c"])

        activation = gi.spread(["a"], allowed_nodes={"a", "b"})
        assert "a" in activation
        assert "b" in activation
        assert "c" not in activation, "cross-scope neighbor must not be activated"

    def test_multiple_decay_calls_cumulative(self, temp_graph_index):
        """Multiple step_decay calls use cumulative steps since last decay."""
        gi = temp_graph_index
        gi.associate(["a", "b"])
        for _ in range(10):
            gi.spread(["a"])
        r1 = gi.step_decay()
        assert r1["steps_since_last_decay"] == 10
        for _ in range(5):
            gi.spread(["a"])
        r2 = gi.step_decay()
        assert r2["steps_since_last_decay"] == 5


# ---------------------------------------------------------------------------
# Meta rows
# ---------------------------------------------------------------------------

class TestGraphMeta:
    def test_ensure_meta_persists_and_refreshes_zone(self, temp_graph_index):
        """ensure_meta(zone=...) should store the zone it accepts."""
        gi = temp_graph_index

        gi.ensure_meta("mem-1", zone="General")
        gi.ensure_meta("mem-1", zone="Work")

        row = gi._get_conn().execute(
            "SELECT zone, status FROM graph_meta WHERE memory_id = ?",
            ("mem-1",),
        ).fetchone()

        assert row["zone"] == "work"
        assert row["status"] == "active"


# ---------------------------------------------------------------------------
# Distill (Hebbian Distillation)
# ---------------------------------------------------------------------------

class TestDistill:
    def test_empty_graph_returns_empty(self, temp_graph_index, temp_mem_store):
        """distill on empty graph returns empty list."""
        gi = temp_graph_index
        store = temp_mem_store
        result = gi.distill(store)
        assert result == []

    def test_hub_identification(self, temp_graph_index, temp_mem_store):
        """Star hub with 4 leaves gets distilled."""
        gi = temp_graph_index
        store = temp_mem_store

        # Create memories: hub + 4 leaves
        hub_fm = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", hub_fm, "Central architecture design pattern")
        for i in range(4):
            fm = MemoryFrontmatter.new(source="test", zone="general")
            store.put("user", fm, f"Leaf memory about design pattern {i}")

        mems = store.list_active()
        assert len(mems) == 5
        hub_id = mems[0].id()

        # Build graph: hub connected to all leaves
        for m in mems[1:]:
            gi.associate([hub_id, m.id()])

        # Ensure all meta rows exist for pagerank
        for m in mems:
            gi.ensure_meta(m.id())

        result = gi.distill(store, hub_threshold=0.01, min_neighbors=2, max_neighbors=10)
        # Should distill at least the hub cluster
        assert len(result) >= 1
        # First distilled item should reference the hub
        assert result[0]["hub_id"] == hub_id
        # Semantic memory should have been written
        semantic_mems = [m for m in store.list_active() if m.frontmatter.zone == "semantic"]
        assert len(semantic_mems) >= 1
        assert "semantic" in semantic_mems[0].frontmatter.tags
        assert "distilled" in semantic_mems[0].frontmatter.tags

    def test_insufficient_neighbors_skipped(self, temp_graph_index, temp_mem_store):
        """Hub with fewer neighbors than min_neighbors is skipped."""
        gi = temp_graph_index
        store = temp_mem_store

        fm = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", fm, "Isolated memory")
        gi.ensure_meta(fm.id)

        result = gi.distill(store, hub_threshold=0.0, min_neighbors=3)
        assert result == []

    def test_seen_members_deduplicated(self, temp_graph_index, temp_mem_store):
        """Overlapping hub clusters do not double-distill shared members."""
        gi = temp_graph_index
        store = temp_mem_store

        # Create 3 memories that all connect to each other (clique)
        ids = []
        for i in range(3):
            fm = MemoryFrontmatter.new(source="test", zone="general")
            store.put("user", fm, f"Shared topic memory {i}")
            ids.append(fm.id)

        for m in store.list_active():
            gi.ensure_meta(m.id())

        gi.associate(ids)

        result = gi.distill(store, hub_threshold=0.0, min_neighbors=2)
        # Only one cluster should be distilled (members overlap)
        assert len(result) <= 1


# ---------------------------------------------------------------------------
# Cross-zone analysis
# ---------------------------------------------------------------------------

class TestCrossZone:
    def test_empty_graph(self, temp_graph_index, temp_mem_store):
        """cross_zone on empty graph returns empty matrix."""
        gi = temp_graph_index
        store = temp_mem_store
        result = gi.cross_zone(store)
        assert result["bridge_count"] == 0
        assert result["zone_matrix"] == {}
        assert result["bridges"] == []

    def test_bridge_detection(self, temp_graph_index, temp_mem_store):
        """Edges connecting different zones are detected as bridges."""
        gi = temp_graph_index
        store = temp_mem_store

        fm_work = MemoryFrontmatter.new(source="test", zone="work")
        fm_general = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", fm_work, "Work project deadline")
        store.put("user", fm_general, "General productivity tips")

        ids = [m.id() for m in store.list_active()]
        gi.associate(ids)
        for m in store.list_active():
            gi.ensure_meta(m.id())

        result = gi.cross_zone(store)
        assert result["bridge_count"] == 1
        # Zone matrix should contain both zones (direction depends on edge order)
        all_zones = set()
        for src, targets in result["zone_matrix"].items():
            all_zones.add(src)
            all_zones.update(targets.keys())
        assert all_zones == {"work", "general"}
        # Total cross-zone weight should be > 0
        total_weight = sum(
            sum(t.values()) for t in result["zone_matrix"].values()
        )
        assert total_weight > 0
        assert len(result["bridges"]) == 1
        assert result["bridges"][0]["weight"] > 0.3

    def test_same_zone_ignored(self, temp_graph_index, temp_mem_store):
        """Edges within the same zone do not create bridges."""
        gi = temp_graph_index
        store = temp_mem_store

        for i in range(3):
            fm = MemoryFrontmatter.new(source="test", zone="general")
            store.put("user", fm, f"General memory {i}")

        ids = [m.id() for m in store.list_active()]
        gi.associate(ids)
        for m in store.list_active():
            gi.ensure_meta(m.id())

        result = gi.cross_zone(store)
        assert result["bridge_count"] == 0
        # zone_matrix should be empty (no cross-zone edges)
        assert result["zone_matrix"] == {}


# ---------------------------------------------------------------------------
# Orphan edge cleanup (P2a)
# ---------------------------------------------------------------------------


class TestCleanOrphanEdges:
    def test_empty_graph(self, temp_graph_index):
        """clean_orphan_edges on empty graph returns 0."""
        gi = temp_graph_index
        assert gi.clean_orphan_edges({"mem-1", "mem-2"}) == 0

    def test_removes_orphan_edges(self, temp_graph_index):
        """Edges pointing to non-existent memories are deleted."""
        gi = temp_graph_index
        gi.ensure_meta("mem-1", zone="general")
        gi.ensure_meta("mem-2", zone="general")
        gi.ensure_meta("mem-3", zone="general")
        gi.associate(["mem-1", "mem-2"])
        gi.associate(["mem-2", "mem-3"])

        # All 3 exist — nothing to clean
        assert gi.clean_orphan_edges({"mem-1", "mem-2", "mem-3"}) == 0

        # Now mem-2 is gone — edge mem-1↔mem-2 and mem-2↔mem-3 become orphan
        cleaned = gi.clean_orphan_edges({"mem-1", "mem-3"})
        assert cleaned > 0

        # mem-2's meta should also be gone
        row = gi._get_conn().execute(
            "SELECT COUNT(*) as cnt FROM graph_meta WHERE memory_id = ?",
            ("mem-2",),
        ).fetchone()
        assert row["cnt"] == 0

    def test_empty_valid_ids_is_noop(self, temp_graph_index):
        """Passing empty set is a caller error and deletes nothing."""
        gi = temp_graph_index
        gi.ensure_meta("mem-1", zone="general")
        gi.ensure_meta("mem-2", zone="general")
        gi.associate(["mem-1", "mem-2"])
        cleaned = gi.clean_orphan_edges(set())
        assert cleaned == 0
        row = gi._get_conn().execute(
            "SELECT COUNT(*) as cnt FROM graph_meta"
        ).fetchone()
        assert row["cnt"] == 2

    def test_removes_orphan_meta_only(self, temp_graph_index):
        """graph_meta row for non-existent memory is deleted even without edges."""
        gi = temp_graph_index
        gi.ensure_meta("orphan-only", zone="work")
        row = gi._get_conn().execute(
            "SELECT COUNT(*) as cnt FROM graph_meta WHERE memory_id = ?",
            ("orphan-only",),
        ).fetchone()
        assert row["cnt"] == 1

        cleaned = gi.clean_orphan_edges({"real-mem"})
        assert cleaned >= 1

        row = gi._get_conn().execute(
            "SELECT COUNT(*) as cnt FROM graph_meta WHERE memory_id = ?",
            ("orphan-only",),
        ).fetchone()
        assert row["cnt"] == 0
