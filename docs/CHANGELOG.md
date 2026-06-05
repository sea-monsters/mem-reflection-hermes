# Changelog

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
