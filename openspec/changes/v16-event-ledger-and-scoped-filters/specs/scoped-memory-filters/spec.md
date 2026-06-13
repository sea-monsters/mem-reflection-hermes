## ADDED Requirements

### Requirement: Scope fields exist in frontmatter
The system SHALL support optional `user_id`, `agent_id`, and `run_id` fields in memory frontmatter.

#### Scenario: Write with scope fields
- **WHEN** `srh_memory_write` is called with `user_id="u1"`, `agent_id="a1"`, `run_id="r1"`
- **THEN** the memory's frontmatter contains these fields and the SQLite row has corresponding columns

#### Scenario: Write without scope fields
- **WHEN** `srh_memory_write` is called without scope fields
- **THEN** the memory is stored with NULL scope columns and remains discoverable by all searches

### Requirement: Scope fields are indexed
The system SHALL create indexes on `user_id`, `agent_id`, and `run_id` columns for efficient filtering.

#### Scenario: Indexes exist after migration
- **WHEN** a store is initialized or migrated
- **THEN** indexes `idx_memories_user_id`, `idx_memories_agent_id`, `idx_memories_run_id`, and `idx_memories_scoped` exist

### Requirement: Search respects scope filters
The system SHALL filter search results by `user_id`/`agent_id`/`run_id` before scoring.

#### Scenario: Filter by user_id
- **GIVEN** memories exist with `user_id="u1"` and `user_id="u2"`
- **WHEN** `srh_memory_search` is called with `filters={"user_id": "u1"}`
- **THEN** only memories with `user_id="u1"` are returned

#### Scenario: Filter by agent_id
- **GIVEN** memories exist with `agent_id="a1"` and `agent_id="a2"`
- **WHEN** `srh_memory_search` is called with `filters={"agent_id": "a1"}`
- **THEN** only memories with `agent_id="a1"` are returned

#### Scenario: Filter by run_id
- **GIVEN** memories exist with `run_id="r1"` and `run_id="r2"`
- **WHEN** `srh_memory_search` is called with `filters={"run_id": "r1"}`
- **THEN** only memories with `run_id="r1"` are returned

### Requirement: Combined scope filters use AND logic
The system SHALL apply combined filters with AND logic.

#### Scenario: Multiple filters combined
- **GIVEN** memories exist for (u1, a1), (u1, a2), (u2, a1)
- **WHEN** `srh_memory_search` is called with `filters={"user_id": "u1", "agent_id": "a1"}`
- **THEN** only memories matching BOTH conditions are returned

#### Scenario: Partial filter with NULL match
- **GIVEN** a memory has `user_id="u1"` and NULL `agent_id`
- **WHEN** searched with `filters={"user_id": "u1", "agent_id": "a1"}`
- **THEN** the memory is NOT returned (NULL does not match specific filter)

### Requirement: NULL scope is universally visible
The system SHALL treat NULL scope fields as matching all requests.

#### Scenario: Search without filters returns all
- **GIVEN** memories exist with scopes (u1, a1, r1) and (NULL, NULL, NULL)
- **WHEN** `srh_memory_search` is called without filters
- **THEN** all memories are returned

#### Scenario: Filtered search includes NULL scoped memories when filter is NULL
- **GIVEN** a memory has NULL `user_id`
- **WHEN** searched with `filters={"user_id": null}`
- **THEN** the memory IS returned

### Requirement: Scope fields are immutable on update
The system SHALL reject attempts to modify scope fields during update.

#### Scenario: Update preserves existing scope
- **GIVEN** a memory has `user_id="u1"`
- **WHEN** `srh_memory_write` updates the body without specifying user_id
- **THEN** the memory retains `user_id="u1"`

#### Scenario: Update with different scope is rejected
- **GIVEN** a memory has `user_id="u1"`
- **WHEN** `srh_memory_write` attempts to update with `user_id="u2"`
- **THEN** the scope change is ignored and a warning is logged

### Requirement: Curator operations respect scope
The system SHALL apply default scope filters to curator actions.

#### Scenario: Curator only archives within scope
- **GIVEN** stale memories exist for user_id="u1" and user_id="u2"
- **WHEN** the curator runs with default filter `user_id="u1"`
- **THEN** only stale memories for "u1" are archived

### Requirement: Delete supports batch scope filtering
The system SHALL support deleting memories by scope filters.

#### Scenario: Delete by filters
- **GIVEN** memories exist with `run_id="r1"`
- **WHEN** `srh_memory_delete` is called with `filters={"run_id": "r1"}` (without specific id)
- **THEN** all memories matching the filter are deleted

#### Scenario: Batch delete without filters is rejected
- **WHEN** `srh_memory_delete` is called without both `id` and `filters`
- **THEN** the operation is rejected with an error

### Requirement: Explain output includes applied filters
The system SHALL include applied filters in search explain output.

#### Scenario: Explain shows filters
- **WHEN** `srh_memory_search` is called with `explain=true` and `filters={"user_id": "u1"}`
- **THEN** the explain metadata includes `applied_filters: {"user_id": "u1"}`
