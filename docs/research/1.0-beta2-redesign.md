# mem-reflection-hermes v1.0-beta2 — Clean-Sheet Redesign

> **Goal:** Preserve 100% of the functional surface (17 SRH tools, dashboard, reflection, graph memory, palace navigation) while cutting ~60% of the code and eliminating architectural debt.
>
> **Current:** ~8,000 lines across 13 modules + dashboard.
> **Target:** ~3,200 lines across 6 modules + dashboard.

---

## 1. Executive Summary

The v1.0-beta codebase works, but it carries the weight of iterative growth: async write queues that are never saturated, a 4-layer graph abstraction over a single SQLite table, three context-injection modes when one suffices, and `late_binding.py` existing solely to paper over a circular-import problem in `__init__.py`.

The beta2 redesign treats **SQLite as the primary index** and **Markdown files as human-readable cold storage**. All runtime queries (BM25, graph traversal, stats, effectiveness) go through SQLite. Markdown files are written atomically for user editability, but the runtime never reads them directly after initial load. This single change removes the need for:

- `_id_to_path`, `_id_to_mem`, `_doc_tokens`, `_cache_valid`, `_index_dirty` caches
- `memory-stats.jsonl` (merged into SQLite)
- `late_binding.py` (circular imports disappear when `__init__.py` stops being 1,870 lines)
- The async write queue and generation-token machinery

---

## 2. Current Architecture — Pain Point Analysis

### 2.1 File I/O Over-Engineering (`core.py` lines 519–682)

**Current state:** A bounded async queue (`maxsize=500`), per-path RLock pool, write-generation tokens, and a daemon thread for background flushing.

**Reality:** At ~200 memories, even a naive `path.write_text()` takes <1ms. The queue is never saturated, the thread is pure overhead, and the generation-token system adds 150 lines of code that no one reasons about.

**Evidence:**
```python
# 164 lines for async machinery that handles <1 write/sec
_write_queue: queue.Queue = queue.Queue(maxsize=500)
_pending_writes: Set[Path] = set()
_write_guard_lock = threading.Lock()
_write_path_locks: Dict[str, threading.RLock] = {}
_write_generations: Dict[str, int] = {}
```

**Beta2 fix:** Synchronous `path.write_text()` with an `RLock` on the store. If the user wants durability, WAL mode on the SQLite index already provides it.

### 2.2 Graph Layer Abstraction Tax (`graph/ahe_graph.py` ~1,024 lines)

**Current state:** `GraphStoreProtocol` → `GraphStore` → `AssociationEngine` → `RetrievalRouter` → `GraphMemoryManager`.

**Reality:** The "engine" and "router" are thin wrappers that delegate 100% of their work back to `GraphStore`. `AssociationEngine.on_co_occurrence()` is a double nested loop calling `store.upsert_edge()` — it adds no logic that couldn't live in `GraphStore` itself. `RetrievalRouter` is a `dict` lookup followed by a method call.

**Evidence:**
```python
class AssociationEngine:
    def on_co_occurrence(self, memory_ids, context=""):
        for i, mid_i in enumerate(memory_ids):
            for mid_j in memory_ids[i+1:]:
                self.store.upsert_edge(mid_i, mid_j, "co_occurs", weight_delta=self.hebbian_lr)
                # ... 30 more lines of loop overhead
```

**Beta2 fix:** One `GraphIndex` class (~250 lines) that owns the SQLite connection and exposes `associate()`, `neighbors()`, `spread()`, `decay()`, `stats()`. No protocol, no engine, no router.

### 2.3 Circular Import Quagmire (`late_binding.py`, `__init__.py` lines 1,870)

**Current state:** `__init__.py` contains `MemoryStore`, `SkillStore`, context assembly, graph tool registration, and re-exports from all submodules. Submodules need these classes, so `late_binding.py` was invented to resolve symbols at runtime.

**Reality:** `late_binding.py` is a code smell. It turns static analysis off, breaks IDE jump-to-definition, and makes testing harder (import isolation gymnastics in `test_reflection.py`).

**Evidence:**
```python
# hooks/lifecycle.py
def _get_mem_store():
    return late_bind("_get_mem_store")()  # Runtime resolution

# __init__.py lines 1837–1850
# import * overwrites root-native versions, so we restore them manually
_get_mem_store = _package_get_mem_store
```

**Beta2 fix:** `__init__.py` is **only** the plugin registration entrypoint (~150 lines). All business logic lives in submodules with a strict DAG:

```
store.py      ← leaf (no project imports)
search.py     ← imports store.py
graph.py      ← imports store.py
reflect.py    ← imports store.py, search.py, graph.py
context.py    ← imports store.py, search.py, graph.py
api.py        ← imports all above
__init__.py   ← imports api.py, registers tools
```

### 2.4 Effectiveness Tracking in a Separate JSONL (`core.py` lines 684–716)

**Current state:** `memory-stats.jsonl` is appended to on every memory load. A separate `load_effectiveness()` function reads the entire file, parses JSON, and aggregates counters. `MemoryStore` caches this with mtime checks.

**Reality:** The graph SQLite already has `graph_memory_meta` with `access_count` and `last_access_at`. The JSONL is a second, redundant persistence layer.

**Beta2 fix:** Effectiveness events write directly to the SQLite `stats` table. Aggregated metrics are computed with a single `SELECT memory_id, COUNT(*) ... GROUP BY memory_id` query.

### 2.5 Embedding Index Hand-Rolled (`__init__.py` lines 1032–1088)

**Current state:** `self._embed_index = {"vectors": {}, "ids": []}` is maintained manually with `_try_index()`, `_try_remove_index()`, `_embed_search()` doing linear scans.

**Reality:** At 200 memories, a `numpy` array + `numpy.dot()` is simpler and 50× faster than a Python dict loop. No need for manual cache invalidation — the array is rebuilt lazily when the memory count changes.

**Beta2 fix:** `numpy.ndarray` of shape `(N, D)` stored in `SearchIndex`. Rebuilt on first embed query after mutation. `functools.lru_cache` on `_embed_single()` replaces the hand-rolled LRU.

### 2.6 Three Context Modes (`__init__.py` lines 1253–1363)

**Current state:** Palace mode, Profile mode, and Legacy mode. Each mode has separate code paths for pinned/active/skills injection, plus `palace_index.md` file caching.

**Reality:** Profile mode is off by default and rarely used. Legacy mode is superseded by Palace mode. The Palace index is a simple zone-grouped text summary that can be generated on demand in <1ms.

**Beta2 fix:** One mode — **Palace**. Context block = zone index + triggered skills + always-active skills. No file caching; generate on demand. The code path drops from ~110 lines to ~40 lines.

### 2.7 Unused or Marginal Abstractions

| Abstraction | Lines | Usage | Verdict |
|---|---|---|---|
| `ResultCache` (`query/cache.py`) | 213 | Never hit in hot path; dashboard bypasses it | **Delete** |
| `QueryTemplate` registry | 80 | 8 templates, never used programmatically | **Delete** |
| `GraphStoreProtocol` | 45 | Only one implementation | **Delete** |
| `_classify_update_intent()` | 30 | Never called from reflection; heuristic is unused | **Delete** |
| `RetrievalRouter` | 85 | Strategy lookup → delegate; adds no value | **Delete** |
| `_is_context_mismatch()` | 10 | `context_scope` field exists but filtering never applied | **Delete** |

---

## 3. New Architecture — "SQLite-Indexed File Store"

### 3.1 Core Principle

Markdown files remain the **source of truth** for content (human-editable, git-friendly), but the **SQLite index** is the source of truth for all runtime metadata, relationships, and search.

On startup: scan `.md` files once, populate SQLite. After that: all reads go to SQLite; `.md` files are only written for persistence.

### 3.2 Module Layout (6 modules)

```
mem_reflection_hermes/
├── __init__.py          # Plugin registration ONLY  (~150 lines)
├── store.py             # Unified MemoryStore + SQLite index  (~550 lines)
├── search.py            # BM25 + embed + fusion + conflict  (~450 lines)
├── graph.py             # SQLite graph + PageRank + cross-zone  (~350 lines)
├── reflect.py           # Micro + full reflection pipeline  (~500 lines)
├── context.py           # Context injection + skill matching  (~250 lines)
└── api.py               # FastAPI dashboard  (~350 lines)
```

**Total target: ~2,600 lines** (vs. current ~7,200 lines of Python, excluding dashboard JS).

### 3.3 Data Model (SQLite Schema)

```sql
-- Memories: metadata + pointer to .md file
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,           -- 'user' | 'project'
    zone TEXT NOT NULL DEFAULT 'general',
    confidence TEXT NOT NULL DEFAULT 'medium',
    pinned INTEGER NOT NULL DEFAULT 0,
    rank INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    valid_from TEXT,
    valid_until TEXT,
    body_hash TEXT NOT NULL,       -- for change detection
    tokens_json TEXT,              -- pre-tokenized body+tags
    embedding BLOB,                -- optional: serialized numpy bytes
    path TEXT NOT NULL             -- filesystem path
);

CREATE INDEX idx_mem_zone ON memories(zone);
CREATE INDEX idx_mem_pinned ON memories(pinned) WHERE pinned = 1;
CREATE INDEX idx_mem_created ON memories(created);

-- Tags: many-to-many
CREATE TABLE tags (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (memory_id, tag)
);
CREATE INDEX idx_tag_name ON tags(tag);

-- Supersedes: version lineage
CREATE TABLE supersedes (
    old_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    new_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    reason TEXT,
    PRIMARY KEY (old_id, new_id)
);
CREATE INDEX idx_sup_new ON supersedes(new_id);

-- Stats: effectiveness tracking (replaces memory-stats.jsonl)
CREATE TABLE stats (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    event TEXT NOT NULL,           -- 'loaded' | 'referenced' | 'accessed'
    at TEXT NOT NULL
);
CREATE INDEX idx_stats_mem ON stats(memory_id);
CREATE INDEX idx_stats_event ON stats(memory_id, event);

-- Graph edges: Hebbian co-occurrence
CREATE TABLE edges (
    source_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relation TEXT NOT NULL DEFAULT 'co_occurs',
    weight REAL NOT NULL DEFAULT 0.5,
    co_occurrence INTEGER NOT NULL DEFAULT 1,
    last_activated TEXT,
    PRIMARY KEY (source_id, target_id, relation)
);
CREATE INDEX idx_edges_source ON edges(source_id);
CREATE INDEX idx_edges_target ON edges(target_id);
CREATE INDEX idx_edges_weight ON edges(weight DESC);

-- Graph meta: per-memory graph state
CREATE TABLE graph_meta (
    memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_access TEXT,
    importance REAL NOT NULL DEFAULT 0.5,
    strength REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'active'
);
```

### 3.4 Module Interfaces

#### `store.py` — `MemoryStore`

```python
class MemoryStore:
    def __init__(self, user_root: Path, project_root: Path | None, db_path: Path)

    # CRUD
    def put(self, scope: str, fm: MemoryFrontmatter, body: str) -> Path
    def get(self, mem_id: str) -> LoadedMemory | None
    def delete(self, scope: str, mem_id: str) -> bool
    def update(self, mem_id: str, **fields) -> LoadedMemory
    def reorder(self, memory_ids: list[str]) -> list[str]

    # Listing (served from SQLite, no in-memory cache needed)
    def list(self, *, zone: str | None = None, active_only: bool = True,
             sort: str = "rank", limit: int | None = None) -> list[LoadedMemory]
    def list_pinned(self) -> list[LoadedMemory]
    def zone_counts(self) -> dict[str, int]

    # Lineage
    def latest_for(self, mem_id: str) -> LoadedMemory | None
    def lineage_chain(self, mem_id: str, max_depth: int = 10) -> list[LoadedMemory]
    def is_superseded(self, mem_id: str) -> bool

    # Stats
    def record_stat(self, memory_id: str, event: str) -> None
    def effectiveness(self, memory_id: str) -> MemoryEffectiveness
    def health_metrics(self) -> dict[str, Any]

    # Index maintenance
    def _sync_from_disk(self) -> None          # scan .md files, update SQLite
    def _write_md(self, path: Path, fm: MemoryFrontmatter, body: str) -> None
```

Key differences from v1.0-beta:
- No `_cache_valid`, `_id_to_path`, `_id_to_mem`, `_doc_tokens`. SQLite **is** the cache.
- No `async_write_memory()`. Synchronous write under `self._lock`.
- `list()` queries SQLite directly; no in-memory list rebuild.
- Effectiveness aggregated with `SELECT ... FROM stats GROUP BY memory_id`.

#### `search.py` — `SearchIndex`

```python
class SearchIndex:
    def __init__(self, store: MemoryStore)

    def bm25(self, query: str, k: int = 5, *, zone: str | None = None,
             active_only: bool = True) -> list[tuple[LoadedMemory, float]]

    def embed(self, query: str, k: int = 5) -> list[tuple[str, float]] | None
    def fusion(self, query: str, k: int = 5, *, zone: str | None = None,
               alpha: float = 0.5, beta: float = 0.3) -> list[LoadedMemory]
    def conflict(self, body: str, threshold: float | None = None,
                 exclude_ids: list[str] | None = None) -> tuple[str, float] | None
```

Key differences:
- BM25 reads `tokens_json` from SQLite instead of re-tokenizing on every query.
- Embedding uses `numpy.ndarray` (rebuilt lazily) instead of Python dict loop.
- `_embed_single()` uses `functools.lru_cache(maxsize=500)` instead of hand-rolled `OrderedDict`.
- No `ResultCache` layer; SQLite is fast enough.

#### `graph.py` — `GraphIndex`

```python
class GraphIndex:
    def __init__(self, db_path: Path)

    def ensure_meta(self, memory_id: str, zone: str = "general") -> None
    def associate(self, memory_ids: list[str], context: str = "") -> int
    def neighbors(self, memory_id: str, min_weight: float = 0.1,
                  limit: int = 20) -> list[dict]
    def spread(self, seed_ids: list[str], decay: float = 0.7,
               max_iter: int = 50) -> dict[str, float]
    def decay(self) -> None
    def stats(self) -> dict[str, Any]
    def pagerank(self, damping: float = 0.85) -> dict[str, float]
    def cross_zone(self, store: MemoryStore) -> dict[str, Any]
    def close(self) -> None
```

Key differences:
- One class replaces `GraphStore` + `AssociationEngine` + `RetrievalRouter` + `GraphMemoryManager`.
- No `Protocol` base class.
- `spread()` (fixed-point activation) is the primary API; BFS `propagate_activation()` is removed.
- PageRank and cross-zone analysis are methods on the same class, not separate modules.

#### `reflect.py` — Reflection Pipeline

```python
class ReflectionEngine:
    def __init__(self, store: MemoryStore, search: SearchIndex, graph: GraphIndex)

    def micro(self, ctx: Any, user_msg: str, assistant_msg: str) -> list[dict]
    def full(self, ctx: Any, messages: list[dict]) -> list[dict]
    def audit(self, candidate: dict, decision: str, reason: str) -> dict
    def log(self, entry: dict) -> None
    def recent(self, n: int = 10) -> list[dict]
```

Key differences:
- Engine is a class with explicit dependencies (no late binding).
- Single `micro()` method with embedding path; no separate `embedding_micro` / `micro_llm` / `raw_chunk` variants.
- Reflection log uses SQLite `INSERT` instead of JSONL append (optional; JSONL is fine too if simpler).

#### `context.py` — Context Assembly

```python
def build_context(store: MemoryStore, search: SearchIndex, skills: SkillStore,
                  query: str = "", max_tokens: int = 4000) -> str
```

Key differences:
- One function, one mode (Palace).
- No `profile_mode_enabled()`, `palace_mode_enabled()` branching.
- No `build_palace_index()` file caching — generate text on demand.

#### `api.py` — FastAPI Dashboard

Unchanged surface (14 endpoints), but internals simplified:
- Direct `store.list(zone=...)` instead of manual filtering.
- Direct `graph.neighbors()` instead of `_get_graph_manager()` dance.
- No `_ModuleProxy` import fallback; `store.py` is always importable.

---

## 4. Side-by-Side Comparison

| Concern | v1.0-beta (Current) | v1.0-beta2 (Redesign) | Delta |
|---|---|---|---|
| **Modules** | 13 Python + dashboard | 6 Python + dashboard | −54% |
| **Python LOC** | ~7,200 | ~2,600 | −64% |
| **Test modules** | 8 files, ad-hoc fixtures | 4 files, shared `conftest.py` | Simpler |
| **Primary storage** | Markdown files + SQLite graph + JSONL stats | Markdown files + **unified SQLite** | −2 layers |
| **In-memory caches** | 6 (`_id_to_path`, `_id_to_mem`, `_cache`, `_doc_tokens`, `_embed_index`, `_cached_adj`) | 1 (`_embed_array` in `SearchIndex`) | −83% |
| **Lock types** | RLock, Lock, per-path RLock pool | One RLock per store | −2 types |
| **Async I/O** | Queue + daemon thread + generations | None (sync under RLock) | Deleted |
| **Context modes** | 3 (Palace / Profile / Legacy) | 1 (Palace) | −67% |
| **Reflection modes** | 5 (full_llm/micro_llm/embedding/embedding_micro/raw_chunk) | 2 (embedding + llm fallback) | −60% |
| **Graph classes** | 4 (Protocol / Store / Engine / Router / Manager) | 1 (`GraphIndex`) | −80% |
| **Late binding** | `late_bind()` in 3 modules | None | Deleted |
| **Import order rules** | Strict 6-step DAG with traps | Natural 3-layer DAG | Simpler |

---

## 5. Data Flow Comparison

### 5.1 Current: Memory Write

```
Tool handler → MemoryStore.put()
  → async_write_memory() → queue.put() → daemon thread → _safe_write()
  → _update_cache_for_put() (manually patches 4 dicts + lists)
  → _try_index() (patches Python dict `_embed_index`)
  → graph auto-associate hook → GraphManager.associate_memories()
    → AssociationEngine.on_co_occurrence()
      → GraphStore.upsert_edge() → SQLite
        → _invalidate_adj_cache()
  → record_memory_stat() → _append_stat_entries() → JSONL append
```

**Touch points:** 8 functions, 4 persistence layers, 3 cache invalidations.

### 5.2 Beta2: Memory Write

```
Tool handler → MemoryStore.put()
  → INSERT INTO memories + tags + supersedes (SQLite, single transaction)
  → _write_md() (synchronous atomic write to .md file)
  → SearchIndex.invalidate() (clears embed array)
  → GraphIndex.associate() (SQLite edge upsert)
  → record_stat() (SQLite stats INSERT)
```

**Touch points:** 4 functions, 1 persistence layer (SQLite), 1 cache invalidation.

---

## 6. Key Design Decisions

### 6.1 Why Keep Markdown Files?

Deleting them and going full-SQLite would be even simpler, but it breaks the user's ability to:
- Edit memories with any text editor
- `git diff` memory changes
- Bulk-import/export via file copy

Markdown files are the **human interface**. SQLite is the **machine interface**.

### 6.2 Why Delete the Async Write Queue?

The queue was designed for high-throughput scenarios, but the actual workload is:
- Micro-reflection: 0–1 writes per 3 turns
- Full reflection: 0–5 writes per session end
- Manual writes: 0–1 per user action

Peak throughput is <1 write/second. A locked synchronous write is simpler, easier to reason about, and avoids the "generation token expired but file still on disk" edge cases.

### 6.3 Why Collapse Graph Abstractions?

The original design anticipated pluggable graph backends ("what if we swap SQLite for Neo4j?"). That never happened, and the protocol + engine + router layers add no value today. If a second backend is ever needed, a `Protocol` can be reintroduced then (YAGNI).

### 6.4 Why Keep BM25 Instead of SQLite FTS5?

FTS5 requires the SQLite build to have the extension enabled, which is not guaranteed on all Python distributions (especially Windows). A pure-Python BM25 over pre-tokenized text in SQLite is portable and only ~80 lines.

### 6.5 Why Not Use an ORM?

SQLAlchemy or Peewee would add a dependency and ~30% more code for little gain. The schema is simple (7 tables, no migrations needed). Raw `sqlite3` with helper methods is cleaner for this scale.

---

## 7. API Compatibility Guarantee

All 17 SRH tools retain identical signatures and return schemas:

```python
srh_memory_search(query, k, zone)
srh_memory_write(body, scope, confidence, tags, pinned, zone)
srh_memory_delete(id, scope)
srh_memory_history(id, max_depth)
srh_skill_search(query, k)
srh_palace_zones()
srh_palace_read_zone(zone)
srh_palace_recall(topic, zone, limit)
srh_palace_search(query, limit)
srh_palace_rebalance(dry_run)
srh_reflect_now()
srh_compile_profile(mode)
srh_associate(memory_ids, relation)
srh_graph_retrieve(memory_ids, task_type, max_results, tier)
srh_graph_stats()
srh_graph_viz(tier)
srh_memory_health()
```

Dashboard endpoints are unchanged. Memory file format (YAML frontmatter) is unchanged.

The only observable difference is performance (faster) and internal file layout (simpler).

---

## 8. Migration Plan

### Phase 1: Scaffold (1–2 days)
1. Create `store.py` with SQLite schema and `_sync_from_disk()`.
2. Port `MemoryStore` CRUD methods one by one with tests.
3. Verify `store.py` can read existing `~/.hermes/memories/*.md` files.

### Phase 2: Core Modules (3–4 days)
1. Port `search.py` (BM25 + embed + fusion).
2. Port `graph.py` (merge ahe_graph + pagerank + cross_zone).
3. Port `reflect.py` (simplify to 2-mode pipeline).
4. Port `context.py` (single Palace mode).

### Phase 3: Integration (2 days)
1. Port `tools/handlers.py` → thin wrappers around new modules.
2. Port `api.py` → use new module APIs directly.
3. Rewrite `__init__.py` as pure registration entrypoint.
4. Port test suite (reuse `conftest.py` fixtures).

### Phase 4: Migration Tool (1 day)
1. Write one-time migration script:
   - Read all existing `.md` files → new SQLite
   - Read `memory-stats.jsonl` → SQLite `stats` table
   - Read `ahe_graph_memory.db` → new unified SQLite (or keep separate if preferred)
2. Run against existing user data to verify.

### Phase 5: Cleanup (1 day)
1. Delete old modules: `core.py`, `graph/*`, `query/cache.py`, `search/embed.py`, `reflection/engine.py`, `hooks/lifecycle.py`, `tools/handlers.py`, `late_binding.py`.
2. Update `CLAUDE.md`, `README.md`, docs.
3. Final test run: `pytest tests/ -v`.

**Total estimated effort:** 8–10 days of focused work.

---

## 9. Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SQLite WAL mode issues on Windows | Medium | High | Test on Windows; fallback to `journal_mode=DELETE` if WAL fails |
| Existing user data migration edge cases | Medium | High | One-time migration script with dry-run mode; backup before migrate |
| Embedding numpy dependency | Low | Medium | Keep sentence-transformers fallback; numpy is already common |
| Performance regression on very large stores (>1000 memories) | Low | Medium | BM25 over SQLite is still O(n); if needed, add inverted index later |
| Plugin host (Hermes) import expectations | Medium | High | Keep `__init__.py` exports identical; only internal layout changes |

---

## 10. Success Metrics

| Metric | Current | Target | How to Measure |
|---|---|---|---|
| Python LOC | ~7,200 | <3,000 | `find . -name "*.py" | xargs wc -l` |
| Modules | 13 | 6 | `ls *.py` |
| `pytest` runtime | ~8s | <5s | `time pytest tests/ -q` |
| Memory write latency | ~5ms (queue + async) | <2ms (sync SQLite) | Benchmark script |
| Fusion search latency | ~15ms | <5ms | Benchmark script |
| Circular import count | 1 (`late_binding.py`) | 0 | Static analysis (`import mem_reflection_hermes` from clean env) |

---

## Appendix A: Module Responsibility Map

```
store.py
  ├─ MemoryStore         (CRUD, listing, lineage, health)
  ├─ SkillStore          (listing, matching)
  ├─ MemoryFrontmatter   (dataclass)
  ├─ LoadedMemory        (dataclass)
  ├─ parse_frontmatter() (YAML frontmatter I/O)
  └─ serialize_frontmatter()

search.py
  ├─ SearchIndex         (BM25, embed, fusion, conflict)
  ├─ _tokenise()         (CJK-aware tokenizer)
  ├─ _embed_single()     (ONNX / sentence-transformers)
  └─ _cosine_sim()       (numpy dot product)

graph.py
  ├─ GraphIndex          (edges, meta, decay, PageRank, cross-zone)
  └─ spread_activation() (fixed-point iteration)

reflect.py
  ├─ ReflectionEngine    (micro, full, audit, log)
  └─ _sanitize_filename()

context.py
  ├─ build_context()     (context block assembly)
  └─ match_skills()      (token overlap skill matching)

api.py
  └─ router              (14 FastAPI endpoints)

__init__.py
  └─ register()          (tool + hook + slash command registration)
```

---

## 11. Open-Source Validation & Optimizations

After surveying the 2025–2026 open-source ecosystem for agent memory and personal knowledge-base systems, **the beta2 "SQLite-indexed Markdown" direction is strongly validated**. Nearly every active project in this space converges on the same three pillars: Markdown + YAML frontmatter as source of truth, SQLite as derived index, and hybrid keyword + semantic retrieval.

Below is a synthesis of findings from **12 relevant projects** and the concrete optimizations they suggest for beta2.

### 11.1 Projects Surveyed

| Project | Language | Stars | Key Relevance |
|---------|----------|-------|---------------|
| **[echovault](https://github.com/mraza007/echovault)** | Python | ~500 | Local-first coding-agent memory. Markdown + YAML frontmatter. SQLite FTS5 + sqlite-vec. MCP server. |
| **[palinode](https://github.com/phasespace-labs/palinode)** | Python | ~300 | FastAPI + MCP. Markdown + YAML frontmatter. SQLite-vec + FTS5. BGE-M3 embeddings. Git-versioned. |
| **[memweave](https://github.com/sachinsharma9780/memweave)** | Python | ~200 | Zero-infra agent memory. Markdown source of truth + SQLite derived cache. BM25 + vector. Temporal decay + MMR. |
| **[k-lines](https://github.com/r0k3/k-lines)** | Python | ~150 | 4-memory taxonomy (Episodic/Semantic/Procedural/Reflective). Markdown + YAML frontmatter. SQLite FTS5 + ChromaDB. RRF fusion. |
| **[memory-graph](https://github.com/afgonullu/memory-graph)** | Python | ~100 | File-based knowledge graph. Markdown + YAML frontmatter. Zero DB deps. Derived SQLite + FTS5 indexes are disposable. |
| **[pi-hermes-memory](https://github.com/chandra447/pi-hermes-memory)** | Python | ~80 | Hermes-style persistent memory. Markdown + SQLite FTS5. SKILL.md with YAML frontmatter. |
| **[bloxcue](https://github.com/bokiko/bloxcue)** | Python | ~50 | MCP-first local context. Markdown blocks + frontmatter. BM25 + SQLite learned memory. |
| **[Mem0](https://github.com/mem0ai/mem0)** | Python | 37k+ | Memory layer for LLMs. Hybrid store (vector + graph + SQLite history). Extract → Consolidate → Retrieve pipeline. |
| **[knowledge-base-mcp](https://github.com/handrew/knowledge-base-mcp)** | Python | ~200 | Hybrid search (vector + FTS5). SQLite default. Deduplication. TTL. |
| **[SQLDown](https://github.com/mbailey/sqldown)** | Python | ~100 | Bidirectional Markdown ↔ SQLite converter. Dynamic schema from YAML frontmatter. Watch mode. |
| **[OpenClaw memory](https://github.com/AkashaBot/openclaw-memory-offline-sqlite)** | TypeScript | ~200 | SQLite FTS5/BM25 + embeddings. Offline-first. Windows-friendly. 70/30 vector/BM25 weighting. |
| **[Vault](https://mcpmarket.com/zh/server/vault-9)** | Python | — | Auto-rebuilding SQLite index over Markdown. FTS5 + embedding. Wikilinks. |

### 11.2 Cross-Project Architectural Consensus

Every project above agrees on these five patterns — beta2 already aligns with 4/5:

1. **Markdown + YAML frontmatter = human-readable source of truth**
   — All 12 projects use this. Validates beta2's decision to keep `.md` files.

2. **SQLite as derived / disposable index**
   — echovault, palinode, memweave, memory-graph, and k-lines all treat SQLite as a **rebuildable cache**, not primary storage. The `.md` files are the contract; the DB can be deleted and regenerated.
   — **Beta2 alignment:** Exact match. `_sync_from_disk()` on startup embodies this.

3. **Hybrid retrieval (keyword + semantic)**
   — All projects with search use some form of BM25/FTS5 + embedding fusion. No project relies on a single modality.
   — **Beta2 alignment:** Exact match. `SearchIndex.fusion()` already does this.

4. **Local-first, zero external infrastructure**
   — Every project in the list is designed to run without Docker, without cloud APIs (embeddings may be local), and without dedicated vector DBs.
   — **Beta2 alignment:** Exact match. ONNX Runtime + SQLite are both local.

5. **MCP (Model Context Protocol) exposure**
   — echovault, palinode, bloxcue, and knowledge-base-mcp all expose memory via MCP so Claude Code, Cursor, Codex, etc. can share a single memory store.
   — **Beta2 gap:** Not in scope for Hermes plugin, but worth documenting as a Phase-6 extension.

### 11.3 Concrete Optimizations for Beta2

#### O1: Use SQLite FTS5 as Default BM25, Hand-Rolled BM25 as Fallback

**Current beta2 plan:** Keep hand-rolled BM25 because "FTS5 may not be available on all Python distributions (especially Windows)."

**Open-source reality:** echovault, palinode, k-lines, pi-hermes-memory, and bloxcue all use FTS5 on Windows, macOS, and Linux without issues. Python's `sqlite3` module bundles FTS5 on:
- CPython 3.11+ official builds (all platforms)
- conda-forge builds
- Most Linux distro packages (Ubuntu, Debian, Fedora, Arch)
- Windows python.org installers

The only environment where FTS5 is missing is **custom-compiled Python with `--disable-fts5`** — extremely rare.

**Optimized design:**
```python
# search.py
_FTS5_AVAILABLE = False
try:
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    _FTS5_AVAILABLE = True
except Exception:
    pass

def _bm25_search(...) -> list[tuple[LoadedMemory, float]]:
    if _FTS5_AVAILABLE:
        return _fts5_search(...)      # ~2ms, native C implementation
    return _handrolled_bm25(...)      # ~5ms, pure Python fallback
```

**Benefits:**
- FTS5 is 2–3× faster than pure-Python BM25 on identical corpora
- No token pre-computation needed in SQLite schema (`tokens_json` column can be removed)
- CJK text is handled natively by FTS5's Unicode tokenizer
- Fallback preserves portability guarantee

**Schema simplification:** Remove `tokens_json` from `memories` table; add `FTS5` virtual table instead:
```sql
CREATE VIRTUAL TABLE mem_fts USING fts5(
    body,
    tags,
    content='memories',
    content_rowid='rowid'
);
```

#### O2: Evaluate sqlite-vec as Optional Embedding Backend

**Current beta2 plan:** Store embeddings as `BLOB` in SQLite and do numpy dot-product in Python.

**Open-source reality:** palinode, memweave, and OpenClaw memory all use `sqlite-vec` — a SQLite extension that adds native vector search (`vec0` virtual table) with cosine similarity, L2 distance, and metadata filtering inside SQL.

**Trade-off analysis:**

| Concern | numpy.ndarray (beta2 plan) | sqlite-vec (alternative) |
|---|---|---|
| Dependencies | `numpy` only | `sqlite-vec` PyPI package |
| Vector search speed | Fast (numpy C) | Fast (SQLite C extension) |
| Filtered search | Post-filter in Python | Native `WHERE` in SQL |
| Index rebuild | Re-allocate ndarray | `DELETE` + `INSERT` into vec0 table |
| Portability | Zero dependency if embed off | Requires pre-built wheel |
| Embedding dimension flexibility | Any | Any |

**Optimized design:** Support both. Default to numpy (zero dependency, no wheel issues). Add an `sqlite-vec` code path when the package is installed:

```python
# search.py
class SearchIndex:
    def __init__(self, store: MemoryStore):
        self.store = store
        self._vec_backend = self._detect_vec_backend()
        self._embed_array: np.ndarray | None = None  # numpy path
        self._embed_ids: list[str] = []

    def _detect_vec_backend(self) -> str:
        try:
            import sqlite_vec
            return "sqlite-vec"
        except ImportError:
            return "numpy"
```

This keeps the default zero-dependency while giving power users a native vector-index option.

#### O3: Replace Weighted Fusion with RRF as Default

**Current beta2 plan:** Weighted fusion (`alpha * cosine + beta * bm25 + gamma * recency + delta * eff`).

**Open-source reality:** Every hybrid project in the survey (k-lines, memweave, OpenClaw, knowledge-base-mcp, idalin6127's hybrid retriever) uses **Reciprocal Rank Fusion (RRF)** as the default merge strategy. The 2025 production survey (Thread Transfer blog) confirms RRF is the "safe default" because it:
- Requires no score normalization (robust to different score scales)
- Needs no hyper-parameter tuning (`k=60` is universal)
- Naturally handles missing channels (if embedding is off, BM25 ranks still contribute)
- Outperforms fixed-weight fusion in NDCG@10 (0.61 vs 0.54 for dense alone)

**Optimized design:**
```python
def _rrf_fuse(rank_lists: dict[str, list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion across multiple ranked lists.
    rank_lists: {'bm25': [id1, id2, ...], 'embed': [id3, id1, ...], ...}
    Returns: {id: rrf_score, ...}
    """
    scores: dict[str, float] = {}
    for channel, ranked in rank_lists.items():
        for rank, mem_id in enumerate(ranked, start=1):
            scores[mem_id] = scores.get(mem_id, 0.0) + 1.0 / (k + rank)
    return scores
```

**Updated `fusion_search()` flow:**
1. BM25 top-N → ranked list A
2. Embedding top-N → ranked list B
3. Recency top-N → ranked list C (optional)
4. Effectiveness top-N → ranked list D (optional)
5. RRF merge → final ranked list
6. Apply Hebbian boost and PageRank hub bonus as **post-RRF additive scores** (not part of RRF itself)

**Keep weighted fusion as a config option** for users who want fine-grained control, but make RRF the default.

#### O4: Add MMR Reranking as Optional Post-Processing

**Current beta2 plan:** Fusion returns top-k by combined score. No diversity consideration.

**Open-source reality:** memweave explicitly supports **Maximal Marginal Relevance (MMR)** reranking to balance relevance with diversity. This prevents the "all results say the same thing" problem common in small memory stores with repetitive content.

**Optimized design:** Add an optional `mmr_lambda: float = 0.0` parameter to `fusion_search()`. When `lambda > 0`:

```python
def _mmr_rerank(candidates: list[LoadedMemory], query_embed: np.ndarray,
                lambda_param: float = 0.5, k: int = 5) -> list[LoadedMemory]:
    """MMR: maximize relevance while penalizing similarity to already-selected items."""
    selected: list[LoadedMemory] = []
    remaining = list(candidates)
    while remaining and len(selected) < k:
        best_score = -1.0
        best_idx = 0
        for i, cand in enumerate(remaining):
            rel = _cosine_sim(query_embed, _get_embed(cand.id()))
            div = max((_cosine_sim(_get_embed(cand.id()), _get_embed(s.id()))
                       for s in selected), default=0.0)
            score = lambda_param * rel - (1 - lambda_param) * div
            if score > best_score:
                best_score = score
                best_idx = i
        selected.append(remaining.pop(best_idx))
    return selected
```

**Default:** `mmr_lambda=0.0` (disabled) to preserve current behavior. Users can set `mmr_lambda=0.5` for diversity-aware retrieval.

#### O5: Mem0-Style Reflection Pipeline as Optional Advanced Mode

**Current beta2 plan:** Simplified reflection: embedding similarity check → conflict detection → write.

**Open-source reality:** Mem0 (37k stars, used by Netflix/Lemonade) uses a more sophisticated **Extract → Consolidate → Retrieve** pipeline:
1. **Extract:** LLM extracts candidate facts from the conversation
2. **Consolidate:** For each candidate, retrieve semantically similar memories; LLM decides `ADD / UPDATE / DELETE / NOOP`
3. **Retrieve:** At inference time, embed query and fetch top-k

**Optimized design:** Keep the lightweight embedding+BM25 reflection as the **default** (fast, cheap, no LLM calls). Add a `reflection_mode: str = "embedding" | "llm-merge"` config option.

When `llm-merge` is enabled:
```python
# reflect.py — optional advanced path
def _llm_merge_candidate(ctx, candidate: dict, similar: list[LoadedMemory]) -> str:
    """Ask LLM to decide what to do with a candidate memory.
    Returns one of: ADD, UPDATE(existing_id), DELETE(existing_id), NOOP
    """
    prompt = _build_merge_prompt(candidate, similar)
    decision = ctx.llm.complete(prompt)
    return _parse_merge_decision(decision)
```

This mirrors Mem0's quality without forcing every user to pay for LLM reflection on every turn.

#### O6: Strengthen "Disposable Index" Documentation and Tooling

**Current beta2 plan:** Mentions `_sync_from_disk()` at startup.

**Open-source reality:** memory-graph and memweave both make the disposable-index pattern **explicit** in their APIs:
- `rebuild_index()` — force rebuild from `.md` files
- `validate_index()` — check for orphaned DB rows or missing files
- `prune_index()` — remove DB entries for deleted `.md` files

**Optimized design:** Add three public methods to `MemoryStore`:
```python
def rebuild_index(self) -> dict:          # Full resync from disk
    """Rebuild SQLite index from all .md files. Returns stats."""

def validate_index(self) -> list[str]:    # Consistency check
    """Return list of inconsistencies (orphaned rows, missing files, etc.)."""

def prune_index(self) -> int:             # Cleanup
    """Remove DB entries for .md files that no longer exist. Returns count."""
```

These make the disposable-index pattern **discoverable and operable**, not just an implementation detail.

#### O7: MCP Exposure as Phase-6 Extension

**Current beta2 plan:** Hermes plugin registration only.

**Open-source reality:** echovault, palinode, bloxcue, and knowledge-base-mcp all expose memory via **Model Context Protocol (MCP)**. This lets Claude Code, Cursor, Windsurf, and Codex share the same memory without each tool having its own plugin.

**Optimized design (future):** Add an `mcp_server.py` module (separate from core plugin) that exposes:
- `memory_search(query, k)`
- `memory_write(body, zone, tags)`
- `memory_graph_neighbors(memory_id)`
- `memory_health()`

This is a **Phase-6** item (after beta2 ships) but worth noting in the roadmap.

### 11.4 Updated Side-by-Side Comparison

| Concern | v1.0-beta | v1.0-beta2 (Original) | v1.0-beta2 (Optimized) |
|---|---|---|---|
| **BM25 engine** | Hand-rolled Python | Hand-rolled Python | **FTS5 native** (hand-rolled fallback) |
| **Fusion strategy** | Weighted α/β/γ/δ | Weighted α/β/γ/δ | **RRF default** (weighted optional) |
| **Diversity rerank** | None | None | **MMR** (optional, λ=0 default) |
| **Reflection modes** | 5 modes | 2 modes | **2 modes** + optional LLM-merge |
| **Index rebuild** | Implicit at startup | `_sync_from_disk()` | **Explicit API** (rebuild/validate/prune) |
| **Vector backend** | Python dict loop | numpy.ndarray | **numpy default** + sqlite-vec optional |
| **MCP exposure** | None | None | Phase-6 extension |

### 11.5 Sources

- [SQLDown — Bidirectional Markdown ↔ SQLite](https://github.com/mbailey/sqldown)
- [knowledge-base-mcp — Hybrid search with SQLite](https://github.com/handrew/knowledge-base-mcp)
- [Vault — AI Agent Markdown & SQLite Knowledge Base](https://mcpmarket.com/zh/server/vault-9)
- [OpenClaw Memory — SQLite FTS5 + BM25 + Embeddings](https://github.com/AkashaBot/openclaw-memory-offline-sqlite)
- [echovault — Local-first memory for coding agents](https://github.com/mraza007/echovault)
- [palinode — Memory substrate for AI agents](https://github.com/phasespace-labs/palinode)
- [memweave — Zero-infra agent memory](https://github.com/sachinsharma9780/memweave)
- [k-lines — Cognitively-grounded memory architecture](https://github.com/r0k3/k-lines)
- [pi-hermes-memory — Hermes-style persistent memory](https://github.com/chandra447/pi-hermes-memory)
- [memory-graph — File-based personal knowledge graph](https://github.com/afgonullu/memory-graph)
- [bloxcue — MCP-first local context retrieval](https://github.com/bokiko/bloxcue)
- [Mem0 — Open-source memory layer for LLMs](https://github.com/mem0ai/mem0)
- [Hybrid Search in 2025 — Thread Transfer](https://thread-transfer.com/blog/2025-03-22-hybrid-search-production/)
- [Hybrid Retriever — FAISS + SQLite FTS5/BM25](https://github.com/idalin6127/Module5-Hybrid-Retriever-SFT-FAISS-FTS5-BM25-LoRA-QLoRA)
- [LlamaIndex BM25 Retriever](https://developers.llamaindex.ai/python/framework/integrations/retrievers/bm25_retriever/)
- [LlamaIndex Reciprocal Rerank Fusion](https://developers.llamaindex.ai/python/framework/integrations/retrievers/reciprocal_rerank_fusion/)
- [Mem0 Architecture — Emergent Mind](https://www.emergentmind.com/topics/mem0-system)
- [Mem0 vs LangChain — AICoolies](https://aicoolies.com/comparisons/mem0-vs-langlang)
- [Local AI Wiki with Markdown + GPT + SQLite](https://notes.suhaib.in/docs/tech/how-to/how-to-build-a-local-ai-wiki-with-markdown-+-gpt-+-sqlite/)
- [MarkdownDB Launch — Datopian](https://www.datopian.com/blog/markdowndb-launch)
