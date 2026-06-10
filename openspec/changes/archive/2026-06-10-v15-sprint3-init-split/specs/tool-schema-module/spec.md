## ADDED Requirements

### Requirement: Tool schemas live in runtime/schemas.py
The system SHALL define all 12 registered tool schemas in a dedicated `runtime/schemas.py` module.

#### Scenario: Schemas are module-level dicts
- **WHEN** a developer opens `runtime/schemas.py`
- **THEN** the file contains exactly the 12 `_SRH_*_SCHEMA` dicts and no other runtime logic

#### Scenario: All 12 schemas are present
- **WHEN** `runtime/schemas.py` is loaded
- **THEN** the following names are defined: `_SRH_MEMORY_WRITE_SCHEMA`, `_SRH_MEMORY_SEARCH_SCHEMA`, `_SRH_MEMORY_DELETE_SCHEMA`, `_SRH_PALACE_NAVIGATE_SCHEMA`, `_SRH_REFLECT_NOW_SCHEMA`, `_SRH_SKILL_QUERY_SCHEMA`, `_SRH_COMPILE_PROFILE_SCHEMA`, `_SRH_ASSOCIATE_SCHEMA`, `_SRH_GRAPH_RETRIEVE_SCHEMA`, `_SRH_GRAPH_STATS_SCHEMA`, `_SRH_GRAPH_VIZ_SCHEMA`, `_SRH_MEMORY_HEALTH_SCHEMA`

### Requirement: Package entrypoint re-exports schemas
The system SHALL make every `_SRH_*_SCHEMA` available as an attribute of the `mem_reflection_hermes` package.

#### Scenario: Package import resolves schema
- **WHEN** a caller runs `from mem_reflection_hermes import _SRH_MEMORY_WRITE_SCHEMA`
- **THEN** the import succeeds and returns the dict defined in `runtime/schemas.py`

#### Scenario: Late-bound lookup resolves schema
- **WHEN** `_lb("_SRH_MEMORY_WRITE_SCHEMA")` is invoked from a runtime module
- **THEN** it returns the dict defined in `runtime/schemas.py`

### Requirement: register() uses relocated schemas unchanged
The system SHALL call `ctx.register_tool(name, schema, ...)` with the same schema dicts after relocation as before.

#### Scenario: Tool registration remains functional
- **WHEN** `register(ctx)` is invoked by Hermes Agent
- **THEN** all 12 tools are registered using the schemas from `runtime/schemas.py`
- **AND** no registration error occurs

### Requirement: Package init remains focused
The system SHALL reduce `__init__.py` so it contains only imports, singleton getters, `_lb` helpers, and `register()`.

#### Scenario: Entrypoint size reduction
- **WHEN** `__init__.py` is measured
- **THEN** its line count is under 300 lines
