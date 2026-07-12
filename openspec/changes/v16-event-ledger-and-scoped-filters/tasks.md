## 1. Schema & Migration (Infrastructure)

- [ ] 1.1 Add `memory_events` table creation to `core/store.py` `_init_sqlite()`
- [ ] 1.2 Add `user_id`, `agent_id`, `run_id` columns to `memories` table with migration
- [ ] 1.3 Add indexes: `idx_memory_events_memory_id`, `idx_memory_events_session_id`, `idx_memory_events_created_at`
- [ ] 1.4 Add indexes: `idx_memories_user_id`, `idx_memories_agent_id`, `idx_memories_run_id`, `idx_memories_scoped`
- [ ] 1.5 Extend `MemoryFrontmatter` in `core/models.py` with `user_id`, `agent_id`, `run_id` (optional)
- [ ] 1.6 Update frontmatter parser to accept new scope fields (ignore unknown fields with warn)
- [ ] 1.7 Add `_record_memory_event()` private method to `MemoryStore`
- [ ] 1.8 Add `get_memory_events()` public method to `MemoryStore`
- [ ] 1.9 Add `get_memory_history()` public method (event + supersedes chain)

## 2. RED Phase — Event Ledger Tests (Must fail initially)

- [ ] 2.1 Create `tests/test_memory_events.py` with test class `TestEventLedgerTable`
- [ ] 2.2 Write `test_add_event_recorded_on_write` — asserts ADD event exists after write
- [ ] 2.3 Write `test_update_event_recorded_on_update` — asserts UPDATE event with old/new body
- [ ] 2.4 Write `test_supersede_event_recorded` — asserts SUPERSEDE event
- [ ] 2.5 Write `test_delete_event_recorded_before_removal` — asserts DELETE event
- [ ] 2.6 Write `test_pin_unpin_events_recorded` — asserts PIN/UNPIN events
- [ ] 2.7 Write `test_event_query_by_type` — filter by event_types
- [ ] 2.8 Write `test_event_query_by_session_id` — filter by session
- [ ] 2.9 Write `test_event_query_with_limit` — limit returns most recent N
- [ ] 2.10 Write `test_deleted_memory_events_remain_queryable` — events survive memory deletion
- [ ] 2.11 Write `test_session_id_from_context` — session_id populated from hook context
- [ ] 2.12 Write `test_actor_id_defaults_to_agent` — default actor_id
- [ ] 2.13 Write `test_explicit_actor_override` — custom actor_id
- [ ] 2.14 Write `test_event_frontmatter_truncation` — large frontmatter handled
- [ ] 2.15 Write `test_event_atomic_with_memory` — WAL safety (transaction rollback)
- [ ] 2.16 Write `test_event_old_frontmatter_preserved` — old fm stored on update
- [ ] 2.17 Verify all 16 event tests FAIL (RED phase confirmed)

## 3. RED Phase — Scoped Filters Tests (Must fail initially)

- [ ] 3.1 Create `tests/test_scope_filters.py` with test class `TestScopedFilters`
- [ ] 3.2 Write `test_write_with_scope_fields` — frontmatter contains scope
- [ ] 3.3 Write `test_write_without_scope_fields` — NULL columns, discoverable
- [ ] 3.4 Write `test_search_filter_by_user_id` — only matching user returned
- [ ] 3.5 Write `test_search_filter_by_agent_id` — only matching agent returned
- [ ] 3.6 Write `test_search_filter_by_run_id` — only matching run returned
- [ ] 3.7 Write `test_combined_filters_use_and_logic` — multiple filters AND
- [ ] 3.8 Write `test_null_scope_universally_visible` — no filters returns all
- [ ] 3.9 Write `test_filtered_search_null_does_not_match_specific` — NULL != specific filter
- [ ] 3.10 Write `test_scope_immutable_on_update` — update ignores scope change
- [ ] 3.11 Write `test_update_preserves_existing_scope` — update without scope keeps old
- [ ] 3.12 Write `test_curator_respects_scope` — curator only affects scoped memories
- [ ] 3.13 Write `test_delete_by_filters` — batch delete by scope
- [ ] 3.14 Write `test_delete_without_id_or_filters_rejected` — validation error
- [ ] 3.15 Write `test_explain_includes_applied_filters` — explain metadata shows filters
- [ ] 3.16 Write `test_indexes_exist_after_migration` — verify index creation
- [ ] 3.17 Write `test_null_filter_matches_null_memory` — filters={user_id: null}
- [ ] 3.18 Verify all 17 scope tests FAIL (RED phase confirmed)

## 4. Core Implementation — Event Ledger (GREEN Phase)

- [ ] 4.1 Implement `MemoryStore._record_memory_event()` with full field population
- [ ] 4.2 Wire `_record_memory_event()` into `MemoryStore.write()` for ADD events
- [ ] 4.3 Wire `_record_memory_event()` into `MemoryStore.update()` for UPDATE events
- [ ] 4.4 Wire `_record_memory_event()` into `MemoryStore.delete()` for DELETE events
- [ ] 4.5 Wire `_record_memory_event()` into pin/unpin operations for PIN/UNPIN events
- [ ] 4.6 Implement `MemoryStore.get_memory_events()` with filtering (event_types, session_id, limit, after)
- [ ] 4.7 Implement `MemoryStore.get_memory_history()` combining events + supersedes chain
- [ ] 4.8 Add frontmatter truncation logic for events (preserve key fields)
- [ ] 4.9 Ensure event writes are in same SQLite transaction as memory writes
- [ ] 4.10 Run `pytest tests/test_memory_events.py -v` — all 16 tests pass

## 5. Core Implementation — Scoped Filters (GREEN Phase)

- [ ] 5.1 Update `MemoryFrontmatter` serialization to include scope fields
- [ ] 5.2 Update `MemoryStore.write()` to persist scope columns
- [ ] 5.3 Update `MemoryStore.update()` to reject scope changes (preserve original)
- [ ] 5.4 Update `SearchIndex.search()` to accept `filters` dict parameter
- [ ] 5.5 Implement SQL WHERE clause generation for scope filters (NULL = match all)
- [ ] 5.6 Apply filters before BM25/embedding scoring in search pipeline
- [ ] 5.7 Update `MemoryStore.list_memories()` with filters support
- [ ] 5.8 Update `MemoryStore.delete()` to support batch delete by filters
- [ ] 5.9 Update curator actions to respect default scope filters
- [ ] 5.10 Run `pytest tests/test_scope_filters.py -v` — all 17 tests pass

## 6. Runtime Integration

- [ ] 6.1 Update `runtime/schemas.py` — `srh_memory_write` add user_id/agent_id/run_id
- [ ] 6.2 Update `runtime/schemas.py` — `srh_memory_search` add filters object
- [ ] 6.3 Update `runtime/schemas.py` — `srh_memory_delete` add filters object
- [ ] 6.4 Update `runtime/schemas.py` — `srh_memory_history` add include_events, event_types, session_id
- [ ] 6.5 Update `runtime/tools.py` — `_tool_srh_memory_write` pass scope fields + record events
- [ ] 6.6 Update `runtime/tools.py` — `_tool_srh_memory_search` pass filters to SearchIndex
- [ ] 6.7 Update `runtime/tools.py` — `_tool_srh_memory_delete` pass filters + record DELETE event
- [ ] 6.8 Update `runtime/tools.py` — `_tool_srh_memory_history` extend with event chain
- [ ] 6.9 Update `runtime/hooks.py` — inject session_id into store on session start
- [ ] 6.10 Update `runtime/hooks.py` — inject actor_id from tool context in post_tool_call
- [ ] 6.11 Add `tests/test_schema_module.py` assertions for new schema fields
- [ ] 6.12 Add `tests/test_runtime_import_hygiene.py` test for history/filter handler dispatch

## 7. Integration & Regression

- [ ] 7.1 Run `pytest tests/test_memory_events.py tests/test_scope_filters.py -v`
- [ ] 7.2 Run `pytest tests/ -v` — verify all 511 existing tests still pass
- [ ] 7.3 Run `pytest tests/test_schema_module.py tests/test_runtime_import_hygiene.py -v`
- [ ] 7.4 Verify no stale import paths introduced in runtime modules
- [ ] 7.5 Verify no `except Exception: pass` blocks added
- [ ] 7.6 Check test coverage: event ledger ≥ 90%, scope filters ≥ 90%

## 8. Documentation & Freeze

- [ ] 8.1 Update `docs/TOOLS.md` with new tool parameters
- [ ] 8.2 Update `docs/CHANGELOG.md` with v1.6 entry
- [ ] 8.3 Update `docs/DATA_SAFETY.md` with event storage and scope isolation
- [ ] 8.4 Update `docs/testing/test-coverage.md` with new test counts
- [ ] 8.5 Freeze `tests/test_memory_events.py` — no assertion changes unless bug fix
- [ ] 8.6 Freeze `tests/test_scope_filters.py` — no assertion changes unless bug fix
- [ ] 8.7 Freeze `docs/design/1.6/v1.6-sdd.md` — design locked
- [ ] 8.8 Final validation: `pytest tests/ -q` → 511 + 33 = 544 passed
