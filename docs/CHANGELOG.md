# Changelog

## v1.7 — Scope-Aware Reflection Propagation (release hardening)

### Phase A: Reflection Scope Context

- **Central scope helper**: `core/scope.py` now exposes a shared `scope_from_context()` helper so hooks, curator, and reflection can resolve `user_id` / `agent_id` / `run_id` the same way.
- **Reflection write paths**: `_run_full_reflection()`, `_run_micro_reflection()`, `_run_embedding_reflection()`, `_run_raw_chunk_reflection()`, and `_compact_episode_zone()` now stamp scope fields onto new memories when the host provides them.
- **Reflection reads and conflict checks**: scoped reflection paths now pass `filters` into `list_active()`, `check_conflict()`, and compaction reads instead of falling back to global store reads.
- **Hook propagation**: `pre_llm_call` and `on_session_end` now forward scope filters into context assembly, micro-reflection, full reflection, compaction, and curator runs.
- **Manual reflection tool**: `srh_reflect_now` now accepts optional `filters` so agents can manually run reflection without accidentally widening the scope.

### Phase B: Refined Extraction Baseline

- **Shared extraction module**: `reflection/extraction.py` now centralizes refined candidate extraction so runtime and engine stop drifting apart.
- **Typed candidate kinds**: extracted candidates now carry `kind` / `priority` metadata for `intent`, `correction`, `decision`, `todo`, `preference`, `policy`, and `procedure`.
- **Lossless propagation**: `kind` now survives from extraction into `memory_candidates` and `accepted_memories`, so downstream callers can inspect what kind of memory was stored.
- **Regression coverage**: new `tests/test_reflection_refinement.py` locks the decision / todo / preference behavior before future tuning.

### Phase C: Semantic Supersedes Start

- **Semantic resolver**: `reflection/supersedes_resolver.py` now separates correction, merge, store, skip, and scope-split decisions from raw conflict detection.
- **Heuristic reflection update**: embedding-based micro/full reflection paths now preserve explicit memory intent without automatically promoting it to supersedes, while true correction flows still supersede the prior memory.
- **LLM reflection update**: full/micro LLM reflection paths now route candidate supersedes through the same resolver so generic intent strips replacement edges instead of writing them blindly.
- **Scope guard**: `MemoryStore.put()` now rejects supersedes targets that belong to a different scope or tenant root.
- **Regression coverage**: `tests/test_semantic_supersedes.py` locks correction, merge, store, scope-split, and cross-scope rejection behavior.

### Phase D: Typed Fact Sidecar Start

- **Typed sidecar table**: `core/graph.py` now stores typed fact rows with source memory, target memory, episode lineage, relation, kind, and invalidation metadata.
- **Reflection writes**: micro/full/raw-chunk reflection now best-effort record typed sidecar facts when a graph backend is attached to the store.
- **Distillation lineage**: `GraphIndex.distill()` now records both the semantic summary row and member-of relations in the typed sidecar.
- **Regression coverage**: `tests/test_typed_fact_sidecar.py` locks sidecar write, invalidation, and distill lineage behavior.

### Phase E: Compaction Quality

- **Scored fallback**: `_compact_episode_zone()` and the LLM fallback now prefer concise, conclusion-like fragments instead of the longest raw transcript chunk.
- **Conservative merge**: the fallback may join up to two non-duplicate high-signal fragments when they complement each other.
- **Token accounting**: compaction summaries now report source/summary token counts and compression ratio so fallback quality can be inspected directly.
- **Quality gate**: verbose or noisy LLM summaries are rejected when they score worse than the scored fallback, and the fallback summary is used instead.
- **Regression coverage**: `tests/test_compaction.py` now locks the representative-fragment behavior, token accounting, and noisy LLM fallback regressions.

### Phase F: Release Hardening

- **Windows test isolation**: pytest now uses a per-run plugin-specific `basetemp`, avoiding stale Windows temp-directory ACL conflicts during suite setup.
- **Recovery callback compatibility**: runtime import-hygiene tests now accept scoped recovery kwargs, matching the current checkpoint recovery contract.
- **Scoped compaction boundary**: reflection scope tests now assert that compacting one tenant scope does not supersede another scope's raw episodes.
- **Coverage index sync**: `docs/testing/test-coverage.md` has been synchronized with the current `638` collected tests and v1.7 acceptance surfaces.
- **Full-suite validation**: current v1.7 hardening baseline is `pytest tests -q` -> `686 passed` (includes 2026-06-26 round-5 fixes).
- **Test grouping simplification**: markers are now registered in `pytest.ini` and auto-assigned per-file in `tests/conftest.py`; new `v17` group covers the round-3 functional fixes (typed sidecar invalidation, semantic supersedes merge/scope_split, kind typing, ScopeIntent). Marker coverage closed for the 11 previously-untagged test files.

### Round-3 Functional Fixes (`v17`)

- **P1-1 typed sidecar invalidation**: `core/graph.py` gains `invalidate_facts_for_memories()`; `_record_typed_fact_sidecar()` now accepts `superseded_memory_ids` and all supersede/compaction paths invalidate the folded facts.
- **P1-2 semantic merge/scope_split**: `_record_semantic_relation_sidecar()` writes `merges` / `scope_split_with` typed edges so the resolver's decision is no longer silently dropped; merge also invalidates the merge target's facts.
- **P2-1 kind typing**: `REFINED_MEMORY_KINDS` vocabulary + `normalize_memory_kind()`; the LLM reflection schema and prompts now emit `kind`, ending the silent degeneration of the sidecar's kind column to `fact`.
- **P2-2 dead-code cleanup**: removed the latent broken `_memory_tokens` import in `reflection/engine.py`; consolidated the triplicated `_is_memorable_content` / `_is_noise_text` / `_text_similarity` onto the CJK-aware `extraction.py` source of truth.
- **P2-3 ScopeIntent**: `core/scope.py` introduces `ScopeIntent` (UNSCOPED/TENANT/GLOBAL_ONLY) and `global_only_scope()` so the IS NULL intent is finally expressible; `None`/`{}` semantics unchanged for backward compatibility.
- **A5 palace_recall schema stability**: `runtime/tools.py::_tool_srh_palace_recall` now returns `graph_expanded: []` in scoped mode to keep the response schema stable; `tests/test_palace_recall.py` locks the regression.
- **Regression coverage**: 20 new tests (suite 617 -> 638); see `docs/dev/1.7/v1.7-round3-functional-audit.md`.

### Round-4 P3 Root-Cause Fixes

- **P3-1 batch stats recording**: `record_memory_stat()` loops in `runtime/tools.py` and `runtime/hooks.py` are now batched through `batch_record_stats()` to reduce lock churn and make the batch API actually used in production.
- **P3-2 async write fallback failure propagation**: `core/store.py::async_write_memory()` now raises `RuntimeError` when the synchronous fallback fails, instead of silently logging a warning.
- **P3-3 frontmatter version preservation**: `MemoryFrontmatter.version` is now serialized by `serialize_frontmatter()` and `_frontmatter_to_data()`, surviving disk round-trips.
- **P3-4 MockStore scope parity**: `tests/_helpers.py::MockStore.list()` now filters through `core.scope.filter_memories_by_scope()` so tests mirror production scope semantics.
- **P3-5 scope_split fact invalidation**: `_record_semantic_relation_sidecar()` now invalidates target-memory typed facts for `scope_split` as well as `merge`.
- **P3-6 pending-skills archive retention**: old `pending-skills.<timestamp>.json` archives are pruned after 30 days or when more than 10 archives accumulate.
- **P3-7 raw_chunk per-session cap**: `_run_raw_chunk_reflection()` now limits raw-chunk episode memories to 20 per session (configurable via `plugin_config().reflection.max_raw_chunk_per_session`).
- **P3-8 atomic cold-store prune**: `memory/curator/cold_store.py::_prune_cold_store()` rewrites the cold store atomically via temp-file + `os.replace()`.
- **P3-9 dashboard update 404**: `web/api.py::update_memory()` now returns HTTP 404 instead of 500 for non-existent memory IDs.
- **P3-10 capped auto-association**: `web/api.py::create_memory()` caps graph auto-association to the 20 most recent tag-overlapping memories.
- **Regression coverage**: 9 new tests; full-suite baseline moves to 656 passed.

### Round-5 Schema / Graph / Curator Hardening (2026-06-26)

Triggered by a systematic-debugging review of the latest v1.7 beta fixes. All items below are confirmed by new regression tests; full-suite baseline is **686 passed**.

- **R5-1 graph feature registration wired**: `runtime/registration.py::register()` now calls `runtime/graph.py::register_graph_features()` so the `/graph` slash command, graph lifecycle hook, and hook-side graph manager getter are actually registered. `register_graph_features()` uses a context-idempotency guard to avoid duplicate registration.
- **R5-2 `srh_graph_retrieve` parameter compatibility**: the handler now accepts both `memory_ids` (canonical) and the deprecated `seed_ids`; passing `seed_ids` alone logs a deprecation warning. The schema uses `anyOf` to keep old clients valid.
- **R5-3 `MergeSimilar` scan cap restored**: `_scan_for_similar()` now sorts and slices active memories to the intended 500 most-recent before grouping by scope, fixing the O(n²) regression introduced in v1.6.
- **R5-4 stats compaction concurrency safety**: `memory/curator/helpers.py::compact_stats_snapshot()` holds `_stat_write_lock` from snapshot load through stream truncate, preventing event loss from concurrent `record_memory_stat()` writes.
- **R5-5 SQLite stats dead path deprecated**: `core/store_methods.py::effectiveness()` and `record_stat()` now forward to the JSONL truth path (`store.effectiveness()` / `record_memory_stat()`) and emit `DeprecationWarning`; the SQLite `stats` table DDL is kept only for existing databases.
- **R5-6 `srh_memory_delete` return shape unified**: single-ID and filter-batch paths now both return `{success, id, deleted_count}`.
- **R5-7 orphan-edge cleanup fail-safe**: `GraphIndex.clean_orphan_edges()` treats an empty `valid_ids` set as a caller error and returns 0 instead of wiping the whole graph.
- **R5-8 graph scope boundary filtering**: `srh_graph_retrieve` and `srh_graph_viz` accept optional `filters`; graph traversal is restricted to nodes in the active scope (`allowed_nodes`) so Hebbian retrieval does not leak across tenants. `_GraphStoreShim.spread_activation()` / `propagate_activation()` and `GraphManagerCompat.retrieve_related()` / `propagate_activation()` propagate `allowed_nodes`.
- **R5-9 semantic sidecar partial-failure resilience**: `_record_semantic_relation_sidecar()` in `reflection/runtime.py` now tries every target individually, collects failures, and logs warnings instead of silently stopping on the first error.
- **R5-10 reflection excludes current session IDs in LLM/micro paths**: `_run_full_reflection()` and `_run_micro_reflection()` now pass `current_session_ids` as `exclude_ids` to novelty/conflict checks in the LLM branch, matching the embedding path.
- **R5-11 `ReflectionEngine` scope-aware**: `ReflectionEngine.__init__` accepts optional `scope_filters` and stamps `user_id`/`agent_id`/`run_id` onto newly created memories in `micro()`/`full()`.
- **R5-12 `srh_reflect_now` response normalization**: `_tool_srh_reflect_now()` post-processes `_run_full_reflection()` results so the returned JSON always contains `{mode, summary, accepted_memories, skill_candidates, conflicts, chunks_created, error}`, regardless of reflection mode.
- **R5-13 palace read-zone return shape unified**: `_tool_srh_palace_read_zone` now returns a single schema `{zone, source, count, memories, content, message}` across all branches.
- **R5-14 ArchiveStale age fallback**: when a memory has no effectiveness or `last_access` record, `ArchiveStale` now falls back to `frontmatter.created` and then file mtime for staleness decisions.
- **R5-15 curator recovery journal**: `memory/curator/__init__.py::_run_curator()` writes a `curator_recovery.jsonl` with mutation entries and supports a `curator.stop_on_error` config flag to halt the pipeline on first failure.
- **R5-16 effectiveness snapshot GC**: `compact_stats_snapshot()` removes snapshot rows for memory IDs that no longer exist in the store.
- **R5-17 `_scope_filters_from_kwargs` deprecation guard**: legacy bare `user_id`/`agent_id`/`run_id` kwargs to lifecycle hooks still work but now emit a `DeprecationWarning`; explicit `scope_filters` dict is preferred.
- **R5-18 memory-event truncation auditability**: `core/store.py::_event_json()` now records an `_original_frontmatter_hash` when frontmatter is truncated to >8KB, preserving a verifiable link to the original event payload.
- **R5-19 spread_activation / decay counter decoupled**: `GraphIndex.spread()` gains `increment_step=False` and `allowed_nodes` parameters; search-time Hebbian boost calls it with `increment_step=False` so retrieval no longer advances the decay step counter.
- **Regression coverage**: ~30 new tests across `test_schema_module.py`, `test_runtime_graph_aliases.py`, `test_curator_pipeline.py`, `test_effectiveness_snapshot.py`, `test_graph.py`, `test_reflection.py`, `test_semantic_supersedes.py`, and `test_memory_events.py`; full-suite baseline moves to 686 passed.

### Round-4 Slash-Command / Graph / Curator / Stats Fixes (`v17`)

Triggered by: plugin-registered slash commands (e.g. `/reflect`) not executing at runtime. Root cause was a missing `import os` (fixed upstream in `49dccbd`) that aborted module load, masking a chain of latent defects surfaced once the module loaded. All five below are confirmed by importlib.util host-loader reproduction; see `docs/review/v1.7-slashcmd-graph-curator-audit.md`.

- **R4-1 `/skills <query>` import**: `runtime/hooks.py::_slash_skills` did `from . import match_skills`, but `runtime/__init__.py` does not export it. Fixed to `from .. import match_skills` (the form `runtime/tools.py` already used correctly). The no-arg branch worked; only `/skills <query>` crashed with `ImportError`.
- **R4-2 graph manager singleton**: `runtime/state.py::_get_graph_mgr` imported `_get_graph_mgr` from `runtime.graph`, which defines no such name (only `get_graph_manager_compat`). This raised `ImportError` on first call, which `registration.py` swallowed in `try/except`, so `ms.set_graph(gm)` never ran and graph boost was silently disabled. Fixed to call `get_graph_manager_compat()`.
- **R4-3 `runtime.store` misreference**: `runtime/graph.py` imported `plugin_data_dir` from `.store`, but `runtime/` has no `store.py`. Fixed to `from ..core.store import plugin_data_dir` (two sites). Was masked by R4-2 until that was fixed.
- **R4-4 curator effectiveness API**: `memory/curator/helpers.py::_load_effectiveness` called `mem_store.list_active_effectiveness()`, a method that does not exist on `MemoryStore` (real API: `effectiveness(memory_id)`). The `AttributeError` was swallowed, so effectiveness-based archival was dead. Fixed to use `effectiveness(memory_id)`.
- **R4-5 dataclass-as-dict access + combined score**: `scan_for_stale` / `ArchiveStale` / `MergeSimilar` called `.get("effectiveness")` / `.get("last_accessed")` on a `MemoryEffectiveness` dataclass, which has no `.get()`. Fixed to use `eff.factor() * eff.decay_factor()` (combined hit-rate × time-decay score). Default `effectiveness_threshold` raised `0.1` -> `0.2` because the combined floor is `0.5 * 0.3 = 0.15` (the old threshold never triggered).
- **Stats persistence unification (JSONL truth path)**: discovered while fixing R4-4 — production writes stats to `memory-stats.jsonl` (`record_memory_stat`), but `MemoryStore.effectiveness()` read the SQLite `stats` table, which was never populated (the only writer, `record_stat()`, had zero production callers). Unified on JSONL: `effectiveness()` now reads via `load_effectiveness()` with a 30s TTL cache; `record_stat()` marked `[DEPRECATED]`.
- **Dual-track stats compaction**: `memory-stats.jsonl` was append-only with no compaction (~15 lines/query, unbounded). Added `effectiveness-index.jsonl` (one row per memory) as an aggregate snapshot; `load_effectiveness()` reads snapshot baseline + event-stream tail (`at > folded_at`); `compact_stats_snapshot()` (curator session_end, threshold 5000 lines) folds the stream into the snapshot and truncates it via atomic `_safe_write`.
- **Test isolation**: added `tests/conftest.py` autouse fixtures resetting graph singletons + isolating the graph manager for curator tests (monkeypatch `get_graph_manager_compat` to a temp-db instance). Fixed the pre-existing `test_memory_store_save_duplicate` to match the real `put()` / id-dedup contract (it called a non-existent `.save()` and asserted body-based dedup the store never performed).
- **`debug_curator_bug.py` rewrite**: the diagnostic script had a broken import and documented the now-fixed R4-4/R4-5 bugs. Rewritten as a 10-check regression script (`python debug_curator_bug.py`, exit 0 on success).
- **Regression coverage**: 8 new tests in `tests/test_effectiveness_snapshot.py` (suite 638 -> 647); full audit + systematic-debugging skill gap analysis in `docs/review/v1.7-slashcmd-graph-curator-audit.md`.

### Development Notes

- Scope propagation, refined extraction, semantic supersedes, typed fact sidecar, compaction quality, and release-hardening coverage are now all represented in code and tests.
- Remaining future work is deeper relation-aware recall and more advanced fact-level temporal querying, not a blocker for the v1.7 scope/quality release line.

## v1.6 — Memory Event Ledger & Scoped Filters

### Memory Event Ledger (Wave 1)

- **SQLite `memory_events` table**: Tracks ADD/UPDATE/DELETE/SUPERSEDE/PIN/UNPIN events with old/new body, old/new frontmatter, session_id, and actor_id.
- **`_record_memory_event()`**: Safe JSON serialization with datetime handling and 8KB frontmatter truncation to prevent oversized rows.
- **`get_memory_events()`**: Query events filtered by `event_types`, `session_id`, and `limit`.
- **`get_memory_history()`**: Combines supersedes chain + optional event timeline for full memory provenance.
- **Transaction awareness**: Events are written atomically within the same SQLite connection as the memory mutation. Rollback-safe: if the outer transaction is rolled back, neither the memory nor its events persist.
- **Tool integration**: `srh_memory_history(..., include_events=True, event_types=[...], session_id=...)` exposes audit trail to agents.

### Scoped Filters (Wave 2)

- **Three scope columns**: `user_id`, `agent_id`, `run_id` on the `memories` table. NULL = universally visible.
- **AND logic for combined filters**: `{"user_id": "u1", "agent_id": "a1"}` matches only memories where BOTH conditions hold.
- **`filters` parameter** on `MemoryStore.list()`, `SearchIndex.search()`, `SearchIndex.search_explain()`, and `MemoryStore.delete_by_filters()`.
- **NULL matching**: `filters={"user_id": None}` uses `IS NULL` to find universally-visible memories.
- **Unknown key rejection**: `list()` raises `ValueError` for unknown filter keys (e.g. `user_od` typo).
- **Tool schema updates**:
  - `srh_memory_write` accepts `user_id`, `agent_id`, `run_id`
  - `srh_memory_search` accepts `filters`
  - `srh_memory_delete` accepts `filters` for batch delete (`id` is optional when `filters` is provided)
  - `srh_memory_history` accepts `include_events`, `event_types`, `session_id`
- **Backward compatibility**: v1.5 memories without scope fields remain discoverable (columns default to NULL).

### Code Review Fixes (v1.6)

- **Schema unification**: `runtime/registration.py` imports all 13 schemas from `runtime/schemas.py` (was inline in `runtime/tools.py::register()`).
- **Search response scope fields**: `srh_memory_search` results now include `user_id`/`agent_id`/`run_id` when non-None.
- **History frontmatter deserialization**: `srh_memory_history` parses `old_frontmatter`/`new_frontmatter` JSON strings into objects.
- **Event frontmatter truncation warning**: Logs `logger.warning` when event frontmatter exceeds 8KB and is truncated.
- **Scope clause helper**: Extracted `_build_scope_clauses()` to unify `list()` and `delete_by_filters()` logic.
- **Empty string normalization**: `MemoryFrontmatter.to_dict()` treats empty-string scope values as `None`.
- **Explicit column select**: `delete_by_filters()` uses explicit column list instead of `SELECT *`.

### Infrastructure

- **Test count**: 523 → 553 tests (30 new tests across event ledger + scoped filters)
- **Tool count**: 12 → 13 (`srh_memory_history` registered through canonical path)
- **Zero regressions**: All v1.5 features preserved unchanged

---

## v1.5 — Module Refactoring & Composable Curator Pipeline

### Core Module Split (v1.5)

- **`core/store.py` → 10 modules**: Extracted `core/models.py`, `core/utils.py`, `core/tokenization.py`, `core/entities.py`, `core/store_methods.py`, `core/skill_store.py`, `core/lineage.py`, `core/intent.py`, `core/async_writer.py`, `core/store_health.py`
- **Layered imports preserved**: No circular dependencies; `core/store.py` remains the leaf module

### Curator Refactor (v1.5)

- **Composable action pipeline**: `memory/curator/` subpackage replaces monolithic `memory/curator.py`
  - `actions.py`: `CuratorAction` base + 6 implementations (`ArchiveStale`, `CompactChains`, `ArchiveSuperseded`, `MergeSimilar`, `CleanOrphanEdges`, `GenerateReport`)
  - `helpers.py`: `is_protected`, `build_cold_entry`, `archive_and_delete`, `load_last_access`, config
  - `cold_store.py`: JSONL append-only cold storage with 10MB cap
  - `report.py`: Human-readable report generation and persistence
- **Pipeline ordering**: ArchiveStale → CompactChains → ArchiveSuperseded → MergeSimilar → CleanOrphanEdges → GenerateReport
- **Legacy API preserved**: Thin wrappers in `memory/curator/__init__.py` for backward compatibility

### Runtime Package Split (v1.5)

- **`runtime/registration.py`**: `register(ctx)` entrypoint that wires hooks, commands, tools, and post-delete callbacks
- **`runtime/schemas.py`**: 12 Hermes tool JSON schemas
- **`runtime/state.py`**: Singleton getters (`_get_mem_store`, `_get_search_index`, `_get_graph_mgr`, etc.) with double-checked locking
- **`runtime/helpers.py`**: `_build_context_block`, `_build_context_bundle`, `_estimate_tokens`, `match_skills`
- **`runtime/_lb.py`**: Late-binding helper — resolves modules/symbols without hard imports to avoid circular dependencies
- **`__init__.py` slimmed**: Exports public API + backward-compat aliases only; registration delegated to `runtime/registration`

### Infrastructure

- **Test count**: 413 → 523 tests (110 new tests across module-split boundaries)
- **Late-binding pattern**: Runtime modules use `_lb(name)` for cross-module resolution at call time rather than import time
- **No functional regressions**: All v1.4 features (ContextBundle, checkpoint, entity index, explainable search, CJK tokenizer) preserved unchanged

---

## v1.4-beta — Context Reliability & Entity Recall

### Context Reliability (stable/dynamic split)

- **`ContextBundle`**: Internal structured context with stable/dynamic section separation. Stable section (pinned + always-active skills) preserves prompt cache; dynamic section (relevant memories + triggered skills + episode summaries) varies per turn.
- **Timeout-protected context assembly**: `pre_llm_call` hook has 8s timeout with stable-only fallback on timeout/failure.
- **Graded compression**: `none/mild/aggressive/emergency` levels applied to dynamic section under token pressure; stable section always preserved.
- **Backward compatible**: Public `build_context()` still returns a single string; bundle helpers exported via package facades.

### Retrieval Quality & Explainability

- **Configurable CJK tokenizer**: `auto` (default), `bigram`, `jieba` modes. `jieba.cut_for_search` with fail-open fallback to bigram.
- **Explainable search**: `search_explain()` returns structured score breakdown per hit (BM25, embedding, recency, effectiveness, supersedes, entity, Hebbian signals).
- **Opt-in tool flag**: `srh_memory_search(..., explain=true)` returns diagnostic output.

### Runtime Reliability

- **Session checkpoint**: `runtime/checkpoint.py` with atomic JSON persistence, corrupt backup, and pending-stage recovery.
- **Session-end recovery**: Hooks mark reflection/curator/compaction stages as `pending` on failure; next session-start recovers them.
- **Typed config diagnostics**: `core/config.py` validates types, warns on unknown keys, falls back to safe defaults.

### Entity Recall & Backend Readiness

- **SQLite entity index**: `entities` and `entity_links` tables with lifecycle hooks on write, delete, and rebuild.
- **Entity extraction pipeline**: Regex-first + optional spaCy architecture. Six regex patterns:
  - `file_path` (weight 1.0): filesystem paths like `src/providers/http/index.test.ts`
  - `code` (0.9): backtick-quoted identifiers like `` `ToolRunner.execute` ``
  - `quoted` (0.8): single/double-quoted strings like `"config.yaml"`
  - `package` (0.75): dot-separated identifiers like `numpy.linalg.norm`
  - `proper` (0.7): PascalCase/camelCase like `HttpRequestHandler`
  - `compound` (0.65): hyphen/slash compounds like `auth-middleware`
  - Optional spaCy NER (0.6) when `en_core_web_sm` is available
- **Entity boost in search**: Mentioned entities receive recall boost proportional to extraction weight; hits appear in explain output.
- **Cross-reference**: mem0 uses spaCy-only NER; SRH's regex-first approach provides finer weight granularity and no mandatory heavy dependency.
- **Backend capability abstraction**: `core/backend.py` exposes SQLite capabilities without changing default runtime.

### Tests

- 317+ tests collected; new v1.4 tests grouped under pytest markers: `v14_context`, `v14_retrieval`, `v14_runtime`, `v14_entity`, `v14_contract`.

## v1.3-beta — Curator Enhancement + Graph Cleanup

- Orphan edge cleanup (delete + curator sweep)
- Compaction before archive in curator pipeline
- `total_archived` includes compacted entries
- Pre-existing test regressions fixed

---

## v1.2-beta2 — Optional Reranker Layer (mem0 pattern)

### Optional Reranker Layer

New `core/reranker.py` module (~150 LOC) providing pluggable second-stage reranking after the primary retrieval pipeline:

- **Providers**: `cross_encoder` (local, default) and `cohere` (API-based).
- **Integration**: Inserted after Hebbian boost and before MMR in `SearchIndex.search()`.
- **Lazy loading**: Models/clients initialized on first `rerank()` call.
- **Graceful fallback**: On any failure returns original order with `logger.warning`.
- **Config**: `reranker.*` under plugin config — disabled by default (no config = no reranker).
- **Tests**: 13 unit tests covering interface, factory, lazy load, and integration. 13/13 pass.
- **Zero regression**: Full test suite 294/294 pass.

### Ported from mem0

Design pattern borrowed from mem0's `reranker/` package (base ABC + factory + provider implementations). Adapted for SRH's config style (YAML dict vs Pydantic) and candidate shape (`.body` attribute vs `{"memory": ...}` dict).

---

## v1.2-beta — Memory Curator + v0.16.0 Telemetry Hooks

### Memory Curator Module

New `memory_curator.py` module (~450 LOC) with 4-phase curation pipeline:

- **Phase 1 — TTL + Staleness** (`scan_for_stale`/`archive_expired`): Automatically detects expired (`valid_until` past) and stale (>90 days no access) memories. Moves them to cold storage JSONL. Exempts pinned/permanent entries.
- **Phase 2 — Supersedes Archiving** (`archive_superseded`): Deep supersedes chains (depth >= 2) with no recent access are archived as a single cold-storage entry preserving chain lineage.
- **Phase 3 — Similarity Detection** (`scan_for_similar`): BM25 token-overlap pair scoring (O(n²) clamped to 500 most-recent). Flags candidates above 0.6 threshold for optional LLM merge.
- **Phase 4 — Cold Storage Engine** (JSONL read/write): Configurable 10MB cap with oldest-entry pruning. Supports restore via `_restore_from_cold()`.
- **Integration**: Called from `runtime_hooks._on_session_end()` after episode compaction. Fail-open: exceptions caught and logged.
- **Config**: `curator.*` under plugin config — enabled by default, runs on session end.
- **Tests**: 15 unit tests covering all phases + full pipeline. 15/15 pass.

### Body Refinement (v1.2)

- **`_refine_body()`**: Strips fenced code blocks, `[Tool:xxx]` markers, tool-result prefixes, and collapses excess whitespace. Applied in both bridge write path and cold-storage archive to keep memory bodies clean.

### v0.16.0 Enhanced Hooks

## v1.1 — Hermes Agent v0.16.0 Telemetry Hooks + Root-Cause Fixes

### v0.16.0 Enhanced Hooks

- **`subagent_start` hook**: New `_on_subagent_start` handler tracks concurrent
  subagent count and records start timestamps for lifecycle tracking.
- **Enhanced `_post_tool_call`**: Now consumes v0.16.0 kwargs:
  - `status`: Skips graph enrichment on tool errors; bypasses Dir A bridge on
    `memory` tool failures.
  - `duration_ms`: Logs slow tool calls (>10s) with `turn_id` for diagnostics.
  - `turn_id`/`session_id`/`tool_call_id`: Used in diagnostic logging for
    per-turn traceability.
- **Zero-cost gate**: All new hooks/hook kwargs are gated by `has_hook()` on
  the host side — no overhead when no plugin subscribes.

### Root-Cause Fixes

- **P1-1 (context.py)**: Removed `_build_builtin_memory_block()` entirely.
  Root cause: Dir A startup sync was missing, requiring context builder to
  re-read MEMORY.md on every pre_llm_call. Fix: `sync_builtin_to_plugin()`
  runs once at plugin registration, then Dir A handles incremental sync.
- **P1-2 (runtime_tools.py)**: Removed Dir B delete-from-MEMORY.md logic.
  Root cause: Dir B delete was using body-text matching against MEMORY.md,
  risking false matches. Fix: Dir B is now purely APPEND-ONLY — MEMORY.md
  is a frozen snapshot, not a plugin store mirror.

### Housekeeping

- **Episode Compaction** (v1.1): `_compact_episode_zone()` clusters raw episode entries into daily summaries via LLM, running automatically after `on_session_end` reflection.
- **Dead code cleanup**: Removed `QueryTemplate`/`ResultCache` (~170 lines) from `search.py`, `_classify_intent` embedding prototypes, `_embed_texts`, `_find_plugin_entry_by_content`, `_BUILTIN_MEMORY_DIR_MTIME`, duplicate `_hermes_home`/`_read_builtin_entries` from `context.py`, and legacy singleton functions from `__init__.py`. Net reduction: 625 lines across 9 files.
- **Hermes Agent v1.1 compat fixes**: `PluginLlmStructuredResult.error` → `.content_type`/`.parsed` check; empty `input=[]` in `complete_structured` → text block; removed stale `test_query_cache.py`.
- 257/281 tests pass (24 Windows temp dir permission errors, non-code).

## v1.0-beta3 — Beta3 Cleanup + Runtime Contract Review

Beta3 retires the remaining pre-beta2 implementation files while preserving the beta2 runtime feature surface: 17 tools, 4 hooks, 8 slash commands, dashboard routes, runtime graph, search, store, and reflection flows.

### Cleanup
- Removed retired pre-beta2 implementations: `core.py`, `late_binding.py`, `search/embed.py`, `query/cache.py`, `ahe_graph/`, `graph/ahe_graph.py`, `graph/cluqi.py`, `graph/pagerank.py`, and `graph/cross_zone.py`.
- Promoted canonical runtime modules: `runtime_tools.py`, `runtime_hooks.py`, `runtime_graph.py`, and `runtime_reflection.py`.
- Reduced package-root wildcard exports and routed host registration through explicit runtime modules.
- Kept only four deprecated explicit old-path compatibility entrypoints: `tools/handlers.py`, `hooks/lifecycle.py`, `graph/compat.py`, and `reflection/engine.py`.

### Review Fixes
- Restored `MemoryStore` derived-view invalidation for palace index and search cache after `put`, `update`, `delete`, and `reorder`.
- Hardened `GraphIndex._get_conn()` so a closed thread-local SQLite connection is detected and recreated.
- Fixed dashboard graph cleanup to avoid closing the shared runtime graph connection after memory deletion.
- Moved `SearchIndex.search(zone=...)` filtering into the candidate pool before retrieval/rerank so zone-scoped searches are not starved by global top-k results.

### Validation
- `scripts/smoke_host_contract.py`: 37 passed, 0 failed.
- `scripts/check_v092.py`: 7 passed, 0 failed.
- `scripts/check_issues.py`: 0 issues.
- `python -m pytest tests -q`: 215 passed, 1 warning.

## v1.0-beta — R1 Code Review Fixes + Thread Safety + Robustness

First release candidate. 63 review findings addressed (1 CRITICAL, 30 HIGH, 16 MEDIUM, 9 LOW).

### CRITICAL (1)
- **C1**: Fixed `_embed_single` / `_cosine_sim` NameError — added missing re-exports from `search/embed.py` to `__init__.py`

### Thread Safety (6 fixes)
- `MemoryStore` public methods guarded with `RLock` (concurrent `put`/`delete`/`update`/`reorder`/`search`)
- `_session_messages` dict protected with `threading.Lock` in lifecycle hooks
- `_turns_since_reflect` counter protected with `threading.Lock` (micro-reflection cadence)
- `_reflect_log_lock` now acquired for both read and write paths (prevents torn reads)
- `ResultCache` singleton uses double-checked locking (`get_cache()`)
- `_classify_intent_stats` mutations synchronized via `_bump_classify_intent_stat`

### Logic Bug Fixes (5 fixes)
- `sup_factor` inversion corrected — current (most-revised) memories now score highest, not lowest
- `check_conflict` supersedes validation checks ALL targets, not just the first
- `_run_full_reflection` / `_run_micro_reflection` now register newly created memory IDs in session exclusion set (prevents feedback loop)
- `_pre_llm_call` micro-reflection cadence preserved when reflection is skipped (no false counter reset)
- `_pre_llm_call` fixed `UnboundLocalError` — added missing `global _turns_since_reflect`

### Error Handling (11 fixes)
- Silent failures upgraded from `logger.debug` to `logger.warning` in: async write flush, `record_memory_stat`, `batch_record_stats`, `load_effectiveness`, `fusion_search` embedding fallback, `build_palace_index`, `close()` checkpoint
- `_repair_truncated_json` escape-sequence bypass fixed — backward scan now tracks unescaped quotes
- `load_effectiveness` logs corruption details before returning empty dict

### Performance (3 fixes)
- `_build_adjacency` cache key now includes `min_weight` (prevents stale adjacency results)
- `_build_adjacency` mtime check + DB query + cache update all inside `self._lock` (fixes stale cache race)
- `check_conflict` tokenizes query once and reuses for both threshold check and BM25 search
- `_bm25_search_scored` accepts optional `query_tokens` parameter to avoid re-tokenization

### Graph Module Cleanup
- `decay_all` uses batch processing (100 rows at a time) with per-row error handling
- PageRank uses single `get_all_edges()` query instead of N+1 `get_neighbors()` calls
- `get_top_pagerank` uses `get_all_nodes()` with zone info instead of N+1 `get_meta()` calls
- `_lineage_status` caches supersedes mapping at query time (O(n*k) → O(n))
- `GraphStoreProtocol` now inherits from `typing.Protocol`
- `RetrievalRouter` accepts `GraphStoreProtocol` instead of concrete `GraphStore`
- `add_supersedes_edge` is now a true no-op (deprecated; lineage uses frontmatter)
- `_query_graph_layer` removes dead dict-style activation code
- Dead code in `cluqi.py` dict/list branching removed

### Cache & Search Improvements
- `ResultCache` eviction uses LRU (least recently accessed) instead of earliest expiry
- `_set_cached_embed` delegates to proper LRU `_put_cached_embed`
- `_intent_prototypes` logs configuration errors instead of silently discarding
- `invalidate_pattern` logs warning and returns 0 (previously silent no-op)
- `SkillStore` cached per-request in dashboard `/graph` endpoint

### Infrastructure
- `late_binding.py` — shared module with `invalidate_late_bindings()` for cache invalidation
- `_sanitize_filename` blocks path separators
- Reflect log archives auto-deleted after 30 days
- Dashboard `delete_memory` uses `try/finally` with `conn.close()` (fixes SQLite connection leak)
- Dashboard `create_memory` reuses parsed JSON (no double `json.loads`)
- `update()` populates `_id_to_mem` with new memory before cache invalidation
- File naming uses `fm.created.strftime('%Y-%m-%d')` instead of fragile `str()[:10]` slicing
- `reorder()` uses `_write_memory` + `os.replace` (consistent with other write paths)

### Test Coverage
- 117 tests passing (was 105), including new regression tests for:
  - Full reflection session-ID exclusion
  - Micro-reflection cadence preservation on skip
  - Supersedes cycle detection for multi-target
  - Embedding reflection supersedes exclusion

## v0.9.2-beta2 — Reflection Audit + Supersedes Governance + Lineage Recall

- **WS-1 Supersedes governance**: `supersedes_reason` frontmatter field, lineage helpers (`latest_for`, `is_superseded`, `lineage_chain`), cycle detection, missing-target validation
- **WS-2 Lineage-aware recall**: Default search excludes superseded memories; `include_history` flag; CLUQI boosts latest active memory in chain
- **WS-3 Reflection quality audit**: Structured `audit_entries` in reflect log with `candidate_id`, `decision`, `decision_reason`, `novelty_score`, `conflict_id`, `supersedes_ids`, `supersedes_reason`, `assigned_zone`, `graph_migration`. Dashboard `/reflections/audit` endpoint for querying audit entries.
- **WS-6 Temporal/context hints**: `valid_from`, `valid_until`, `context_scope`, `supersedes_reason` frontmatter fields with round-trip preservation and expired-memory detection
- **Beta2 plan**: Added `docs/design/PLAN_0_9_2_BETA2.md` to turn review gaps into prioritized workstreams

### Codex Review Fix Rounds (Round 1–4)

- **Round 1** (`467629f`): Late bindings resolve from root package (`mem_reflection_hermes`) instead of child subpackage (`mem_reflection_hermes.tools` / `.hooks`)
- **Round 2** (`78e3466`/`5cab32c`): Restore `_get_mem_store`/`_get_skill_store` after `reflection.engine` star import; CLUQI fallback applies `zone`/`tags`/`min_confidence`/`include_superseded` filters; `search/embed.py` fixes relative import for custom `intent_prototypes`
- **Round 3** (`cf7c6e0`/`15dbf62`/`30d805a`): Dependency-free YAML frontmatter fallback parser; standalone dashboard import via `importlib.util`; `fusion_search` supports `include_history`; CLUQI correctly uses `is_superseded()` instead of `frontmatter.supersedes`
- **Round 4** (`7c21c91`/`c6388ef`/`da12b39`/`4ed9f35`/`8255dcf`/`c7dad3e`/`94a3045`/`655cdf2`/`0796ec8`/`3ea5dbc`): `sys.modules` registration before `exec_module`; `record_memory_stat` import in lifecycle; transcript truncation preserves recent turns; conflict check accepts `exclude_ids` for supersede targets; `include_history` exposed in search schema; `_read_memory` alias restored for cold delete; beta2 metadata preserved during rebalance; `supersedes_reason` exposed in write schema; SUPERSEDES edge filter fixed in dashboard graph endpoint

## v0.9.2-beta — Dashboard Graph Integration + CLUQI + PageRank

- **Design evaluation**: Added `docs/review/DESIGN_EVALUATION.md` evaluating supersedes chains, associative graph memory, reflection refinement, and recall against Mem0, Letta/MemGPT, Zep/Graphiti, Memary, and Cognee.
- **CLUQI** (`cluqi.py`): Cross-Layer Unified Query Interface joining MemoryStore, GraphStore, and supersedes chains
- **PageRank** (`pagerank.py`): Centrality scores for identifying hub memories in the graph
- **Query templates** (`query_cache.py`): 8 predefined patterns + TTL-based result cache
- **Cross-zone analysis** (`cross_zone.py`): Bridge memories, zone centrality, zone recommendations
- **SUPERSEDES graph edges**: Structural version lineage edges in ahe_graph (preserved from decay)
- **Dashboard v0.9.2**: 13 API routes
  - `GET /graph` returns real Hebbian edges from ahe_graph SQLite + PageRank scores
  - `GET /graph/neighbors/{id}` with CLUQI metadata enrichment
  - `GET /graph/zones` for cross-zone bridge visualization
  - `GET /query` for CLUQI unified search
  - Memory CRUD auto-syncs graph data
  - Frontend: node click highlighting, Zone Analysis tab, CLUQI Query tab, zone move dropdown

## v0.9.1 — Cross-Layer Query + Graph Analysis (2026-05-31)

- CLUQI cross-layer unified query
- PageRank centrality computation
- SUPERSEDES graph edge type
- Query templates and result cache
- Cross-zone graph analysis

## v0.8.0 — Module Split + ahe_graph Integration

- **6-way module split**: `__init__.py` reduced from ~4,500 to ~1,588 lines; logic moved to `core`, `embed`, `reflection`, `hooks`, `tools`
- **12 SRH tools**: `srh_memory_search/write/delete/history`, `srh_skill_search`, `srh_reflect_now`, `srh_palace_zones/read_zone/recall/search/rebalance`, `srh_compile_profile`
- **ahe_graph integration**: Graph memory layer surfaced through dashboard graph endpoints and CLUQI query
- **Backpressure queue**: Micro-reflection bounded queue (max 20) with overflow logging
- **CJK adaptive threshold**: Per-language similarity threshold (CJK 0.55, Latin 0.65)
- **LRU embed cache**: 128-entry LRU cache for embedding vectors
- **Zone rebalance**: Automatic zone split/merge with dry-run mode
- **Memory history**: `srh_memory_history` traces supersedes chains
- **Codex review fixes**: P0/P1/P2 issues resolved with audit script verification

## v0.7.0 — ahe_graph Deep Integration

- Graph memory layer with associate/retrieve/stats/viz tools
- `post_tool_call` hook builds tool result associations
- Intent classification via embedding similarity

## v0.6.1 — Atomic Store Refactor

- **Atomic `MemoryStore.update()`**: Single method handles file write, cache invalidation, and index rebuild. Prevents 5 classes of cache inconsistency bugs.
- **Atomic `MemoryStore.reorder()`**: Assigns explicit `rank` values instead of manipulating timestamps. Stable across filtering and sorting modes.
- **`rank` field**: New `rank: int = 0` in `MemoryFrontmatter`. Higher rank = earlier in default sort. Backward compatible.
- **Simplified HTTP layer**: `plugin_api.py` delegates all mutation logic to Store methods — HTTP layer only validates and translates errors.
- **Post-review fixes**: Plugin registration API compat (`__HERMES_PLUGINS__.register`), atomic write via `os.replace()`, reflections field name fix, scope validation, zone list refresh.

## v0.6.0 — Dashboard Memory Manager + Bug Fixes

- **Dashboard Memory Manager**: Full CRUD UI with search, zone filter, sort controls, and reorder
- **6 API endpoints**: Create, read, update, delete, reorder, zones
- **Bug fix**: Duplicate memory scanning when `cwd` is `~` (user root == project root)
- **Bug fix**: Python 3.11 `importlib.util` + `@dataclass` compatibility (full fallback chain)
- Dashboard version: 1.0.0 → 1.1.1; path: `/memory-graph` → `/memory-manager`

## v0.5.0 — Performance Optimization Release

- **P0-1**: Palace index write-on-change — skip disk write when content hasn't changed
- **P0-2**: O(1) delete via id→path reverse index — eliminates directory scan
- **P1-1**: Fast byte-based token estimation — 5000x faster than char-by-char CJK check
- **P1-2**: Async stat flush — background thread for JSONL recording
- **P2-1**: Event-driven palace index rebuild — only rebuild on memory mutation
- **P2-2**: Async memory write — background thread for file I/O
- **SkillStore lazy cache**: One-time file read per session
- **build_palace_index cache**: Cached result string on MemoryStore
- Fix: `build_palace_index` sort key type error (int vs datetime)

## v0.4.0 — Palace Mode + Profile Compilation

- **Memory Palace**: Zone-based organization (core, work, episode, general, project:*)
- **Palace navigation tools**: `srh_palace_zones`, `srh_palace_read_zone`, `srh_palace_recall`, `srh_palace_search`
- **Profile compilation**: LLM-driven compilation of memories into structured profile documents
- **Always-active skills**: User-configured skills always injected into context
- **Supersedes chains**: Memory versioning with explicit replacement tracking

## v0.3.0 — Effectiveness Tracking + BM25

- **Per-memory effectiveness scoring**: Track how often memories are triggered
- **Exponential time decay**: `score * 0.5^(days/30)`
- **BM25 search**: Replaces TF-IDF with Okapi BM25 for better relevance
- **Effectiveness boosting**: Search results weighted by effectiveness score

## v0.2.0 — Embedding + Semantic Search

- **ONNX Runtime integration**: 16x faster than PyTorch for embeddings
- **Semantic search**: Cosine similarity over embedding vectors
- **Hybrid search**: Token overlap + embedding similarity combined
- **Lazy model loading**: ONNX model loaded on first use

## v0.1.0 — Initial Port

- Ported from `coder-brzhang/small-rust-hermes`
- TF-IDF search with zero dependencies
- Dual scope (user + project)
- Micro/full reflection pipelines
- Skill auto-matching via token overlap
