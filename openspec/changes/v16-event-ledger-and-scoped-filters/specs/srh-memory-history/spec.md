## MODIFIED Requirements

### Requirement: Memory history returns event chains
The system SHALL extend `srh_memory_history` to optionally include the event ledger.

#### Scenario: History with events includes full lifecycle
- **GIVEN** a memory has ADD, UPDATE, and DELETE events
- **WHEN** `srh_memory_history` is called with `include_events=true`
- **THEN** the response includes the supersedes chain AND the event chain with timestamps

#### Scenario: History without events returns only supersedes chain
- **GIVEN** a memory has both supersedes relationships and events
- **WHEN** `srh_memory_history` is called without `include_events`
- **THEN** only the supersedes chain is returned (backward compatible)

#### Scenario: History filters by event type
- **GIVEN** a memory has ADD, UPDATE, PIN, and DELETE events
- **WHEN** `srh_memory_history` is called with `include_events=true` and `event_types=["UPDATE", "DELETE"]`
- **THEN** only UPDATE and DELETE events are included in the response

#### Scenario: History filters by session
- **GIVEN** events exist for session "sess-a" and "sess-b"
- **WHEN** `srh_memory_history` is called with `session_id="sess-a"`
- **THEN** only events from "sess-a" are included

## ADDED Requirements

### Requirement: History output includes actor information
The system SHALL include actor_id in event entries when available.

#### Scenario: Event shows who made the change
- **GIVEN** a memory was updated by actor "user-proxy"
- **WHEN** `srh_memory_history` returns the UPDATE event
- **THEN** the event entry contains `actor_id: "user-proxy"`
