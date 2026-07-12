## ADDED Requirements

### Requirement: Event ledger table exists
The system SHALL maintain a `memory_events` SQLite table with columns: `id`, `memory_id`, `event_type`, `old_body`, `new_body`, `old_frontmatter`, `new_frontmatter`, `session_id`, `actor_id`, `created_at`.

#### Scenario: Store initialization creates event table
- **WHEN** a new `MemoryStore` is initialized
- **THEN** the SQLite database contains a `memory_events` table with the required schema

#### Scenario: Existing store migrates to add event table
- **WHEN** an existing v1.5 store is opened
- **THEN** the `memory_events` table is created automatically via schema migration

### Requirement: ADD events are recorded
The system SHALL record an `ADD` event when a new memory is written via `srh_memory_write`.

#### Scenario: Write new memory records ADD event
- **WHEN** `srh_memory_write` creates a new memory with body "test content"
- **THEN** `get_memory_events(memory_id)` returns exactly one event with `event_type="ADD"`, `new_body="test content"`, and `old_body` is NULL

#### Scenario: Write with frontmatter records full frontmatter
- **WHEN** `srh_memory_write` creates a memory with tags `["tag1"]` and zone `"work"`
- **THEN** the ADD event's `new_frontmatter` contains the tags and zone fields

### Requirement: UPDATE events are recorded
The system SHALL record an `UPDATE` event when an existing memory is modified.

#### Scenario: Update existing memory records UPDATE event
- **WHEN** `srh_memory_write` updates an existing memory's body from "old" to "new"
- **THEN** `get_memory_events(memory_id)` returns an `UPDATE` event with `old_body="old"` and `new_body="new"`

#### Scenario: Update frontmatter without body change
- **WHEN** an existing memory's pinned flag changes from false to true
- **THEN** the UPDATE event's `old_frontmatter` has `pinned=false` and `new_frontmatter` has `pinned=true`

### Requirement: SUPERSEDE events are recorded
The system SHALL record a `SUPERSEDE` event when a memory supersedes another.

#### Scenario: Supersede records SUPERSEDE event
- **WHEN** `srh_memory_write` creates a new memory that supersedes an existing memory
- **THEN** the superseded memory has a `SUPERSEDE` event recording the transition

### Requirement: DELETE events are recorded
The system SHALL record a `DELETE` event before a memory is removed.

#### Scenario: Delete memory records DELETE event
- **WHEN** `srh_memory_delete` removes a memory
- **THEN** `get_memory_events(memory_id)` returns a `DELETE` event with `old_body` containing the final body and `new_body` is NULL

#### Scenario: Deleted memory events remain queryable
- **WHEN** a memory is deleted
- **THEN** `get_memory_events(memory_id)` still returns all historical events for that memory_id

### Requirement: PIN and UNPIN events are recorded
The system SHALL record `PIN` and `UNPIN` events when a memory's pinned status changes.

#### Scenario: Pin memory records PIN event
- **WHEN** a memory is pinned
- **THEN** a `PIN` event is recorded for that memory

#### Scenario: Unpin memory records UNPIN event
- **WHEN** a pinned memory is unpinned
- **THEN** an `UNPIN` event is recorded for that memory

### Requirement: Event queries support filtering
The system SHALL support querying events with filters for `event_types`, `session_id`, and time range.

#### Scenario: Filter by event type
- **WHEN** `get_memory_events(memory_id, event_types=["UPDATE", "DELETE"])` is called
- **THEN** only UPDATE and DELETE events are returned, ADD events are excluded

#### Scenario: Filter by session ID
- **WHEN** `get_memory_events(memory_id, session_id="sess-123")` is called
- **THEN** only events with matching session_id are returned

#### Scenario: Filter with limit
- **WHEN** `get_memory_events(memory_id, limit=2)` is called on a memory with 5 events
- **THEN** exactly 2 most-recent events are returned

### Requirement: Session and actor tracking
The system SHALL record `session_id` and `actor_id` for each event.

#### Scenario: Session ID from hook context
- **WHEN** a memory is written during an active session
- **THEN** the event's `session_id` matches the current session

#### Scenario: Actor ID defaults to agent
- **WHEN** a memory is written without explicit actor
- **THEN** the event's `actor_id` is "agent"

#### Scenario: Explicit actor override
- **WHEN** a tool handler writes a memory with actor_id="srh_memory_write"
- **THEN** the event's `actor_id` is "srh_memory_write"

### Requirement: Event frontmatter size limits
The system SHALL handle large frontmatter gracefully.

#### Scenario: Large frontmatter is truncated
- **WHEN** a memory has frontmatter exceeding the storage threshold
- **THEN** the event stores truncated frontmatter preserving key fields (id, created, source, confidence, pinned, tags, zone, supersedes)

### Requirement: Event ledger is WAL-safe
The system SHALL ensure event writes are atomic with memory writes.

#### Scenario: Event and memory written in same transaction
- **WHEN** a memory is written and the process crashes before commit
- **THEN** neither the memory nor the event is persisted
