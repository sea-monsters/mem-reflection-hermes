# v1.4 SDD: Search Retrieval Enhancement

**Version**: v1.4  
**Date**: 2026-06-09  
**Status**: Completed  
**Scope**: Phase 2 implementation for configurable CJK tokenization, entity recall, explainable retrieval signals, and backend capability abstraction

## 1. Purpose

This SDD defines the second implementation slice of v1.4 for `mem-reflection-hermes`.

The goal of this slice is to improve retrieval quality for Chinese-heavy queries and make ranking decisions observable without breaking the current search API.

## 2. Problem

The current retrieval pipeline had three practical gaps:

1. CJK tokenization depends on a non-overlapping bigram heuristic, which is stable but weak for compound Chinese search phrases.
2. Search results expose only ordered memories, so BM25, embedding, recency, effectiveness, supersedes, and Hebbian contributions cannot be audited during tuning.
3. Proper names, file paths, package identifiers, and code-like entities had no dedicated recall path beyond generic token overlap.

## 3. Design Goals

- Keep `_tokenise()` as the single tokenizer entrypoint for both index and query paths.
- Introduce configurable CJK tokenization with safe fallback behavior.
- Preserve `SearchIndex.search()` and `MemoryStore.fusion_search()` return types.
- Add a parallel explain path that exposes score components without forcing downstream callers to migrate.
- Surface explain data through `srh_memory_search` behind an opt-in flag.
- Add an entity recall layer that can boost weak BM25 / embedding matches for exact entities.
- Expose backend capability metadata for future hybrid backends without changing default behavior.

## 4. Non-Goals

- No multi-backend migration beyond the current SQLite/Markdown stack.
- No dashboard UI rewrite in this slice.
- No replacement of the existing ranking pipeline.

## 5. Proposed Design

### 5.1 Configurable CJK Tokenization

Add search-level config:

```yaml
plugins:
  mem_reflection_hermes:
    search:
      cjk_tokenizer: auto | bigram | jieba
```

Behavior:

- `bigram`: preserve current non-overlapping bigram behavior.
- `jieba`: use `jieba.cut_for_search` when available, otherwise fail open to bigram.
- `auto`: prefer jieba search-mode if available, otherwise bigram.

Implementation stays in `core/store.py` so BM25 fallback and `bm25s` index paths continue to share one tokenizer source of truth.

### 5.2 Explainable Retrieval

Add a parallel API in `core/search.py`:

```python
SearchIndex.search_explain(...)
```

and a store-facing wrapper:

```python
MemoryStore.fusion_search_explain(...)
```

Explain payload records per-result components:

- `embedding_rank`, `embedding_score`
- `bm25_rank`, `bm25_score`
- `rrf_score` or `weighted_base_score`
- `recency_factor`
- `effectiveness_factor`
- `supersedes_factor`
- `hebbian_boost`
- `final_score`
- `final_rank`

The default `search()` / `fusion_search()` methods remain unchanged and keep returning `LoadedMemory` lists.

### 5.3 Entity Recall

Add SQLite tables and helpers in `core/store.py`:

- `entities`
- `entity_links`

Write path:

- memory put/update rebuilds entity links from regex-based extraction
- optional spaCy extraction augments regex results when available
- delete/prune/rebuild cleanup orphan links and entities

Search path:

- query entities are extracted before rerank
- matching memories get additive `entity_boost`
- explain payload includes `entity_hits`

### 5.4 Tool Exposure

`runtime/tools.py::srh_memory_search` adds:

```json
{ "explain": true }
```

When enabled, each result includes an `explain` object and the tool response includes `meta`.

### 5.5 Backend Capability Abstraction

Add `core/backend.py` with:

- `SearchBackendCapabilities`
- `default_sqlite_backend_capabilities(...)`

The current SQLite/Markdown backend reports partial capabilities only:

- no native hybrid short-circuit
- keyword search available
- entity search available
- vector search depends on embeddings

## 6. Files Affected

- `core/store.py`
- `core/search.py`
- `core/backend.py`
- `runtime/tools.py`
- `tests/test_bm25.py`
- `tests/test_search.py`
- `tests/test_backend.py`
- `docs/dev/1.4/DEVELOPMENT_PROGRESS.md`

## 7. Acceptance Criteria

- Chinese-heavy queries can use jieba search-mode tokens when the dependency is available.
- No-jieba environments still pass tokenization tests via fallback behavior.
- Default search APIs remain backward compatible.
- Explain payload exists for opt-in searches and exposes reproducible score components.
- Entity links are maintained across put/delete/rebuild flows.
- Exact entity queries can produce `entity_boost` and `entity_hits`.
- Backend-capability interface exists and does not alter default search behavior.
- Existing context/hook regressions stay green after the retrieval change.

## 8. Progress Notes

- 2026-06-09: SDD created.
- 2026-06-09: Added configurable CJK tokenizer helpers with `auto`, `bigram`, and `jieba` modes.
- 2026-06-09: Preserved `_tokenise()` as the shared tokenizer entrypoint for indexing and query tokenization.
- 2026-06-09: Added `SearchIndex.search_explain()` and `MemoryStore.fusion_search_explain()`.
- 2026-06-09: Added `srh_memory_search.explain` tool flag and result metadata plumbing.
- 2026-06-09: Added entity extraction, entity/entity_links schema, and additive entity boost in rerank.
- 2026-06-09: Added backend capability abstraction for future hybrid backends.
- 2026-06-09: Targeted verification passed on BM25, search, backend, reflection, context, checkpoint, config, and host smoke suites.
- 2026-06-09: Scope completed and aligned with the v1.4 progress ledger.
