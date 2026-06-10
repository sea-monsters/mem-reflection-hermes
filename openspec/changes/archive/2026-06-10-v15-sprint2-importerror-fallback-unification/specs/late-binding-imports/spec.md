## ADDED Requirements

### Requirement: Unified late-binding helper exists
The system SHALL provide a single `_lb(name)` helper in `runtime/_lb.py` that resolves any project module by dotted name and returns the module object, or `None` if the module cannot be resolved.

#### Scenario: Successful module resolution
- **WHEN** a caller invokes `_lb("core.store")`
- **THEN** the helper returns the `core.store` module object

#### Scenario: Failed module resolution
- **WHEN** a caller invokes `_lb("nonexistent.module")`
- **THEN** the helper returns `None` without raising an exception

#### Scenario: Repeated lookups are cached
- **WHEN** `_lb("core.store")` is invoked multiple times
- **THEN** the same module object is returned on subsequent calls

### Requirement: Late-binding helper covers all project imports
All modules in `memory/curator/*`, `core/*`, `reflection/*`, and `runtime/*` SHALL use `_lb()` or an equivalent `_resolve(name)` leaf helper for cross-module imports instead of inline `try/except ImportError` blocks.

#### Scenario: Curator action imports runtime graph
- **WHEN** `memory/curator/actions.py` needs `runtime.graph.get_graph_manager_compat`
- **THEN** it resolves the import via `_lb("runtime.graph")` or `_lb("mem_reflection_hermes.runtime.graph")`

#### Scenario: Curator cold store imports core store
- **WHEN** `memory/curator/cold_store.py` needs `core.store.plugin_config` or `core.store.MemoryFrontmatter`
- **THEN** it resolves the import via `_lb("core.store")` and accesses attributes on the resolved module

### Requirement: No silent fallback blocks remain
After the refactor, no source file in the affected modules SHALL contain `except Exception: pass` or `except ImportError: pass` blocks used solely to silence import failures.

#### Scenario: Static analysis confirms no bare except-pass
- **WHEN** an AST visitor scans all `.py` files under `memory/curator/`, `core/`, `reflection/`, and `runtime/`
- **THEN** no `ExceptHandler` with a single `Pass` node and a bare or `Exception` type is found

### Requirement: Standalone loading continues to work
The system SHALL continue to support standalone module loading via `importlib.util.spec_from_file_location` in tests and scripts. When a module is loaded standalone, `_lb()` SHALL gracefully return `None` rather than raising, preserving the existing fail-open behavior.

#### Scenario: Test loads curator helpers standalone
- **WHEN** a test uses `importlib.util.spec_from_file_location` to load `memory/curator/helpers.py`
- **THEN** the module loads successfully and any cross-module imports via `_lb()` return `None` instead of raising
