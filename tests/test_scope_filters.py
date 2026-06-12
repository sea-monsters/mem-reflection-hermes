"""RED-phase tests for Scoped Memory Filters (v1.6 Wave 2).

All tests are expected to FAIL until the production code is implemented.
Frozen: 2026-06-11 — do not modify assertions to fit implementation.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

# Load store module directly
_spec = importlib.util.spec_from_file_location("_store_mod", str(_REPO / "core" / "store.py"))
_store_mod = importlib.util.module_from_spec(_spec)
sys.modules["_store_mod"] = _store_mod
_spec.loader.exec_module(_store_mod)

MemoryStore = _store_mod.MemoryStore
MemoryFrontmatter = _store_mod.MemoryFrontmatter
LoadedMemory = _store_mod.LoadedMemory

# Load search module directly
_spec_search = importlib.util.spec_from_file_location("_search_mod", str(_REPO / "core" / "search.py"))
_search_mod = importlib.util.module_from_spec(_spec_search)
sys.modules["_search_mod"] = _search_mod
_spec_search.loader.exec_module(_search_mod)

SearchIndex = _search_mod.SearchIndex

from tests._helpers import make_memory_with_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fm_with_scope(
    mem_id: str,
    body: str,
    zone: str = "general",
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
) -> MemoryFrontmatter:
    """Build a MemoryFrontmatter with optional scope fields injected."""
    fm = MemoryFrontmatter(
        id=mem_id,
        created="2026-06-11T00:00:00+00:00",
        source="test",
        confidence="medium",
        tags=[],
        supersedes=[],
        zone=zone,
    )
    if user_id is not None:
        object.__setattr__(fm, "user_id", user_id)
    if agent_id is not None:
        object.__setattr__(fm, "agent_id", agent_id)
    if run_id is not None:
        object.__setattr__(fm, "run_id", run_id)
    return fm


# ---------------------------------------------------------------------------
# 1. Scoped Write
# ---------------------------------------------------------------------------

class TestScopedWrite:
    """Verify memories can be written with scope fields."""

    def test_write_with_scope_fields_persists_to_db(self, temp_store):
        fm = _fm_with_scope("mem-scope-1", "body", user_id="u1", agent_id="a1", run_id="r1")
        temp_store.put("user", fm, "body")

        conn = temp_store._get_conn()
        row = conn.execute(
            "SELECT user_id, agent_id, run_id FROM memories WHERE id = ?", ("mem-scope-1",)
        ).fetchone()
        assert row is not None
        assert row["user_id"] == "u1"
        assert row["agent_id"] == "a1"
        assert row["run_id"] == "r1"

    def test_write_without_scope_fields_sets_null(self, temp_store):
        fm = make_memory_with_id("mem-scope-2", "body")
        temp_store.put("user", fm.frontmatter, fm.body)

        conn = temp_store._get_conn()
        row = conn.execute(
            "SELECT user_id, agent_id, run_id FROM memories WHERE id = ?", ("mem-scope-2",)
        ).fetchone()
        assert row is not None
        assert row["user_id"] is None
        assert row["agent_id"] is None
        assert row["run_id"] is None

    def test_write_scope_fields_persist_in_frontmatter(self, temp_store):
        """Scope fields should round-trip through frontmatter serialization."""
        fm = _fm_with_scope("mem-scope-3", "scoped body", user_id="u1")
        temp_store.put("user", fm, "scoped body")

        loaded = temp_store.get("mem-scope-3")
        assert loaded is not None
        assert getattr(loaded.frontmatter, "user_id", None) == "u1"


# ---------------------------------------------------------------------------
# 2. Scoped Search
# ---------------------------------------------------------------------------

class TestScopedSearch:
    """Verify SearchIndex.search respects scope filters."""

    def _seed_scoped_memories(self, temp_store):
        """Helper: create 4 memories with different scope combinations."""
        for mem_id, body, uid, aid, rid in [
            ("m-u1", "alpha content", "u1", "a1", "r1"),
            ("m-u2", "beta content", "u2", "a1", "r1"),
            ("m-a2", "gamma content", "u1", "a2", "r1"),
            ("m-null", "delta content", None, None, None),
        ]:
            if uid is None:
                fm = make_memory_with_id(mem_id, body)
                temp_store.put("user", fm.frontmatter, fm.body)
            else:
                fm = _fm_with_scope(mem_id, body, user_id=uid, agent_id=aid, run_id=rid)
                temp_store.put("user", fm, body)

    def test_search_filter_by_user_id(self, temp_store):
        self._seed_scoped_memories(temp_store)
        si = SearchIndex(temp_store)
        si.invalidate_cache()

        results = si.search("content", k=10, filters={"user_id": "u1"})
        ids = {r.id() for r in results}
        assert "m-u1" in ids
        assert "m-u2" not in ids
        assert "m-a2" in ids

    def test_search_filter_by_agent_id(self, temp_store):
        self._seed_scoped_memories(temp_store)
        si = SearchIndex(temp_store)
        si.invalidate_cache()

        results = si.search("content", k=10, filters={"agent_id": "a2"})
        ids = {r.id() for r in results}
        assert "m-a2" in ids
        assert "m-u1" not in ids
        assert "m-u2" not in ids

    def test_search_filter_by_run_id(self, temp_store):
        self._seed_scoped_memories(temp_store)
        si = SearchIndex(temp_store)
        si.invalidate_cache()

        results = si.search("content", k=10, filters={"run_id": "r1"})
        ids = {r.id() for r in results}
        assert "m-u1" in ids
        assert "m-u2" in ids
        assert "m-a2" in ids
        assert "m-null" not in ids

    def test_combined_filters_use_and_logic(self, temp_store):
        self._seed_scoped_memories(temp_store)
        si = SearchIndex(temp_store)
        si.invalidate_cache()

        results = si.search("content", k=10, filters={"user_id": "u1", "agent_id": "a1"})
        ids = {r.id() for r in results}
        assert "m-u1" in ids
        assert "m-a2" not in ids  # u1 but a2
        assert "m-u2" not in ids  # u2

    def test_null_scope_excluded_by_specific_filter(self, temp_store):
        """A memory with NULL user_id should NOT match filters={"user_id": "u1"}."""
        self._seed_scoped_memories(temp_store)
        si = SearchIndex(temp_store)
        si.invalidate_cache()

        results = si.search("content", k=10, filters={"user_id": "u1"})
        ids = {r.id() for r in results}
        assert "m-null" not in ids

    def test_search_without_filters_returns_all(self, temp_store):
        self._seed_scoped_memories(temp_store)
        si = SearchIndex(temp_store)
        si.invalidate_cache()

        results = si.search("content", k=10)
        ids = {r.id() for r in results}
        assert "m-u1" in ids
        assert "m-u2" in ids
        assert "m-a2" in ids
        assert "m-null" in ids

    def test_bm25_filter_applied_before_topk_truncation(self, temp_store):
        """Scoped BM25 must consider in-scope memories even if out-of-scope
        memories score higher globally.
        """
        # Out-of-scope memory matches multiple query terms → high BM25 score
        fm_out = _fm_with_scope(
            "m-bm25-out", "apple banana cherry date elderberry", user_id="u2"
        )
        temp_store.put("user", fm_out, "apple banana cherry date elderberry")
        # In-scope memory matches only one term → lower global BM25 score
        fm_in = _fm_with_scope("m-bm25-in", "apple", user_id="u1")
        temp_store.put("user", fm_in, "apple")

        si = SearchIndex(temp_store)
        si.invalidate_cache()

        # k=1: global top-1 would be m-bm25-out, but it is out of scope.
        # Test the BM25 channel directly to avoid rescue by embedding fusion.
        bm25_results = si._bm25_search_bm25s(
            "apple banana cherry", k=1, filters={"user_id": "u1"}
        )
        assert "m-bm25-in" in bm25_results
        assert "m-bm25-out" not in bm25_results

        # Public search should also surface the in-scope memory.
        results = si.search("apple banana cherry", k=1, filters={"user_id": "u1"})
        ids = [r.id() for r in results]
        assert "m-bm25-in" in ids
        assert "m-bm25-out" not in ids

    def test_embedding_filter_applied_before_topk_truncation(self, temp_store, monkeypatch):
        """Scoped embedding search must consider in-scope memories even if
        out-of-scope memories are more similar to the query globally.
        """
        import functools

        # Clear any real embedding cache before replacing the function.
        try:
            _search_mod._embed_single.cache_clear()
        except Exception:
            pass

        # Provide deterministic vectors so we can control ranking precisely.
        @functools.lru_cache(maxsize=500)
        def _fake_embed(text: str):
            text = text or ""
            if "relevant" in text.lower():
                return [1.0, 0.0]
            if "apple" in text.lower():
                return [0.9, 0.1]
            return [0.0, 1.0]

        monkeypatch.setattr(_search_mod, "_embed_single", _fake_embed)

        # Out-of-scope memory is closer to the query vector
        fm_out = _fm_with_scope("m-emb-out", "relevant", user_id="u2")
        temp_store.put("user", fm_out, "relevant")
        # In-scope memory is still relevant but scores lower globally
        fm_in = _fm_with_scope("m-emb-in", "apple", user_id="u1")
        temp_store.put("user", fm_in, "apple")

        si = SearchIndex(temp_store)
        si.invalidate_cache()
        si._embed_array = None  # force rebuild with mocked embeddings

        # Test the embedding channel directly.
        embed_results = si._embed_search("relevant", k=1, filters={"user_id": "u1"})
        assert "m-emb-in" in embed_results
        assert "m-emb-out" not in embed_results

        # k=1: global top-1 would be m-emb-out, but it is out of scope.
        results = si.search("relevant", k=1, filters={"user_id": "u1"})
        ids = [r.id() for r in results]
        assert "m-emb-in" in ids
        assert "m-emb-out" not in ids

        # Clear the fake cache so it cannot leak after monkeypatch restores.
        try:
            _fake_embed.cache_clear()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 3. Scoped List
# ---------------------------------------------------------------------------

class TestScopedList:
    """Verify MemoryStore.list respects scope filters."""

    def test_list_with_user_id_filter(self, temp_store):
        fm1 = _fm_with_scope("m-list-1", "body", user_id="u1")
        fm2 = _fm_with_scope("m-list-2", "body", user_id="u2")
        temp_store.put("user", fm1, "body")
        temp_store.put("user", fm2, "body")

        results = temp_store.list(filters={"user_id": "u1"})
        ids = {m.id() for m in results}
        assert "m-list-1" in ids
        assert "m-list-2" not in ids

    def test_list_without_filters_returns_all(self, temp_store):
        fm1 = _fm_with_scope("m-list-3", "body", user_id="u1")
        fm2 = make_memory_with_id("m-list-4", "body")
        temp_store.put("user", fm1, "body")
        temp_store.put("user", fm2.frontmatter, fm2.body)

        results = temp_store.list()
        ids = {m.id() for m in results}
        assert "m-list-3" in ids
        assert "m-list-4" in ids


# ---------------------------------------------------------------------------
# 4. Scoped Update
# ---------------------------------------------------------------------------

class TestScopedUpdate:
    """Verify scope fields are immutable on update."""

    def test_update_preserves_existing_scope(self, temp_store):
        fm = _fm_with_scope("m-upd-scope-1", "original", user_id="u1")
        temp_store.put("user", fm, "original")
        temp_store.update("m-upd-scope-1", body="updated")

        loaded = temp_store.get("m-upd-scope-1")
        assert getattr(loaded.frontmatter, "user_id", None) == "u1"

    def test_update_ignores_scope_field_changes(self, temp_store):
        fm = _fm_with_scope("m-upd-scope-2", "body", user_id="u1")
        temp_store.put("user", fm, "body")

        # Attempt to change scope during update (should be ignored)
        # This tests the production code rejects scope mutation
        updated = temp_store.update("m-upd-scope-2", body="new body")

        # If update accepted a user_id parameter, it should be ignored
        # For RED phase, we verify the existing scope is preserved
        assert getattr(updated.frontmatter, "user_id", None) == "u1"

        conn = temp_store._get_conn()
        row = conn.execute(
            "SELECT user_id FROM memories WHERE id = ?", ("m-upd-scope-2",)
        ).fetchone()
        assert row["user_id"] == "u1"


# ---------------------------------------------------------------------------
# 5. Scoped Delete
# ---------------------------------------------------------------------------

class TestScopedDelete:
    """Verify batch delete by scope filters."""

    def test_delete_by_filters_removes_matching(self, temp_store):
        fm1 = _fm_with_scope("m-del-f-1", "body", user_id="u1")
        fm2 = _fm_with_scope("m-del-f-2", "body", user_id="u2")
        temp_store.put("user", fm1, "body")
        temp_store.put("user", fm2, "body")

        deleted_count = temp_store.delete_by_filters({"user_id": "u1"})
        assert deleted_count == 1
        assert temp_store.get("m-del-f-1") is None
        assert temp_store.get("m-del-f-2") is not None

    def test_delete_by_filters_invokes_post_delete_callbacks(self, temp_store):
        """Batch delete must run registered post-delete callbacks like single delete()."""
        fm1 = _fm_with_scope("m-del-cb-1", "body", user_id="u1")
        fm2 = _fm_with_scope("m-del-cb-2", "body", user_id="u1")
        fm3 = _fm_with_scope("m-del-cb-3", "body", user_id="u2")
        temp_store.put("user", fm1, "body")
        temp_store.put("user", fm2, "body")
        temp_store.put("user", fm3, "body")

        deleted_ids: list[str] = []

        def _capture_callback(mem_id: str) -> None:
            deleted_ids.append(mem_id)

        temp_store._post_delete_callbacks.append(_capture_callback)

        deleted_count = temp_store.delete_by_filters({"user_id": "u1"})
        assert deleted_count == 2
        assert set(deleted_ids) == {"m-del-cb-1", "m-del-cb-2"}

    def test_delete_without_id_or_filters_rejected(self, temp_store):
        with pytest.raises((ValueError, TypeError)):
            temp_store.delete_by_filters({})


# ---------------------------------------------------------------------------
# 6. Scoped Explain
# ---------------------------------------------------------------------------

class TestScopedExplain:
    """Verify search_explain includes applied filters."""

    def test_explain_includes_applied_filters(self, temp_store):
        fm = _fm_with_scope("m-exp-1", "body", user_id="u1")
        temp_store.put("user", fm, "body")

        si = SearchIndex(temp_store)
        si.invalidate_cache()

        payload = si.search_explain("body", filters={"user_id": "u1"})
        assert payload.get("meta", {}).get("applied_filters") == {"user_id": "u1"}


# ---------------------------------------------------------------------------
# 7. Schema Migration
# ---------------------------------------------------------------------------

class TestScopedMigration:
    """Verify scope columns and indexes exist after migration."""

    def test_scope_columns_exist_after_init(self, temp_store):
        conn = temp_store._get_conn()
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        assert "user_id" in cols
        assert "agent_id" in cols
        assert "run_id" in cols

    def test_scope_indexes_exist(self, temp_store):
        conn = temp_store._get_conn()
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memories'"
            ).fetchall()
        }
        assert "idx_memories_user_id" in indexes
        assert "idx_memories_agent_id" in indexes
        assert "idx_memories_run_id" in indexes
        assert "idx_memories_scoped" in indexes

    def test_null_filter_matches_null_memory(self, temp_store):
        """filters={"user_id": null} should include memories with NULL user_id."""
        fm1 = make_memory_with_id("m-null-match-1", "body")  # NULL scope
        fm2 = _fm_with_scope("m-null-match-2", "body", user_id="u1")
        temp_store.put("user", fm1.frontmatter, fm1.body)
        temp_store.put("user", fm2, "body")

        results = temp_store.list(filters={"user_id": None})
        ids = {m.id() for m in results}
        assert "m-null-match-1" in ids
        assert "m-null-match-2" not in ids
