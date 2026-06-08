# Test Coverage Documentation

> Version: v1.2-beta2
> Last updated: 2026-06-08
> Total tests: **294** (plus 24 Windows temp-dir permission errors, non-code)
> All tests pass on Python 3.14 / Windows 11

---

## Quick Reference

| Module | Test File | Tests | Key Coverage |
|--------|-----------|-------|--------------|
| store.py | test_store.py | 13 | rebuild_index, validate_index, prune_index, lineage boundaries |
| search.py | test_search.py | 16 | RRF fusion, MMR rerank, conflict detection, cache boundaries, graph ranking |
| graph.py | test_graph.py | 13 | step decay, meta zone persistence, distill, cross-zone analysis |
| reflect.py | test_reflect.py | 15 | content gate, fact extraction, micro/full reflection |
| context.py | test_context.py | 10 | 4-layer priority, token budget, skill matching |
| memory_curator.py | test_memory_curator.py | 15 | stale scan, exempt pinned/fresh, archive expired, deep chains, cold storage, full pipeline |
| web/api.py | test_dashboard.py | 14 | CRUD, graph, stats, skills, reflections, zones |
| E2E (all modules) | test_e2e.py | 6 | full lifecycle, update propagation, conflict, priority |
| host contract smoke | test_host_contract_smoke.py | 1 | host contract, 12 tools, 4 hook names, smoke script |
| store compat contracts | test_core_data.py | 10 | frontmatter roundtrip, effectiveness, lineage, safe write |
| runtime graph compat | test_graph_operations.py | 11 | edge CRUD, supersedes, spreading activation, PageRank |
| search compat | test_bm25.py | 7 | tokenisation, BM25 scoring, CJK handling |
| retrieval | test_fusion_rerank.py | 17 | recency, effectiveness, Hebbian additive boost, zone filter |
| query/cache surface | test_query_cache.py | 12 | query templates, TTL cache, invalidation |
| runtime reflection compat | test_reflection.py | 8 | JSON parsing, repair, audit, cadence, supersedes |
| runtime tools compat | test_tool_handlers.py | 8 | lineage cycle check, write/read cycle |
| wave3 retrieval | test_wave3_retrieval.py | 10 | spreading activation, hub detection, BM25 CJK, fusion |
| memory bridge | test_bridge.py | 9 | Dir A/B sync, dedup, zone filter, stats |
| episode compaction | test_compaction.py | 8 | clustering, empty store, dry run, LLM fallback |
| reranker | test_reranker.py | 13 | interface, factory, lazy load, SearchIndex integration |

---

## Module-Level Coverage

### 1. store.py — Memory persistence layer

**File:** `tests/test_store.py`
**Tests:** 13

| Test Class | Tests | Coverage |
|------------|-------|----------|
| TestRebuildIndex | 3 | Empty store, restore memories, drop stale rows |
| TestValidateIndex | 4 | Empty, orphaned rows, orphaned files, hash mismatch |
| TestPruneIndex | 4 | Empty, removes orphaned rows, keeps valid, cleans tags |
| TestLineageBoundaries | 2 | Missing supersedes target rejection, deterministic latest successor |

**Production code touched:**
- `MemoryStore.rebuild_index()`
- `MemoryStore.validate_index()`
- `MemoryStore.prune_index()`
- `write_memory_atomic()`
- `_upsert_memory_row()`
- `_row_to_loaded()`

---

### 2. search.py — Three-layer retrieval

**File:** `tests/test_search.py`
**Tests:** 16

| Test Class | Tests | Coverage |
|------------|-------|----------|
| TestRRFFusion | 4 | Single channel, both channels, k=60 constant, excludes missing |
| TestMMRRerank | 4 | Empty, single candidate, diversity promotion, lambda=0 |
| TestCheckConflict | 5 | Empty store, BM25 path, exclude_ids, threshold tuning, short text |
| TestSearchCacheBoundaries | 1 | include_history cache isolation |
| TestStoreSearchGraphWiring | 2 | store.fusion_search graph injection and ranking behavior |

**Production code touched:**
- `SearchIndex.search()`
- `SearchIndex.check_conflict()`
- `_rrf_fusion()`
- `_mmr_rerank()`
- `_bm25_search_bm25s()`
- `_embed_search()`
- `_ensure_bm25_index()` / `_ensure_embed_index()`

---

### 3. graph.py — Hebbian graph memory

**File:** `tests/test_graph.py`
**Tests:** 13

| Test Class | Tests | Coverage |
|------------|-------|----------|
| TestStepDecay | 5 | Empty, decay reduces weight, prune threshold, step counter, cumulative |
| TestGraphMeta | 1 | ensure_meta persists and refreshes zone |
| TestDistill | 4 | Empty, hub identification, insufficient neighbors, deduplication |
| TestCrossZone | 3 | Empty, bridge detection, same-zone ignored |

**Production code touched:**
- `GraphIndex.associate()`
- `GraphIndex.neighbors()`
- `GraphIndex.step_decay()`
- `GraphIndex.distill()`
- `GraphIndex.cross_zone()`
- `GraphIndex.pagerank()`

---

### 4. reflect.py — Reflection pipeline

**File:** `tests/test_reflect.py`
**Tests:** 15

| Test Class | Tests | Coverage |
|------------|-------|----------|
| TestIsMemorableContent | 6 | Too short, tool output, file path, code pattern, repetitive, normal text |
| TestExtractFacts | 5 | Explicit intent, correction, preference, empty, deduplication |
| TestMicroReflection | 5 | raw_chunk stores episode, skips short, skips non-memorable, heuristic extracts facts, no facts returns none |
| TestFullReflection | 4 | raw_chunk mode, skips tool messages, LLM fallback, empty messages |
| TestReflectLog | 3 | Append/read, recent n limit, audit structure |

**Production code touched:**
- `_is_memorable_content()`
- `_extract_facts_from_turn()`
- `ReflectionEngine.micro()`
- `ReflectionEngine.full()`
- `ReflectionEngine.log()` / `recent()` / `audit()`
- `_append_reflect_log()` / `_read_reflect_log()`

---

### 5. context.py — Context assembly

**File:** `tests/test_context.py`
**Tests:** 10

| Test Class | Tests | Coverage |
|------------|-------|----------|
| TestBuildContextPriority | 4 | Empty store, pinned memories, active via search, always-active skills |
| TestTokenBudget | 2 | Small budget truncates, budget enforcement drops layers |
| TestSkillMatching | 3 | Triggered by overlap, no match, empty query |
| TestFormatting | 2 | Memory truncation, skill basic format |

**Production code touched:**
- `build_context()`
- `_format_memory()`
- `_format_skill()`
- `_match_triggered_skills()`

---

### 6. memory_curator.py — Automated memory lifecycle (v1.2)

**File:** `tests/test_memory_curator.py`
**Tests:** 15

| Test Class | Tests | Coverage |
|------------|-------|----------|
| TestScanForStale | 3 | Finds stale entries, exempts pinned, exempts fresh |
| TestArchiveExpired | 2 | Archives and deletes expired, cold store has entries |
| TestArchiveSuperseded | 2 | Archives deep chains (depth >=2), keeps single chain |
| TestColdStorage | 1 | Append and load round-trip |
| TestFullPipeline | 2 | Runs without crash, cleans up session state |

**Production code touched:**
- `scan_for_stale()`
- `archive_expired()`
- `archive_superseded()`
- `_prune_cold_store()`
- `_load_cold_store()`
- `_run_curator()`

---

### 7. web/api.py — Dashboard API

**File:** `tests/test_dashboard.py`
**Tests:** 14

| Test Class | Tests | Coverage |
|------------|-------|----------|
| TestMemoriesCRUD | 5 | list, create, delete, reorder, zone filter |
| TestGraphEndpoints | 4 | empty, with memories, neighbors not found, zone analysis |
| TestSkillsEndpoint | 1 | list skills |
| TestStatsEndpoint | 1 | basic stats |
| TestReflectionsEndpoint | 3 | empty, with entries, audit empty |
| TestZonesEndpoint | 1 | zones |

**Production code touched:**
- All 15 FastAPI endpoints in `web/api.py`
- `_get_store()` / `_get_graph_interface()`
- Pydantic request models (`MemoryCreate`, `MemoryReorder`)

---

### 8. memory_bridge.py — Bidirectional host sync (v1.1)

**File:** `tests/test_bridge.py`
**Tests:** 9

| Test | Coverage |
|------|----------|
| test_dir_a_mirror_builtin | Built-in memory tool args → plugin store |
| test_dir_a_skip_duplicate | Duplicate detection in Dir A |
| test_dir_b_mirror_short_core | Plugin → built-in for short core memories |
| test_dir_b_skip_long | Dir B skips bodies >200 chars |
| test_dir_b_skip_non_core | Dir B only syncs zone=core |
| test_bridge_stats | Stats accumulation |
| test_refine_body_strips_tool_noise | `_refine_body` tool-noise stripping |

---

### 9. episode compaction (v1.1)

**File:** `tests/test_compaction.py`
**Tests:** 8

| Test Class | Tests | Coverage |
|------------|-------|----------|
| TestCompactEpisodeZone | 8 | Empty store, clusters episodes, respects dry_run, LLM fallback, idempotency |

**Production code touched:**
- `_compact_episode_zone()`

---

### 10. E2E — Cross-module integration

**File:** `tests/test_e2e.py`
**Tests:** 6

| Test | Coverage |
|------|----------|
| test_create_search_graph_reflect_context | Store → Search → Graph → Context full chain |
| test_update_propagates | Update invalidates search caches |
| test_reflection_creates_memories | ReflectionEngine → Store |
| test_conflict_avoids_duplicates | SearchIndex.check_conflict prevents duplicates |
| test_context_priority_layers | Pinned > Active ordering |
| test_graph_pagerank_and_decay | GraphIndex.associate → pagerank → step_decay |

---

### 11. Host Contract Smoke — Tool and hook surface

**File:** `tests/test_host_contract_smoke.py`
**Tests:** 1

Runs `scripts/smoke_host_contract.py` under pytest so the acceptance smoke is part
of the normal suite. The script validates frontmatter preservation,
lineage-aware recall, supersedes validation, temporal hints, reflection audit
round-trip, **12 registered tools**, and the 4 public hook names.

---

## Legacy Module Coverage

These modules are tested for regression safety even where the module is primarily
accessed through the new package layout.

### core.py (store compat)

**File:** `tests/test_core_data.py`
**Tests:** 10

- Frontmatter roundtrip (YAML → dict → file → object)
- Effectiveness dataclass (decay, range, combined)
- Lineage (chain depth, cycle detection)
- Safe atomic write
- Intent classification (correction, append)

### graph/ compat surface

**File:** `tests/test_graph_operations.py`
**Tests:** 11

- Edge CRUD (upsert, accumulate, symmetric)
- Supersedes filtering
- Spreading activation (chain decay, convergence)
- PageRank (star hub, isolated node)
- Graph regression (meta refresh, decay prune)
- Thread safety (multithreaded reads)

### search BM25

**File:** `tests/test_bm25.py`
**Tests:** 7

- Tokenisation (English, CJK bigrams, mixed)
- BM25 scoring (TF, IDF, length norm, effectiveness boost)
- Empty query / no-match handling

### retrieval (fusion + rerank)

**File:** `tests/test_fusion_rerank.py`
**Tests:** 17

- Recency exponential decay
- Effectiveness boost / decay
- Hebbian boost (enabled vs disabled)
- Hub bonus (PageRank > 0.15)
- Supersedes penalty
- Channel normalization
- Zone filtering
- Weight sensitivity
- Full pipeline top-3

### query templates/cache

**File:** `tests/test_query_cache.py`
**Tests:** 12

- Query templates (recent, by_zone, by_tag, unknown)
- Result cache (set/get, miss, TTL expiry, invalidate, clear, stats, eviction)

### reflection runtime compat

**File:** `tests/test_reflection.py`
**Tests:** 8

- JSON output parsing (valid, code fence, empty, non-JSON)
- Truncated JSON repair
- Audit entry structure
- Reflection cadence (pre_llm_call counter)
- Supersedes regression (exclusion from conflict check)

### runtime tools compat

**File:** `tests/test_tool_handlers.py`
**Tests:** 8

- Lineage cycle detection (direct, 3-node, self, no chain)
- Lineage root / latest helpers
- Write/read cycle

### wave3 retrieval

**File:** `tests/test_wave3_retrieval.py`
**Tests:** 10

- Spreading activation (empty, single, two nodes, convergence, adjacency cache)
- Hub detection (PageRank)
- Time sorting (created desc)
- BM25 CJK (stopwords)
- Fusion search (minimal, zone filter)

---

## Running Tests

```bash
# All tests
pytest tests/ -v

# Single module
pytest tests/test_store.py -v
pytest tests/test_search.py -v
pytest tests/test_graph.py -v
pytest tests/test_reflect.py -v
pytest tests/test_context.py -v
pytest tests/test_dashboard.py -v
pytest tests/test_e2e.py -v
pytest tests/test_memory_curator.py -v
pytest tests/test_bridge.py -v
pytest tests/test_compaction.py -v

# Specific test class
pytest tests/test_search.py::TestRRFFusion -v

# Specific test method
pytest tests/test_e2e.py::TestFullLifecycle::test_conflict_avoids_duplicates -v

# With coverage
pytest tests/ --cov=. --cov-report=term-missing
```

---

## Notes for Future Maintenance

- **Import isolation:** Tests for runtime modules (`core/*.py`, `reflection/*.py`, `memory/*.py`) use `importlib.util.spec_from_file_location` to avoid package-relative import issues. Fallback `try/except ImportError` blocks in production code support both package and direct loading.
- **Windows cleanup:** Temp directory fixtures include retry loops with `shutil.rmtree` to handle SQLite file locking on Windows.
- **Search cache invalidation:** `SearchIndex` caches BM25 index, embedding array, and result cache. E2E tests that mutate memories must clear all three (`_bm25_retriever = None`, `_embed_array = None`, `invalidate_cache()`, `_embed_single.cache_clear()`).
- **Dashboard mock isolation:** `test_dashboard.py` mocks `mem_reflection_hermes` via `sys.modules` before loading `web/api.py`. `web/api.py` now checks `sys.modules` first to avoid overwriting the mock.
- **Reflection log isolation:** `ReflectionEngine` accepts a test log path, and both reflection/E2E tests write logs under their temp directories.
- **Plugin data dir isolation:** Dashboard tests use `store._test_data_dir` (set by `temp_dashboard` fixture) to write reflection logs, preventing cross-test pollution.
