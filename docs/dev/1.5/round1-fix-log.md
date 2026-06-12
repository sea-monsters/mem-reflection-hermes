# v1.5 Round 1 Fix Log

**Date**: 2026-06-11
**Source**: follow-up to [round1-code-review.md](D:/Codex_lib/mem-reflection-hermes/docs/dev/1.5/round1-code-review.md)
**Goal**: apply the fastest safe fixes for the highest-risk Round 1 findings.

## Fixed in this pass

### 1. Runtime stale import paths

Updated `runtime/hooks.py`:

- `runtime_reflection` -> `..reflection.runtime`
- `memory_curator` -> `..memory.curator`
- `memory_bridge` -> `..memory.bridge`
- `from . import _config_compaction` -> `from .. import _config_compaction`

Updated `runtime/tools.py`:

- `memory_bridge` import -> `..memory.bridge`
- removed stale `from .store import profile_mode_enabled`
- reused the already-defined `_profile_mode_enabled()` helper

This directly addresses the main v1.5 post-refactor risk where compaction, curator, bridge sync, and profile compile paths could silently degrade under the real package layout.

### 2. Schema drift

Updated `runtime/schemas.py` to match actual handler behavior:

- added `supersedes_reason` to `_SRH_MEMORY_WRITE_SCHEMA`
- added `explain` to `_SRH_MEMORY_SEARCH_SCHEMA`
- changed `_SRH_COMPILE_PROFILE_SCHEMA.mode` from `profile|summary|stats` to `profile|palace_index|zone`

### 3. Tool documentation drift

Updated [docs/TOOLS.md](D:/Codex_lib/mem-reflection-hermes/docs/TOOLS.md) so `srh_compile_profile` documents the real supported modes:

- `profile`
- `palace_index`
- `zone`

### 4. Regression guards

Updated [tests/test_schema_module.py](D:/Codex_lib/mem-reflection-hermes/tests/test_schema_module.py):

- asserts `supersedes_reason` is exposed in the write schema
- asserts `explain` is exposed in the search schema
- asserts compile-profile mode enum matches the handler

Added [tests/test_runtime_import_hygiene.py](D:/Codex_lib/mem-reflection-hermes/tests/test_runtime_import_hygiene.py):

- checks that stale v1.5-pre-split import strings do not reappear in `runtime/hooks.py`
- checks that stale bridge/profile imports do not reappear in `runtime/tools.py`

Updated [tests/test_hooks.py](D:/Codex_lib/mem-reflection-hermes/tests/test_hooks.py):

- isolates checkpoint persistence helpers with monkeypatches during unit tests
- keeps session-state tests focused on hook bag lifecycle instead of real checkpoint file I/O
- removes the reproduced hang in `test_cleanup_session_state_removes_from_memory`

## Not fixed in this pass

- hook lifecycle reliability beyond import-path repair
- broader curator test gaps documented in `sprint1-test-review.md`
- deeper mem0 / hy-memory / graphiti parity gaps
- any larger redesign of runtime hook responsibility boundaries

## Validation target for this pass

Primary validation focus is:

1. schema regression coverage
2. import-hygiene regression coverage
3. hook session-state unit coverage without checkpoint side effects
4. a narrow runtime smoke on changed surfaces

Any remaining hook-runtime instability should be handled in the next pass with more targeted lifecycle tests rather than folded into this quick repair.
