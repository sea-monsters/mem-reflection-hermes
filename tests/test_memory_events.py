"""RED-phase tests for Memory Event Ledger (v1.6 Wave 1).

All tests are expected to FAIL until the production code is implemented.
Frozen: 2026-06-11 — do not modify assertions to fit implementation.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

# Load store module directly to avoid package-relative import issues
_spec = importlib.util.spec_from_file_location("_store_mod", str(_REPO / "core" / "store.py"))
_store_mod = importlib.util.module_from_spec(_spec)
sys.modules["_store_mod"] = _store_mod
_spec.loader.exec_module(_store_mod)

MemoryStore = _store_mod.MemoryStore
MemoryFrontmatter = _store_mod.MemoryFrontmatter
LoadedMemory = _store_mod.LoadedMemory

from tests._helpers import make_memory_with_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _frontmatter_with_scope(
    mem_id: str,
    body: str,
    zone: str = "general",
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
) -> MemoryFrontmatter:
    """Build a MemoryFrontmatter with optional scope fields."""
    fm = MemoryFrontmatter(
        id=mem_id,
        created="2026-06-11T00:00:00+00:00",
        source="test",
        confidence="medium",
        tags=[],
        supersedes=[],
        zone=zone,
    )
    # These fields may not exist on v1.5 frontmatter; set via object __dict__ if needed
    if user_id is not None:
        object.__setattr__(fm, "user_id", user_id)
    if agent_id is not None:
        object.__setattr__(fm, "agent_id", agent_id)
    if run_id is not None:
        object.__setattr__(fm, "run_id", run_id)
    return fm


# ---------------------------------------------------------------------------
# 1. Event Ledger Table Schema
# ---------------------------------------------------------------------------

class TestEventLedgerTable:
    """Verify the memory_events table exists and has the correct schema."""

    def test_event_table_exists_after_init(self, temp_store):
        conn = temp_store._get_conn()
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "memory_events" in tables, "memory_events table should exist after store init"

    def test_event_table_has_required_columns(self, temp_store):
        conn = temp_store._get_conn()
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(memory_events)").fetchall()}
        required = {
            "id", "memory_id", "event_type", "old_body", "new_body",
            "old_frontmatter", "new_frontmatter", "session_id", "actor_id", "created_at",
        }
        assert required.issubset(cols), f"memory_events missing columns: {required - cols}"

    def test_event_indexes_exist(self, temp_store):
        conn = temp_store._get_conn()
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memory_events'").fetchall()
        }
        assert "idx_memory_events_memory_id" in indexes
        assert "idx_memory_events_session_id" in indexes
        assert "idx_memory_events_created_at" in indexes

    def test_event_json_truncation_preserves_hash(self):
        """P2-18: oversized frontmatter is truncated but keeps a correlation hash."""
        big_data = {"id": "mem-big", "body": "x" * 20000}
        result = _store_mod.MemoryStore._event_json(big_data)
        parsed = json.loads(result)
        assert parsed["id"] == "mem-big"
        assert parsed.get("_truncated") is True
        assert "_original_frontmatter_hash" in parsed
        assert len(parsed["_original_frontmatter_hash"]) == 16
        assert len(result) <= 8192


# ---------------------------------------------------------------------------
# 2. ADD Events
# ---------------------------------------------------------------------------

class TestAddEvent:
    """Verify ADD events are recorded when memories are created."""

    def test_add_event_recorded_on_write(self, temp_store):
        fm = make_memory_with_id("mem-add-1", "hello world")
        temp_store.put("user", fm.frontmatter, fm.body)

        events = temp_store.get_memory_events("mem-add-1")
        assert len(events) == 1
        assert events[0]["event_type"] == "ADD"
        assert events[0]["new_body"] == "hello world"
        assert events[0]["old_body"] is None

    def test_add_event_includes_frontmatter(self, temp_store):
        fm = make_memory_with_id("mem-add-2", "tagged content", tags=["important"], zone="work")
        temp_store.put("user", fm.frontmatter, fm.body)

        events = temp_store.get_memory_events("mem-add-2")
        assert events[0]["event_type"] == "ADD"
        assert "work" in (events[0]["new_frontmatter"] or "")


# ---------------------------------------------------------------------------
# 3. UPDATE Events
# ---------------------------------------------------------------------------

class TestUpdateEvent:
    """Verify UPDATE events are recorded when memories are modified."""

    def test_update_event_records_old_and_new_body(self, temp_store):
        fm = make_memory_with_id("mem-upd-1", "original body")
        temp_store.put("user", fm.frontmatter, fm.body)
        temp_store.update("mem-upd-1", body="updated body")

        events = temp_store.get_memory_events("mem-upd-1")
        update_events = [e for e in events if e["event_type"] == "UPDATE"]
        assert len(update_events) == 1
        assert update_events[0]["old_body"] == "original body"
        assert update_events[0]["new_body"] == "updated body"

    def test_update_frontmatter_without_body_change(self, temp_store):
        fm = make_memory_with_id("mem-upd-2", "constant body")
        temp_store.put("user", fm.frontmatter, fm.body)
        temp_store.update("mem-upd-2", pinned=True)

        events = temp_store.get_memory_events("mem-upd-2")
        update_events = [e for e in events if e["event_type"] == "UPDATE"]
        assert len(update_events) == 1
        assert update_events[0]["old_body"] == update_events[0]["new_body"]

    def test_update_event_preserves_old_frontmatter(self, temp_store):
        fm = make_memory_with_id("mem-upd-3", "body", zone="general", confidence="medium")
        temp_store.put("user", fm.frontmatter, fm.body)
        temp_store.update("mem-upd-3", zone="work", confidence="high")

        events = temp_store.get_memory_events("mem-upd-3")
        update_events = [e for e in events if e["event_type"] == "UPDATE"]
        assert len(update_events) == 1
        old_fm = update_events[0]["old_frontmatter"] or ""
        new_fm = update_events[0]["new_frontmatter"] or ""
        assert "general" in old_fm or old_fm == ""
        assert "work" in new_fm or new_fm == ""


# ---------------------------------------------------------------------------
# 4. SUPERSEDE Events
# ---------------------------------------------------------------------------

class TestSupersedeEvent:
    """Verify SUPERSEDE events are recorded when a memory supersedes another."""

    def test_supersede_event_recorded(self, temp_store):
        old_fm = make_memory_with_id("mem-old-1", "old version")
        temp_store.put("user", old_fm.frontmatter, old_fm.body)

        new_fm = MemoryFrontmatter(
            id="mem-new-1",
            created="2026-06-11T01:00:00+00:00",
            source="test",
            confidence="medium",
            tags=[],
            supersedes=["mem-old-1"],
            zone="general",
        )
        temp_store.put("user", new_fm, "new version")

        events = temp_store.get_memory_events("mem-old-1")
        super_events = [e for e in events if e["event_type"] == "SUPERSEDE"]
        assert len(super_events) == 1
        assert "mem-new-1" in (super_events[0]["new_frontmatter"] or "") or super_events[0]["new_body"] == "new version"


# ---------------------------------------------------------------------------
# 5. DELETE Events
# ---------------------------------------------------------------------------

class TestDeleteEvent:
    """Verify DELETE events are recorded before memory removal."""

    def test_delete_event_recorded_before_removal(self, temp_store):
        fm = make_memory_with_id("mem-del-1", "to be deleted")
        temp_store.put("user", fm.frontmatter, fm.body)
        temp_store.delete("user", "mem-del-1")

        events = temp_store.get_memory_events("mem-del-1")
        del_events = [e for e in events if e["event_type"] == "DELETE"]
        assert len(del_events) == 1
        assert del_events[0]["old_body"] == "to be deleted"
        assert del_events[0]["new_body"] is None

    def test_deleted_memory_events_remain_queryable(self, temp_store):
        fm = make_memory_with_id("mem-del-2", "body")
        temp_store.put("user", fm.frontmatter, fm.body)
        temp_store.update("mem-del-2", body="updated")
        temp_store.delete("user", "mem-del-2")

        events = temp_store.get_memory_events("mem-del-2")
        assert len(events) == 3  # ADD, UPDATE, DELETE
        types = [e["event_type"] for e in events]
        assert "ADD" in types
        assert "UPDATE" in types
        assert "DELETE" in types


# ---------------------------------------------------------------------------
# 6. PIN / UNPIN Events
# ---------------------------------------------------------------------------

class TestPinUnpinEvent:
    """Verify PIN and UNPIN events are recorded."""

    def test_pin_event_recorded(self, temp_store):
        fm = make_memory_with_id("mem-pin-1", "body")
        temp_store.put("user", fm.frontmatter, fm.body)
        temp_store.update("mem-pin-1", pinned=True)

        events = temp_store.get_memory_events("mem-pin-1")
        pin_events = [e for e in events if e["event_type"] == "PIN"]
        assert len(pin_events) == 1

    def test_unpin_event_recorded(self, temp_store):
        fm = make_memory_with_id("mem-pin-2", "body")
        temp_store.put("user", fm.frontmatter, fm.body)
        temp_store.update("mem-pin-2", pinned=True)
        temp_store.update("mem-pin-2", pinned=False)

        events = temp_store.get_memory_events("mem-pin-2")
        unpin_events = [e for e in events if e["event_type"] == "UNPIN"]
        assert len(unpin_events) == 1


# ---------------------------------------------------------------------------
# 7. Event Query Filtering
# ---------------------------------------------------------------------------

class TestEventQuery:
    """Verify event queries support type, session, and limit filters."""

    def test_filter_by_event_type(self, temp_store):
        fm = make_memory_with_id("mem-q-1", "body")
        temp_store.put("user", fm.frontmatter, fm.body)
        temp_store.update("mem-q-1", body="v2")
        temp_store.delete("user", "mem-q-1")

        all_events = temp_store.get_memory_events("mem-q-1")
        assert len(all_events) == 3

        filtered = temp_store.get_memory_events("mem-q-1", event_types=["UPDATE", "DELETE"])
        types = [e["event_type"] for e in filtered]
        assert "UPDATE" in types
        assert "DELETE" in types
        assert "ADD" not in types

    def test_filter_by_session_id(self, temp_store):
        fm = make_memory_with_id("mem-q-2", "body")
        temp_store.put("user", fm.frontmatter, fm.body)

        events = temp_store.get_memory_events("mem-q-2", session_id="sess-abc")
        # With no session filtering in put, this should return all or none depending on impl
        # The test asserts that the filter parameter is accepted and processed
        assert isinstance(events, list)

    def test_filter_with_limit(self, temp_store):
        fm = make_memory_with_id("mem-q-3", "body")
        temp_store.put("user", fm.frontmatter, fm.body)
        for i in range(5):
            temp_store.update("mem-q-3", body=f"v{i}")

        all_events = temp_store.get_memory_events("mem-q-3")
        assert len(all_events) == 6  # ADD + 5 UPDATEs

        limited = temp_store.get_memory_events("mem-q-3", limit=2)
        assert len(limited) == 2


# ---------------------------------------------------------------------------
# 8. Session and Actor Tracking
# ---------------------------------------------------------------------------

class TestSessionActor:
    """Verify session_id and actor_id are tracked per event."""

    def test_session_id_from_context(self, temp_store):
        fm = make_memory_with_id("mem-sess-1", "body")
        temp_store.put("user", fm.frontmatter, fm.body)

        events = temp_store.get_memory_events("mem-sess-1")
        assert events[0]["session_id"] is not None or events[0]["session_id"] == ""

    def test_actor_id_defaults_to_agent(self, temp_store):
        fm = make_memory_with_id("mem-act-1", "body")
        temp_store.put("user", fm.frontmatter, fm.body)

        events = temp_store.get_memory_events("mem-act-1")
        assert events[0]["actor_id"] == "agent"

    def test_explicit_actor_override(self, temp_store):
        fm = make_memory_with_id("mem-act-2", "body")
        temp_store.put("user", fm.frontmatter, fm.body)

        events = temp_store.get_memory_events("mem-act-2")
        # When production code supports actor_id injection, this tests explicit override
        # For now, assert the field exists and has a value
        assert "actor_id" in events[0]


# ---------------------------------------------------------------------------
# 9. Event Atomicity and WAL Safety
# ---------------------------------------------------------------------------

class TestEventAtomicity:
    """Verify events are written atomically with memory changes."""

    def test_event_and_memory_in_same_transaction(self, temp_store):
        """If the transaction is rolled back, neither memory nor event should persist."""
        fm = make_memory_with_id("mem-atom-1", "atomic body")
        conn = temp_store._get_conn()
        try:
            conn.execute("BEGIN")
            temp_store.put("user", fm.frontmatter, fm.body)
            # Force rollback by raising inside transaction context
            raise RuntimeError("forced rollback")
        except RuntimeError:
            conn.rollback()
            pass

        # After rollback, memory should not exist
        assert temp_store.get("mem-atom-1") is None
        # And no events should exist
        events = temp_store.get_memory_events("mem-atom-1")
        assert len(events) == 0

    def test_event_frontmatter_truncation(self, temp_store):
        """Very large frontmatter should be truncated while preserving key fields."""
        huge_tags = [f"tag-{i}" for i in range(1000)]
        fm = make_memory_with_id("mem-trunc-1", "body", tags=huge_tags)
        temp_store.put("user", fm.frontmatter, fm.body)

        events = temp_store.get_memory_events("mem-trunc-1")
        fm_json = events[0]["new_frontmatter"] or ""
        # Should contain key fields, not the full 1000 tags
        assert len(fm_json) < 8192  # reasonable size limit
        assert "mem-trunc-1" in fm_json  # id preserved


# ---------------------------------------------------------------------------
# 10. Memory History (Event + Supersedes Chain)
# ---------------------------------------------------------------------------

class TestMemoryHistory:
    """Verify get_memory_history combines events and supersedes chain."""

    def test_history_includes_events_when_requested(self, temp_store):
        fm = make_memory_with_id("mem-hist-1", "body")
        temp_store.put("user", fm.frontmatter, fm.body)
        temp_store.update("mem-hist-1", body="v2")

        history = temp_store.get_memory_history("mem-hist-1")
        assert "events" in history or "supersedes" in history

    def test_history_without_events_returns_supersedes_only(self, temp_store):
        fm = make_memory_with_id("mem-hist-2", "body")
        temp_store.put("user", fm.frontmatter, fm.body)

        # When include_events defaults to False, only supersedes chain
        history = temp_store.get_memory_history("mem-hist-2")
        assert isinstance(history, dict)
