# PR #15 Codex Inline Comments — Systematic Debugging Tracker

> Fetched from `sea-monsters/mem-reflection-hermes` PR #15 at 2026-06-12.
> Latest unaddressed codex review: commit `6311aff37b`.

## Unaddressed Codex Inline Comments

| # | File | Priority | Summary | Status | Root Cause | Fix |
|---|------|----------|---------|--------|------------|-----|
| 1 | `__init__.py` | P1 | Top-level import fails because `__package__` is empty string, not `None` | ✅ Fixed | Guard checks `__package__ is None`; pytest from repo root sets `__package__ = ""` (empty string) | Change guard to `if not __package__:` |
| 2 | `runtime/hooks.py` | P2 | ThreadPoolExecutor timeout path waits for slow worker, blocking stable-only fallback | ✅ Fixed | `with ThreadPoolExecutor(...)` context manager calls `shutdown(wait=True)` on exit, even after `future.result(timeout=...)` raises TimeoutError | Explicit executor + `shutdown(wait=False)` on timeout path |
| 3 | `core/search.py` | P2 | BM25 filters after top-k truncation, dropping in-scope matches | ✅ Fixed | `_bm25_search_bm25s` selects global top-k indices first, then filters against scoped `active_map`; in-scope matches below global top-k are lost | Mask out-of-scope IDs before `argpartition`/sort |
| 4 | `core/search.py` | P2 | Embedding filters after top-k truncation, dropping in-scope matches | ✅ Fixed | `_embed_search` selects global top-k via `argpartition` before filtering against `allowed_ids`; in-scope matches ranked below k globally are dropped | Mask out-of-scope IDs before `argpartition` |
| 5 | `core/store.py` | P2 | `delete_by_filters` does not run `_post_delete_callbacks`, leaving stale graph entries | ✅ Fixed | `delete_by_filters` deletes rows and files but never iterates `self._post_delete_callbacks`; `delete()` does at lines 807-811 | Collect deleted IDs, commit, then invoke callbacks |
| 6 | `memory/context.py` | P2 | `stable_only=True` still builds dynamic context parts (compacted episodes) | ✅ Fixed | `build_context_bundle` always calls `_build_dynamic_context_parts`, which unconditionally builds compacted episode summaries | Skip `_build_dynamic_context_parts` entirely when `stable_only=True` |

## Detailed Comments

### 1. `__init__.py` — Set package context for top-level imports (P1)

**Codex comment (2026-06-12):**
> When pytest (or any file-based loader) imports the repository root `__init__.py` as the top-level module `__init__`, `__package__` is an empty string rather than `None`, so this guard does not run and the first relative import immediately fails with `ImportError: attempted relative import with no known parent package`; I reproduced this by running `python -m pytest -q` from the repo root, which fails collection for the entire suite. Treat the empty-string case the same as `None` before executing the relative imports.

**Line:** 33-34
**Snippet:**
```python
if __package__ is None:
    __package__ = "mem_reflection_hermes"
```

**Expected fix:** `if __package__ is None or __package__ == "":`

---

### 2. `runtime/hooks.py` — Avoid waiting for the timed-out context worker (P2)

**Codex comment (2026-06-12):**
> In the timeout path, exiting the `with ThreadPoolExecutor(...)` block still performs the context manager shutdown with `wait=True` (per the Python docs), so if `build_context_bundle` hangs or simply exceeds `recall_timeout_ms`, `_pre_llm_call` blocks until the slow worker finishes before it can build the stable-only fallback. This defeats the configured recall timeout in exactly the slow-retrieval scenario this code is trying to protect.

**Line:** ~170
**Snippet:**
```python
with ThreadPoolExecutor(max_workers=1, thread_name_prefix="srh-context") as executor:
    future = executor.submit(_assemble_full)
    bundle = future.result(timeout=timeout_ms / 1000.0)
```

**Expected fix:** Use explicit executor creation and `shutdown(wait=False)` on timeout, or avoid context-manager shutdown semantics.

---

### 3. `core/search.py` — Filter before truncating BM25 candidates (P2)

**Codex comment (2026-06-11):**
> When `filters` is provided, this builds `active_map` after `top_idx` has already been limited to the global top `k` scores, so scoped searches can silently drop matching memories that rank below unscoped memories. In a multi-user or run-scoped store with many higher-scoring memories outside the requested scope, `srh_memory_search(..., filters=...)` may return too few or no results even though relevant in-scope memories exist; apply the filter before selecting the top candidates or over-fetch until enough scoped hits are found.

**Line:** ~459
**Snippet:**
```python
indexed.sort(reverse=True)
top_idx = [idx for _, idx in indexed[:k]]
results: Dict[str, float] = {}
list_kwargs: Dict[str, Any] = {"active_only": True}
if filters:
    list_kwargs["filters"] = filters
active_map = {m.id(): m for m in self.store.list(**list_kwargs)}
```

**Expected fix:** Filter candidates by scope before selecting top-k, or over-fetch then filter.

---

### 4. `core/search.py` — Filter embeddings before taking top-k (P2)

**Codex comment (2026-06-12):**
> With scope filters enabled, this chooses the global top `k` embedding matches and only then drops IDs outside the requested scope, so a scoped search can return too few or zero embedding candidates whenever more than `k` better-scoring memories exist in other users/agents/runs. For example, with `k=5` and a matching memory ranked 6th globally but 1st within `user_id='u1'`, it is discarded here and never participates in fusion, making scoped recall depend on unrelated out-of-scope memories.

**Line:** ~379
**Snippet:**
```python
top_idx = np.argpartition(-scores, min(k, len(scores) - 1))[:k]
return {
    self._embed_ids[i]: float(scores[i])
    for i in top_idx
    if allowed_ids is None or self._embed_ids[i] in allowed_ids
}
```

**Expected fix:** Compute allowed_ids first, then select top-k only from allowed_ids (or mask and partition).

---

### 5. `core/store.py` — Run delete callbacks for filtered deletes (P2)

**Codex comment (2026-06-11):**
> This batch delete path removes memory rows without invoking `_post_delete_callbacks`, unlike `delete()`, so the graph cleanup callback registered in `runtime/registration.py` never runs when `srh_memory_delete` is called with `filters`. In scoped batch-deletion workflows, deleted memories can remain in `graph.db` as stale nodes/edges and later graph retrieval/stat views can surface references to memories that no longer exist.

**Line:** ~1004
**Snippet:**
```python
conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
deleted += 1
```

**Expected fix:** After deleting each memory (or batch), call registered `_post_delete_callbacks` with the memory ID.

---

### 6. `memory/context.py` — Respect stable_only when building dynamic context (P2)

**Codex comment (2026-06-11):**
> Even when `stable_only=True`, this still calls `_build_dynamic_context_parts`, which unconditionally tries to add compacted episode summaries from the store. If the fallback path requests stable-only context while compacted episode memories exist, dynamic episode content is still injected, defeating the stable-only/timeout fallback contract documented above; skip this call or pass enough state so the dynamic builder omits all dynamic sections in stable-only mode.

**Line:** ~191
**Snippet:**
```python
dynamic_parts, compression_level, dropped_labels, included_labels = _build_dynamic_context_parts(
    active=active,
    triggered=triggered,
    store=store,
    budget=remaining_budget,
    compression_enabled=compression_enabled,
)
```

**Expected fix:** When `stable_only=True`, skip `_build_dynamic_context_parts` entirely and set dynamic_parts empty.

---

## Methodology

Following `systematic-debugging` skill:

1. **Phase 1 — Root Cause Investigation**
   - Reproduce each issue where possible.
   - Read error messages / code paths carefully.
   - Trace data flow to identify the exact failure point.
   - Run 6-layer pitfall checklist.

2. **Phase 2 — Pattern Analysis**
   - Compare broken path against working patterns elsewhere in the codebase.
   - Identify consistent vs. one-off issues.

3. **Phase 3 — Hypothesis and Testing**
   - Form single hypothesis per issue.
   - Write regression test first (RED).
   - Apply minimal fix (GREEN).

4. **Phase 4 — Implementation**
   - One fix at a time.
   - Run targeted test, then full suite.
   - Update this tracker with root cause and fix summary.

## Verification

- [x] All 6 issues investigated and root-caused
- [x] Regression tests added for each issue
- [x] Targeted tests pass
- [x] Full suite `pytest tests/ -q` passes: **561 passed**
- [x] Full suite `python -m pytest -q` passes: **561 passed**
- [x] Smoke script `python scripts/smoke_host_contract.py` passes: **37 passed, 0 failed**

### Files Changed

| File | Change |
|------|--------|
| `__init__.py` | Treat empty `__package__` like `None` for top-level imports |
| `runtime/hooks.py` | Explicit ThreadPoolExecutor with `shutdown(wait=False)` on timeout |
| `core/search.py` | Mask out-of-scope IDs before top-k in `_bm25_search_bm25s` and `_embed_search` |
| `core/store.py` | Invoke `_post_delete_callbacks` after `delete_by_filters` commits |
| `memory/context.py` | Skip `_build_dynamic_context_parts` when `stable_only=True` |

### Tests Added

| Test File | Test(s) |
|-----------|---------|
| `tests/test_runtime_import_hygiene.py` | `test_top_level_init_loads_with_empty_package` |
| `tests/test_hooks.py` | `TestContextTimeoutFallback` (2 tests) |
| `tests/test_scope_filters.py` | `test_bm25_filter_applied_before_topk_truncation`, `test_embedding_filter_applied_before_topk_truncation`, `test_delete_by_filters_invokes_post_delete_callbacks` |
| `tests/test_context.py` | `test_context_bundle_stable_only_omits_compacted_episodes` |
