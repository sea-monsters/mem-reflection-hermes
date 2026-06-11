# v1.5 Round 1 Code Review

**Date**: 2026-06-11
**Scope**: current `mem-reflection-hermes` plugin implementation, compared with `D:\Codex_lib\code_reference\mem0`, `D:\Codex_lib\code_reference\hy-memory`, and `D:\Codex_lib\code_reference\graphiti`.
**Review focus**: function intent, functional logic, structure/framework, and code implementation.
**Conclusion**: At the time of review, v1.5 had a useful local-first memory substrate and a clearer package layout than earlier versions, but it was not yet a complete peer of the three reference systems. The main gap was not only missing advanced features; several runtime paths still pointed at pre-refactor module names, so important lifecycle behavior could silently degrade in real host sessions.

**Post-review note (2026-06-11)**: the stale runtime import-path issue and the `core/store.py` import-time warning described below were later remediated in the working tree. The current verified suite is `510 passed`.

## 1. Current Implementation Snapshot

The plugin currently implements:

- Local Markdown + SQLite memory persistence through `core/store.py`.
- Hybrid retrieval through `core/search.py`: active-memory filtering, BM25, optional embedding, RRF/weighted fusion, entity boost, recency/effectiveness/supersedes factors, optional reranker, MMR, and explain output.
- Associative graph memory through `core/graph.py`: co-activation edges, spreading activation, decay, PageRank, cross-zone analysis, and heuristic distillation.
- Reflection through `reflection/runtime.py`: full/session reflection, micro reflection, raw-chunk storage, skill candidates, supersedes/conflict handling, and episode compaction.
- Context assembly through `memory/context.py`: stable/dynamic split, pinned memories, relevant memories, triggered skills, compacted episodes, and graded compression.
- Curator package through `memory/curator/`: stale archive, chain compaction, superseded archive, near-duplicate merge, orphan-edge cleanup, and report generation.
- Host surface through `runtime/registration.py`, `runtime/tools.py`, `runtime/hooks.py`, `runtime/graph.py`, and `runtime/schemas.py`.

The shape is coherent for a self-contained Hermes plugin, but parts of the implementation still reflect the older flat module layout and older tool/schema names.

## 2. Reference Comparison

### 2.1 Compared With mem0

mem0's core memory path is built around explicit add/update/delete/search semantics, provider factories, vector-store abstraction, history tables, scoped filters (`user_id`, `agent_id`, `run_id`), entity side stores, metadata preservation, and telemetry-safe config handling.

Current plugin strengths relative to mem0:

- Better local inspectability: Markdown frontmatter plus SQLite index is easy to audit and repair.
- Stronger agent-specific organization: memory zones, pinned memories, context bundles, skills, and session reflection are first-class.
- Lower dependency baseline: BM25 and regex-first entity extraction work without external vector infrastructure.
- Supersedes lineage is visible in the memory model instead of only an update history table.

Current gaps relative to mem0:

- No true pluggable vector-store abstraction. The plugin has local SQLite + optional embedding arrays, but not the provider boundary mem0 has for Qdrant, Chroma, Redis, PgVector, OpenSearch, etc.
- No first-class scoped filters equivalent to mem0's `user_id` / `agent_id` / `run_id` contract. `scope=user/project` and `zone` are useful, but they are not enough for multi-agent, multi-run, or hosted service isolation.
- Update semantics are weaker. `srh_memory_write` can supersede, and `MemoryStore.update()` can mutate, but there is no public `update_memory`-style API with durable history records for each ADD/UPDATE/DELETE event.
- Entity handling is local and helpful, but lacks mem0's separate entity vector collection and linked-memory maintenance semantics.
- Import/export and migration are partial compared with mem0 plugin's onboarding, coding categories, project switching, portable export/import, and competing-tool import paths.

### 2.2 Compared With hy-memory

hy-memory's design intent is strongly layered: L0 conversation, L1 atom, L2 scenario, L3 persona, plus short-term context offload to lightweight Mermaid/task-symbol artifacts with drill-down references to raw evidence.

Current plugin strengths relative to hy-memory:

- It already has a useful stable/dynamic context split, compression levels, raw episode chunking, compaction, and profile compilation hooks.
- It keeps local files readable and has a dashboard/API surface for inspection.
- The plugin is simpler to deploy because it does not require a gateway/offload service for the base path.

Current gaps relative to hy-memory:

- The plugin's memory hierarchy is mostly zone-based, not evidence-layer based. `episode`, `semantic`, `core`, `work`, and `general` are categories, but they do not form a strict L0 -> L1 -> L2 -> L3 provenance chain.
- Episode compaction can supersede raw entries, but the current summaries do not carry a rich, queryable drill-down index from high-level persona/scenario back to exact raw conversation/tool evidence.
- No Mermaid/task-canvas style short-term context offload. The plugin compresses context text, but it does not externalize heavy tool logs into refs and inject a compact symbolic map.
- No explicit warm-up / idle-time / interval pipeline scheduling comparable to hy-memory's staged extraction and persona triggers.
- Skill generation exists as pending candidates, but the layered "trace -> pattern -> skill/SOP" path is not structurally implemented.

### 2.3 Compared With graphiti

Graphiti is a temporal context graph engine: episodes are provenance, entities and facts are typed graph nodes/edges, facts have validity windows, ingestion is incremental, retrieval is hybrid, and old facts are invalidated rather than deleted.

Current plugin strengths relative to graphiti:

- It has useful associative graph behavior for agent memory: co-occurrence, spreading activation, PageRank, decay, and graph-expanded recall.
- It already models version lineage through `supersedes`, `valid_from`, `valid_until`, and `context_scope`.
- It is much lighter than Graphiti and can run as a local plugin without Neo4j/FalkorDB/Neptune.

Current gaps relative to graphiti:

- The graph is associative, not a temporal knowledge graph. Edges mean "used together", not typed facts such as entity-relation-entity.
- There is no prescribed or learned ontology for entity/edge types.
- Provenance is weak. Memories and graph edges do not consistently trace back to episodes, source spans, or extraction evidence.
- Temporal truth handling is shallow. `valid_until` and `supersedes` exist, but there is no automatic fact invalidation pipeline equivalent to Graphiti's temporal edge lifecycle.
- Graph retrieval is supplemental, while Graphiti's graph is the core data model and query substrate.

## 3. Critical Implementation Findings

### P0-1 (historical): v1.5 refactor left stale runtime import paths

`runtime/hooks.py` still imports `.runtime_reflection`, `.memory_curator`, and `.memory_bridge`; `runtime/tools.py` imports `.memory_bridge` and `.store` in several runtime paths. Those modules do not exist under `runtime/` after the v1.5 functional split. The failures are mostly swallowed by broad `try/except`, so the host can appear healthy while session-end compaction, curator execution, bridge sync, or profile compilation are skipped.

Observed examples:

- `runtime/hooks.py` session-start recovery imports `.runtime_reflection` and `.memory_curator`.
- `runtime/hooks.py` session-end compaction and curator imports the same stale modules.
- `runtime/hooks.py` post-tool bridge imports `.memory_bridge`.
- `runtime/tools.py` write bridge imports `.memory_bridge`.
- `runtime/tools.py` profile compilation imports `.store`.

Expected targets are `..reflection.runtime`, `..memory.curator`, `..memory.bridge`, and `..core.store`.

Impact: real lifecycle behavior is under-validated and can silently fail in the exact path where v1.5 claims memory evolution/curation.

### P0-2: Tool schema and handler behavior are inconsistent

`runtime/schemas.py` and `runtime/tools.py` disagree with each other and with `docs/TOOLS.md`:

- `srh_memory_search` handler supports `explain`; schema omits it.
- `srh_memory_write` handler supports `supersedes_reason`; schema omits it.
- `srh_compile_profile` handler supports `profile`, `palace_index`, and `zone`; schema advertises `profile`, `summary`, and `stats`.
- `docs/TOOLS.md` repeats the `summary/stats` profile modes even though the handler rejects them.

Impact: host-side tool validation can block supported handler paths or advertise modes that fail at runtime.

### P0-3: Hook validation is not clean enough to support lifecycle claims

Validated:

- `python -m pytest tests/test_host_contract_smoke.py -q` passed.
- `python -m pytest tests/test_curator_pipeline.py -q` passed.

Not clean:

- `python -m pytest tests/test_hooks.py -q` printed initial progress and then did not exit within the observation window; it had to be stopped by ending the only matching Python process.

This does not prove a production hang by itself, but it reinforces that the current hook tests are not giving crisp feedback on lifecycle behavior. Given P0-1, the lifecycle surface needs targeted tests that execute the actual package import paths.

## 4. Functional Logic Gaps

### Memory lifecycle

The plugin has add/search/delete/supersedes/update primitives, but it does not yet have mem0-grade durable event history or full user/agent/run filtering. `MemoryStore.update()` mutates the current file and index row; lineage only appears when the caller intentionally uses `supersedes`.

Recommended next step: add an explicit memory event ledger for ADD/UPDATE/DELETE/SUPERSEDE, then make `srh_memory_write` and future update tools record it.

### Retrieval

Hybrid retrieval is a strong part of the implementation. The main gaps are operational:

- vector backends are not pluggable;
- embedding model loading is local and ad hoc;
- graph results are supplemental rather than deeply integrated with typed facts;
- explainability exists but the schema does not expose `explain`.

Recommended next step: first fix schema exposure and score explanation contract, then consider a backend interface only if remote/vector-store use becomes a real requirement.

### Reflection and curation

Reflection is practical and conservative, which fits an agent plugin. The curation refactor improves structure, but the previous Sprint 1 test review remains valid: many tests verify symbol presence or key existence rather than behavioral intent.

Recommended next step: before adding new curator behavior, address the P0/P1 tests listed in `docs/dev/1.5/sprint1-test-review.md`, especially pipeline ordering, error isolation, legacy wrapper behavior, cold-store direct tests, and report generation.

### Graph memory

The graph implementation is useful as associative recall. It should not be described as a Graphiti-like temporal context graph. Current claims should keep the distinction explicit: co-activation graph now; typed temporal fact graph is a future track.

Recommended next step: if Graphiti parity is desired, introduce a separate `facts` or `knowledge_graph` layer rather than overloading Hebbian edges with factual meaning.

### Context and offload

The stable/dynamic context bundle is solid for prompt-cache friendliness and budget pressure. The gap versus hy-memory is provenance-preserving offload: compact symbolic state plus exact drill-down refs.

Recommended next step: add a lightweight `episode_refs`/`offload_refs` path only after the stale import bug is fixed, because compaction currently depends on affected hook paths.

## 5. Structural Assessment

Good structure:

- Functional packages (`core`, `reflection`, `memory`, `runtime`, `web`) are the right direction.
- `memory/curator/` action classes are clearer than a monolithic curator file.
- `runtime/registration.py` centralizes the official host registration path.
- `core/models.py`, `core/tokenization.py`, `core/entities.py`, and related splits make `core/store.py` less overloaded than before.

Structural risks:

- `runtime/tools.py` still contains both handler implementation and an older self-registration function. The official path uses `runtime/registration.py`, but the stale registration block increases drift.
- `runtime/hooks.py` has too much responsibility: context injection, micro-reflection triggering, checkpoint recovery, graph enrichment, bridge sync, compaction, curator, slash commands, and telemetry.
- Direct-load fallback logic remains scattered in production modules. Some fallbacks are useful for tests, but they also hide packaging mistakes.
- Documentation still references older file names (`runtime_hooks.py`, `runtime_reflection.py`, `memory_curator.py`) in places.

## 6. Priority Plan

The plan below is the review-time backlog. Its P0 runtime-import issue and related store warning were later fixed in the working tree.

P0:

1. Fix stale imports in `runtime/hooks.py` and `runtime/tools.py`.
2. Add regression tests that fail on stale module names and execute session-end compaction/curator and bridge paths through package imports.
3. Align `runtime/schemas.py`, `runtime/tools.py`, `docs/TOOLS.md`, and `plugin.yaml`.
4. Re-run host smoke plus hook tests and make the hook suite finish reliably.

P1:

1. Complete curator test gaps from `sprint1-test-review.md`.
2. Add memory event history for update/delete/supersede behavior.
3. Add import/export review against mem0 plugin's operational surface.
4. Make graph docs consistently say associative graph, not typed temporal context graph.

P2:

1. Evaluate a pluggable backend boundary only after local behavior is stable.
2. Prototype hy-memory-style offload refs for long tool logs.
3. Consider a separate typed fact graph track inspired by Graphiti if the product goal moves beyond associative recall.

## 7. Review Verdict

v1.5 is a credible local memory plugin foundation, especially for readable storage, hybrid retrieval, session reflection, and associative recall. Compared with mem0, it lacks provider-grade memory operations and scoped API discipline. Compared with hy-memory, it lacks layered evidence/provenance and symbolic offload. Compared with Graphiti, it lacks typed temporal fact modeling.

The immediate release blocker is narrower and more concrete: fix the stale runtime imports and schema drift before treating the v1.5 refactor as operationally closed.

## 8. Validation Performed

- `python -m pytest tests/test_host_contract_smoke.py -q` — passed.
- `python -m pytest tests/test_curator_pipeline.py -q` — passed.
- `python -m pytest tests/test_hooks.py -q` — did not finish cleanly in this review window; stopped the matching Python process after it remained running.
