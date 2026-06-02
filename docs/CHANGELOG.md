# Changelog

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
