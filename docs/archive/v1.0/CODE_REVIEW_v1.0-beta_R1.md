# v1.0-beta Code Review — Round 1 (First Pass)

**Date**: 2026-06-02
**Version**: 1.0-beta (bumped from 0.9.2-beta2)
**Test baseline**: 105/105 tests passing, 0 skipped
**Review scope**: All 12 code modules + dashboard (≈7,000 LOC)

---

## Executive Summary

| Severity | Count | Action |
|----------|-------|--------|
| **CRITICAL** | 1 | Must fix before release |
| **HIGH** | 30 | Should fix before merge |
| **MEDIUM** | 19 | Fix in next wave |
| **LOW** | 13 | Opportunistic cleanup |
| **Total** | **63** | |

**Verdict: BLOCK** — 1 CRITICAL issue causes a `NameError` in the embedding retrieval path. The 30 HIGH issues span thread safety (6), silent error swallowing (5), logic bugs (5), performance N+1 patterns (4), and other categories (10). The medium/low issues are predominantly maintainability concerns and edge-case robustness.

No security vulnerabilities (path traversal, injection, secret leakage) were found. SQL queries consistently use parameterized placeholders.

---

## CRITICAL (1)

### C1. `_embed_single` / `_cosine_sim` — NameError at runtime

**File**: `__init__.py`

**Issue**: Both functions are defined in `search/embed.py` with `__all__` entries, but they are NOT re-exported through any of the three star-import chains (`reflection.engine.*`, `tools.handlers.*`, `hooks.lifecycle.*`). Neither `embed.py` is directly star-imported in `__init__.py`. The `core.py` import at line 76 imports `_cosine_similarity` (takes `Dict[str,float]` args), not `_cosine_sim` (takes embedding vectors). Calling either function at runtime raises `NameError`. This is the primary retrieval path for fusion search — the embedding channel silently fails, degrading to BM25-only with no indication.

**Fix**: Add explicit imports:
```python
from .search.embed import _embed_single, _cosine_sim
```

---

## HIGH (30)

### Core / MemoryStore (__init__.py + core.py)

**H1. `_gm_getter_func` — unbound NameError risk** — `__init__.py:1894`
The `global _gm_getter_func` is set inside a try block within `_register_slash_commands()`. If any exception occurs before that line, `register()` at line 1894 accesses an unbound name. Initialize `_gm_getter_func = None` at module level.

**H2. `sup_factor` inversion penalizes current (newest) memories** — `__init__.py:907-908`
`sup_factor = 1.0 / (1.0 + supersedes_depth)`. Depth-3 (most-revised, current) memory gets factor 0.25; depth-0 (never superseded, potentially stale) gets 1.0. The intent is to penalize obsolete chain members, but this penalizes the CURRENT revision.

**H3. Async write failures logged at DEBUG — silent data loss** — `core.py:636`
`_file_flush_worker` logs `logger.debug("Async write failed for %s", path)`. DEBUG is disabled in production. Same pattern at `record_memory_stat` (line 508), `batch_record_stats` (line 516), `load_effectiveness` (line 704). Use `logger.warning`.

**H4. `load_effectiveness()` returns empty dict on corrupt stats** — `core.py:673-704`
Outer `except Exception` at line 703 catches corrupted `stats.jsonl` and silently returns `{}`, flattening all effectiveness scores to 1.0. Corruption persists undetected. Log the exception and consider partial-result returns.

**H5. `update()` dead code — invalidated cache makes incremental update a no-op** — `__init__.py:682-684`
`_invalidate_cache()` sets `_cache_valid = False`, then `_update_cache_for_put()` immediately returns because cache is invalid. The `_id_to_mem` map is not populated. Either update cache before invalidating, or populate `_id_to_mem` directly.

**H6. `MemoryStore` has no thread-safety guarantees** — `__init__.py:292-297`
`_cache`, `_id_to_path`, `_id_to_mem`, `_doc_tokens` are mutated by `_ensure_cache`, `put`, `delete`, `update`, `reorder` without any lock. Concurrent `list_active()` during `put()` causes inconsistent reads. Add `threading.RLock` to all public mutation methods.

**H7. Embedding errors in `fusion_search` silently degrade to BM25-only** — `__init__.py:812-816`
`try/except Exception: pass` around `_embed_search()`. Transient ONNX errors (OOM, corrupted model) silently remove the entire embedding channel. Log at warning level and track a health metric.

**H8. `WeakValueDictionary` GC race on `_write_path_locks`** — `core.py:525`
If GC collects an `RLock` object between `_write_path_lock` returning it and the caller acquiring it, mutual exclusion breaks. Use a regular `Dict[Path, RLock]` or bounded cleanup.

**H9. `_write_generations` grows unboundedly** — `core.py:526`
Every unique file path accumulates an entry. Over long-running instances, deleted memories' paths remain indefinitely. Evict entries for paths with no pending writes.

**H10. Duplicate constant definitions shadow imports** — `__init__.py:1068-1120`
`_MIN_TOKEN_LEN`, `_TOKEN_RE`, `_CJK_RANGES`, `_is_cjk`, `_cjk_ratio`, `_adaptive_conflict_threshold` are redefined in `__init__.py` despite being imported from `core.py`. Two independent copies exist. Remove the duplicate definitions.

---

### Graph Modules (ahe_graph.py, pagerank.py, cluqi.py, cross_zone.py)

**H11. `_build_adjacency` cache ignores `min_weight` — returns stale results** — `ahe_graph.py:320-359`
Cache is keyed only by DB mtime. Two calls to `spread_activation()` with different `min_weight` values return the same adjacency, including edges below the stricter threshold. Include `min_weight` in the cache key.

**H12. `decay_all()` holds `_lock` for full row iteration — blocks all threads** — `ahe_graph.py:600-631`
For 10,000 memories this holds the lock for potentially seconds. Process rows in batches (100 at a time) releasing and re-acquiring.

**H13. No per-row error handling in `decay_all` — malformed date crashes entire operation** — `ahe_graph.py:610-631`
Corrupt `last_access_at` raises unhandled `ValueError`, leaving the DB transaction incomplete. Wrap per-row processing in try/except.

**H14. Isolated seeds silently dropped in `spread_activation`** — `ahe_graph.py:388-431`
Seeds in `graph_memory_meta` but with zero edges are not in the adjacency dict and are silently skipped. Returns `{}` with no warning. Log a warning or include isolated nodes.

**H15. `compute_pagerank` issues N+1 neighbor queries** — `pagerank.py:47-56`
One `get_neighbors()` call per node. For 1000 nodes this is 1000 separate SQL queries. Build the graph from a single `get_all_edges()` query.

**H16. `get_top_pagerank` with zone filter issues N+1 meta queries** — `pagerank.py:97-103`
Additional `get_meta(nid)` call per scored node when zone filtering. Include zone info in the `get_all_nodes()` result.

**H17. `_lineage_status` O(n*k) scan in CLUQI** — `cluqi.py:211-228`
Fallback iterates `list_active()` to find superseded status per result. For k=30 results against 500 memories: 15,000 comparisons. Cache the supersedes mapping once at query time.

**H18. `_build_adjacency` mtime check outside lock — stale cache race** — `ahe_graph.py:330-335`
Mtime read at line 331 outside `self._lock`. Another thread can mutate the graph between the mtime read and the DB query inside the lock. Move cache check-and-rebuild entirely inside `self._lock`.

---

### Reflection / Hooks / Handlers (engine.py, lifecycle.py, handlers.py)

**H19. Silent error swallowing in `_append_reflect_log`** — `engine.py:168-174`
Both log rotation and file write blocks use bare `except Exception: pass`. Disk-full or permission-denied failures are invisible. Log at `warning` level.

**H20. Race condition on `_session_messages` dict** — `lifecycle.py:51,124,157`
Module-level dict accessed in `_pre_llm_call` (append) and `_on_session_end` (pop) with no lock. Concurrent sessions cause `KeyError` or lost messages. Guard with `threading.Lock()`.

**H21. Race condition on `_turns_since_reflect` counter** — `lifecycle.py:105,178-191`
Integer increment (read-modify-write) without a lock. Two sessions can both read, increment, and skip reflection when one should trigger. Use `threading.Lock`.

**H22. Read/write race on reflect log file** — `engine.py:177-193 vs 144-174`
`_recent_reflect_outcomes` reads the log WITHOUT acquiring `_reflect_log_lock`. Concurrent write/rotation produces torn reads or `FileNotFoundError`. Acquire the lock.

**H23. Escape-sequence bypass in `_repair_truncated_json`** — `engine.py:353-363`
Backward comma search (`rfind(",")`) finds commas inside string values containing escaped quotes. Produces truncated JSON that starts mid-string. Track unescaped quotes in the backward scan.

**H24. Wildcard import provides names used earlier in file** — `lifecycle.py:425, lines 149-227`
`_is_explicit_memory_intent` (used at line 179) comes from `from ..reflection.engine import *` at line 425 (END of file). Works at runtime but fragile. Move import to top.

**H25. Duplicated `_lb` late-binding cache across modules** — `handlers.py:44-61`, `lifecycle.py:58-75`
Two independent `_lb` functions with separate caches. Each resolves same names independently. Extract to shared module.

**H26. Reflection feedback loop: raw_chunk output feeds embedding reflection** — `engine.py:1396-1465`
Raw chunk writes to `episode` zone; embedding reflection extracts facts from `episode` zone in the same cycle. The model reflects on its own just-written output. Exclude current-session memories from similarity search.

**H27. Supersedes cycle check validates only first target** — `engine.py:991-1022`, `handlers.py:200-207`
When `supersedes: [mem1, mem2]`, only `mem1` is checked for cycles. `mem2` could create a cycle. Validate ALL targets.

**H28. No timeout on LLM calls in reflection pipeline** — `engine.py:541-698`
If the LLM provider hangs, the reflection thread blocks indefinitely. Add `timeout=30` to all LLM calls.

---

### Search / Query / Dashboard (embed.py, cache.py, plugin_api.py)

**H29. Broken non-LRU cache eviction in `_set_cached_embed`** — `embed.py:251-261`
Clears cache and keeps last 250 of insertion ORDER, discarding recently-accessed (promoted) items. `_put_cached_embed` has proper LRU but is dead code. Use `popitem(last=False)` and `move_to_end`.

**H30. `_intent_prototypes()` silently discards user config errors** — `embed.py:125-126`
Bare `except Exception: pass` swallows malformed config. User gets no feedback that customization was ignored. Log the exception.

**H31. `_classify_intent_stats` mutations are unsynchronized** — `embed.py:165,199-214`
Read-modify-write operations on shared dict without a lock. Counter increments lost under concurrency. Use `threading.Lock`.

**H32. `invalidate_pattern()` cannot match any cache key** — `cache.py:174-180`
Keys are hex hashes. Substring matching on hex is useless (non-hex chars never match) or too broad (hex digits match nearly everything). Either remove or maintain a semantic-tag index.

**H33. `get_cache()` race on global singleton** — `cache.py:203-211`
Two concurrent callers each create a `ResultCache` instance. Second overwrites global, first is discarded. Use module-level instantiation or add a lock.

**H34. SQLite connection leak in `delete_memory`** — `plugin_api.py:231-236`
`_connect()` called but `conn` never closed. WAL locks leak. Repeated calls accumulate unclosed connections. Use `try/finally` with `conn.close()`.

**H35. Double `json.loads` crash on non-JSON response** — `plugin_api.py:170-183`
First parse (line 171) catches error; second parse (line 182) does NOT. Non-JSON response causes unhandled `JSONDecodeError`. Reuse the already-parsed `result_obj`.

---

## MEDIUM (19)

### Core / MemoryStore
1. `build_palace_index` catches all exceptions silently — `__init__.py:209`
2. `check_conflict` tokenizes query body twice — `__init__.py:989-990`
3. `reorder()` uses ad-hoc tempfile instead of `_safe_write` — `__init__.py:733-735`

### Graph
4. `close()` tears down all thread connections without coordination — `ahe_graph.py:150-174`
5. Bare except blocks in `close()` silently swallow checkpoint failures — `ahe_graph.py:164-174`
6. `add_supersedes_edge` writes edges despite deprecated docstring claiming otherwise — `ahe_graph.py:563-585`
7. `GraphStoreProtocol` not inheriting from `typing.Protocol` — `ahe_graph.py:37-83`
8. Dead dict-style activation code in `_query_graph_layer` — `cluqi.py:192-199`
9. `RetrievalRouter` accepts concrete `GraphStore` instead of `GraphStoreProtocol` — `ahe_graph.py:789`

### Reflection / Hooks / Handlers
10. Archive directory accumulation in `_append_reflect_log` (no retention policy) — `engine.py:157-170`
11. `_late_bindings` cache has no invalidation mechanism — `handlers.py:44-61`
12. Console output capture without restore-on-exception — `engine.py:541-698`
13. `_sanitize_filename` allows path separators — `engine.py:530-541`
14. `_run_embedding_reflection` exceeds 267 lines — `engine.py:1127-1393`
15. `register()` in handlers.py exceeds 244 lines — `handlers.py:736-979`
16. `_recent_reflect_outcomes` file-based heuristic is fragile — `engine.py:177-193`

### Search / Query / Dashboard
17. `ResultCache` eviction uses earliest expiry instead of access recency — `cache.py:159-161`
18. `SkillStore` reconstructed on every `GET /graph` request — `plugin_api.py:357-360`
19. `_set_cached_embed` is O(n) instead of O(1) — `embed.py:251-261`

---

## LOW (13)

### Core / MemoryStore
1. `_ensure_doc_tokens` return type annotation mismatch (`Counter` vs `List[str]`) — `__init__.py:944`
2. CJK bigram comment says "overlapping" but stride is 2 (non-overlapping) — `core.py:818`
3. `str(fm.created)[:10]` fragile slicing — `__init__.py:598`
4. `reorder()` duplicates `_safe_write` pattern — `__init__.py:733-735`

### Graph
5. `zone_degree` computed but never used in return value — `cross_zone.py:41,68`
6. `get_zone_recommendations` missing None guard on `target_id` — `cross_zone.py:114`
7. `propagate_activation` BFS per-node limit may prune important paths — `ahe_graph.py:457`
8. Magic thresholds (0.05, 0.15) in `decay_all` undocumented — `ahe_graph.py:620-625`

### Reflection / Hooks / Handlers
9. `_jd` helper `default=str` silently coerces non-serializable types — `handlers.py:34-38`
10. `print()` statements in production code paths (verify with grep)
11. Inconsistent docstring style across modules
12. Wildcard imports pollute namespace — `lifecycle.py:425,433`

### Search / Query / Dashboard
13. `_put_cached_embed` is dead code — `embed.py:74`

---

## v1.0-beta Version Bump Summary

The following files were updated from `0.9.2-beta2` → `1.0-beta`:

| File | Change |
|------|--------|
| `plugin.yaml` | `version: 1.0-beta`, updated description |
| `__init__.py` | Docstring version lines |
| `scripts/check_v092.py` | All version references |

Remaining `0.9.2` references in docs/ and review/ are historical documents and intentionally preserved.

---

## Fix Prioritization

### Wave 1 (must fix, 1 item)
- C1: Add missing `_embed_single` / `_cosine_sim` imports to `__init__.py`

### Wave 2 (should fix, 9 items)
- H1: Initialize `_gm_getter_func = None` at module level
- H2: Fix `sup_factor` inversion in `fusion_search`
- H3-H7: Add logger.warning to silent failure paths
- H11: Fix `_build_adjacency` cache to include `min_weight`
- H14: Log warning for isolated seeds in `spread_activation`
- H15-H17: N+1 query patterns in PageRank and CLUQI

### Wave 3 (thread safety, 6 items)
- H6: Add `RLock` to `MemoryStore` mutation methods
- H20-H22: Add locks to `_session_messages`, `_turns_since_reflect`, reflect log file
- H31, H33: Fix race conditions in embed cache and get_cache singleton

### Wave 4 (robustness, 6 items)
- H13, H19, H23, H26, H27, H28: Per-row error handling, JSON repair, reflection feedback loop, LLM timeouts
