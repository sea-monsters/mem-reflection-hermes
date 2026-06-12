## MODIFIED Requirements

### Requirement: Curator modules use unified import resolution
All modules under `memory/curator/` SHALL use `_lb()` from `runtime/_lb.py` (or a local `_resolve` wrapper) to resolve cross-module imports. They SHALL NOT contain inline `try/except ImportError: pass` or `except Exception: pass` blocks.

#### Scenario: CleanOrphanEdges resolves graph manager
- **WHEN** `CleanOrphanEdges.execute` needs `get_graph_manager_compat`
- **THEN** it calls `_lb("runtime.graph")` and checks for `None` before calling `get_graph_manager_compat()`

#### Scenario: Cold store resolves core.store symbols
- **WHEN** `cold_store.py` needs `plugin_config` or `MemoryFrontmatter`
- **THEN** it resolves `core.store` once via `_lb()` and accesses attributes on the resolved module

#### Scenario: Report module resolves reflection runtime log helper
- **WHEN** `report.py` needs `_append_reflect_log` from `reflection.runtime`
- **THEN** it resolves `reflection.runtime` via `_lb()` and calls `_append_reflect_log` only when the module is available

### Requirement: Curator actions surface import failures as warnings
When `_lb()` returns `None` for an optional dependency, the caller SHALL log a `logger.warning` message and return a graceful default, preserving the existing fail-open behavior without silent `pass`.

#### Scenario: Graph manager unavailable
- **WHEN** `CleanOrphanEdges` cannot resolve `runtime.graph`
- **THEN** it logs a warning and returns a `CuratorResult` with `orphan_edges == 0`

#### Scenario: Reflection log helper unavailable
- **WHEN** `report.py` cannot resolve `reflection.runtime`
- **THEN** it logs a warning and skips report logging without raising
