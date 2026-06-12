## 1. RED Phase — Intent-Facing Tests

- [x] 1.1 Create `tests/test_schema_module.py` with tests verifying all 12 schemas are defined in `runtime/schemas.py`.
- [x] 1.2 Add test verifying `from mem_reflection_hermes import _SRH_MEMORY_WRITE_SCHEMA` resolves the same dict as `runtime/schemas.py`.
- [x] 1.3 Add test verifying `_lb("_SRH_MEMORY_WRITE_SCHEMA")` returns the schema dict.
- [x] 1.4 Add test verifying `register(ctx)` registers exactly 12 tools without schema mutation.
- [x] 1.5 Add test verifying `__init__.py` line count is under 300.
- [x] 1.6 Run `pytest tests/test_schema_module.py -v` and confirm tests fail (RED phase).

## 2. GREEN Phase — Implementation

- [x] 2.1 Create `runtime/schemas.py` and move all 12 `_SRH_*_SCHEMA` dicts from `__init__.py`.
- [x] 2.2 Update `__init__.py` to import schemas from `.runtime.schemas` and add them to `__all__`.
- [x] 2.3 Verify `__init__.py` line count is under 300.

## 3. Verification

- [x] 3.1 Run `pytest tests/test_schema_module.py -v` until all tests pass.
- [x] 3.2 Run full test suite `pytest tests/ -v` and confirm no regressions.
- [x] 3.3 Verify `from mem_reflection_hermes import register` still works and `_lb` symbol lookup resolves schemas.
