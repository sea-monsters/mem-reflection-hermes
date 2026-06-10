# v1.4-beta Release Changelog

> **Branch**: `v1.4-beta`
> **Date**: 2026-06-10
> **Base**: `v1.3-beta` (5836e2d) + 92 commits ahead of `main`
> **Tests**: 413 passed, 0 failed

---

## New Modules

| File | Lines | Description |
|------|-------|-------------|
| `core/backend.py` | 29 | `SearchBackendLike` protocol + `SearchBackendCapabilities` flags |
| `core/config.py` | ~100 | Typed config models with diagnostics, validation, safe defaults |
| `runtime/_lb.py` | 13 | Shared late-binding helper (`_lb`) extracted from tools.py + hooks.py |
| `runtime/checkpoint.py` | ~120 | Atomic session checkpoint: JSON persistence, corrupt backup recovery, pending-stage tracking |

## New Tests (v1.4)

| File | Tests | Description |
|------|-------|-------------|
| `tests/test_async_writer.py` | async writer | Async memory write thread |
| `tests/test_backend.py` | backend capability | `SearchBackendLike` protocol |
| `tests/test_checkpoint.py` | checkpoint | Session checkpoint persistence + recovery |
| `tests/test_checkpoint_backup_failure.py` | backup failure | Corrupt backup handling |
| `tests/test_config.py` | typed config | Config validation + diagnostics |
| `tests/test_entity_extraction.py` | entity extraction | Regex-first + optional spaCy NER |
| `tests/test_graph_distil_failure.py` | graph distillation | Failure path coverage |
| `tests/test_hooks.py` | hooks | v0.16.0 enhanced hooks |
| `tests/test_optional_deps.py` | optional deps | jieba/spaCy/tiktoken fallbacks |
| `tests/test_reranker_exceptions.py` | reranker exceptions | Cohere/cross-encoder error paths |

## Bug Fixes (from v1.4 six-layer review)

| ID | File | Fix |
|----|------|-----|
| L1-01 | `__init__.py` | Replaced broken `match_skills` (called non-existent `SearchIndex.match_skills`) with Jaccard-like token overlap implementation |
| L1-02 | `__init__.py` | Added missing imports `_reflection_mode` and `_save_pending_skill_candidates` from `reflection.runtime` |
| L1-03 | `__init__.py` | Implemented `load_zone_summary` and `save_zone_summary` (were no-op stubs) |
| L1-04 | `memory/context.py` | Fixed dummy config: `compression.enabled = True`, `recall_timeout_ms = 1500` |
| L2-01 | `memory/curator.py` | `clean_orphan_edges` no longer returns early on empty `all_ids`; passes empty set to `GraphIndex` for full orphan sweep |
| L3-01 | `memory/curator.py` | `merge_similar` now tracks keeper lineage via `supersedes` field |
| L4-01 | `core/store.py` | Added `_mark_changed()` after `prune_index()` when memories removed |
| L5-01 | `memory/curator.py` | Replaced 3 silent `except Exception: pass` blocks with `logger.warning` |
| L5-02 | `memory/curator.py` | Added warning log for `compact_superseded_chains` update failure |
| L6-02 | `runtime/tools.py`, `runtime/hooks.py` | Extracted shared `_lb` into `runtime/_lb.py`; removed phantom `build_palace_index` proxy |

## Documentation

| File | Change |
|------|--------|
| `CLAUDE.md` | Updated to v1.4-beta: ContextBundle, checkpoint, entity index, backend, CJK tokenizer |
| `docs/ARCHITECTURE.md` | v1.4 module layout, import order rules, thread safety table |
| `docs/CHANGELOG.md` | v1.4-beta section: Context Reliability, Retrieval, Runtime, Entity Recall |
| `docs/DASHBOARD.md` | Security model note (host middleware protects plugin routes) |
| `docs/TOOLS.md` | v1.4 explain flag, 12 tools |
| `docs/testing/test-coverage.md` | v1.4 coverage analysis (intent 97%, impl 90%, boundary 82%) |
| `docs/mem0-comparison-report-v2.md` | Deep comparison: mem0 v2.0.4 + hy-memory v0.3.6 + HeLa-Mem/MemForest |
| `docs/dev/1.4/` | v1.4 development tracking + v1.5 refactor plan |
| `docs/design/1.4/` | v1.4 design documents |
| `docs/testing/v1.4-round1-review.md` | Six-layer code review (L1-L6) |

## Cleanup

- Removed `.hermes/` from git tracking (local-only config)
- Removed phantom `build_palace_index` from `runtime/tools.py`
- `_lb` unified in `runtime/_lb.py` (no duplicate definitions)

## Version

```yaml
# plugin.yaml
name: mem-reflection-hermes
version: 1.4-beta
```
