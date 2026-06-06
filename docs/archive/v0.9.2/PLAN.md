# v0.9.2-beta2 Development Plan

Date: 2026-06-01

This plan turns the v0.9.2-beta design evaluation into a focused beta2
implementation track. The goal is not to add another retrieval algorithm. The
goal is to make the existing supersedes + associative graph + reflection system
more trustworthy as memory volume grows.

## Release Goal

v0.9.2-beta2 should close the design gaps found in
`docs/DESIGN_EVALUATION.md`:

- clarify and enforce `supersedes` semantics
- make default recall lineage-aware
- keep graph semantics honest and observable
- record reflection quality decisions
- add lightweight temporal/context hints without building a full temporal KG
- expose memory health metrics for operator review

## Non-Goals

- Do not replace the flat-file store with a vector DB or graph DB.
- Do not turn the Hebbian graph into an entity-relation knowledge graph.
- Do not add automatic extraction on every message pair.
- Do not remove human-auditable Markdown memory files.
- Do not widen scope into a large dashboard redesign.

## Priority Overview

| Priority | Theme | Outcome |
|---|---|---|
| P0 | Supersedes governance | Prevent duplicate lineage drift and ambiguous replacement semantics |
| P0 | Lineage-aware recall | Default recall prefers current active memory, while history remains inspectable |
| P1 | Reflection quality audit | Reflection writes explain why candidates were accepted, skipped, or superseded |
| P1 | Graph semantic labeling | UI/docs/tools consistently describe graph edges as associative/co-activation edges |
| P1 | Memory health metrics | Operators can see duplicate clusters, long chains, stale nodes, and graph drift |
| P2 | Lightweight temporal hints | Add optional fields for validity/context without adopting temporal KG complexity |
| P2 | Regression coverage | Codify host-plugin and memory-system smoke tests |

## Workstreams

### WS-1: Supersedes Governance

Problem:

`supersedes` is conceptually strong but can drift if reflection or manual writes
create parallel duplicates instead of replacement chains. The current system
needs clearer runtime policy, not just documentation.

Tasks:

1. Add a `supersedes_reason` optional frontmatter field.
2. Add a helper in `core.py` or `reflection.py` to classify update intent:
   - `replacement`
   - `correction`
   - `elaboration`
   - `scoped_exception`
   - `historical_episode`
3. Update `srh_memory_write` conflict response to recommend either:
   - pass `supersedes=[id]`
   - change `zone` / `scope`
   - keep as episode/history
4. Add validation when `supersedes` points to missing memory IDs.
5. Add a small lineage utility:
   - latest node lookup
   - chain root lookup
   - chain depth
   - cycle detection

Acceptance:

- Writing a memory with missing `supersedes` target returns a clear error.
- Writing a memory that supersedes an old one records `supersedes_reason` when provided.
- A cycle in a supersedes chain is detected and reported.
- `srh_memory_history` reports chain depth and latest/current status.

Files:

- `core.py`
- `tools.py`
- `reflection.py`
- `docs/MEMORY_FORMAT.md`
- `docs/TOOLS.md`

Validation:

- unit-style smoke for linear chain, missing ID, cycle guard
- `srh_memory_history` smoke

### WS-2: Lineage-Aware Recall

Problem:

Recall must not let stale superseded facts dominate default search. History is
valuable, but the default answer path should favor the latest active claim.

Tasks:

1. Add `MemoryStore.latest_for(memory_id)` and `MemoryStore.is_superseded(memory_id)`.
2. Update `MemoryStore.search()` / `fusion_search()` to:
   - hide superseded memories by default
   - optionally include superseded memories with `include_history=true`
3. Update CLUQI result scoring to boost latest active memory in a chain.
4. Update graph retrieval to mark superseded nodes as historical unless explicitly requested.
5. Add tool/schema option where useful:
   - `include_history`
   - `explain_lineage`

Acceptance:

- Default `srh_memory_search` returns latest memory, not old superseded memory.
- `srh_memory_history` still shows the full chain.
- CLUQI result metadata shows whether a result is current or historical.
- Graph neighbors can include historical nodes only when requested.

Files:

- `__init__.py`
- `core.py`
- `tools.py`
- `cluqi.py`
- `ahe_graph/__init__.py`
- `docs/TOOLS.md`

Validation:

- create A -> B supersedes chain
- search old term and verify B wins
- history tool returns A and B
- CLUQI reports `lineage_status=current`

### WS-3: Reflection Quality Audit

Problem:

Reflection quality determines memory quality. Current logs need stronger
operator evidence: why something was accepted, skipped, superseded, or zoned.

Tasks:

1. Extend reflect log entries with:
   - `candidate_id`
   - `decision`
   - `decision_reason`
   - `novelty_score`
   - `conflict_id`
   - `supersedes_ids`
   - `supersedes_reason`
   - `assigned_zone`
   - `graph_migration`
2. Update full reflection write path to record these fields.
3. Update micro-reflection path to record skipped/no-op decisions when useful.
4. Add dashboard/read API support for recent reflection decisions.
5. Document the log schema.

Acceptance:

- Reflection log can explain why a candidate did or did not become a memory.
- Supersedes decisions are visible in reflect log.
- Zone assignment is visible in reflect log.
- Existing log readers remain backward-compatible.

Files:

- `reflection.py`
- `hooks.py`
- `dashboard/plugin_api.py`
- `docs/DASHBOARD.md`
- `docs/MEMORY_FORMAT.md`

Validation:

- reflection smoke with accepted candidate
- reflection smoke with conflict/skipped candidate
- log parser smoke over old and new entries

### WS-4: Graph Semantic Boundary

Problem:

The graph layer is associative, not an entity-relation knowledge graph. UI,
tool descriptions, docs, and API fields should make this explicit.

Tasks:

1. Rename user-facing labels from ambiguous "graph memory" where needed to:
   - associative graph
   - co-activation graph
   - related memories
2. Add API metadata field:
   - `graph_semantics: "associative_coactivation"`
3. Update dashboard graph legend text.
4. Update tool descriptions for `srh_graph_retrieve` and `srh_graph_viz`.
5. Document that edges are suggestive, not factual or causal.

Acceptance:

- Docs and API do not imply entity-relation truth.
- Dashboard labels distinguish associative edges from `SUPERSEDES` structural edges.
- `srh_graph_viz` returns graph semantics metadata.

Files:

- `__init__.py`
- `dashboard/plugin_api.py`
- `docs/DASHBOARD.md`
- `docs/TOOLS.md`
- `docs/DESIGN_EVALUATION.md`

Validation:

- grep for risky phrases: `knowledge graph truth`, `causal`, `entity relation`
- dashboard API smoke checks metadata

### WS-5: Memory Health Metrics

Problem:

The system needs health signals before memory clutter becomes a hidden quality
problem.

Tasks:

1. Add health analyzer helper:
   - duplicate cluster count
   - longest supersedes chain
   - supersedes cycle count
   - orphan graph node count
   - stale high-rank memory count
   - graph edge density per zone
   - reflection candidate acceptance rate
2. Add `srh_memory_health` tool or extend `srh_graph_stats` with `tier=health`.
3. Add dashboard `/stats` health section.
4. Add recommendations:
   - consolidate duplicates
   - review long chains
   - prune stale graph edges
   - inspect noisy reflection zones

Acceptance:

- Health output works on an empty memory store.
- Health output identifies at least duplicate clusters and longest chain.
- Dashboard stats expose health metrics without breaking existing fields.

Files:

- `core.py`
- `tools.py`
- `dashboard/plugin_api.py`
- `docs/TOOLS.md`
- `docs/DASHBOARD.md`

Validation:

- empty store smoke
- duplicate fixture smoke
- supersedes chain fixture smoke

### WS-6: Lightweight Temporal and Context Hints

Problem:

The system should not become a full temporal KG, but it needs better support
for scoped or time-bounded facts.

Tasks:

1. Add optional frontmatter fields:
   - `valid_from`
   - `valid_until`
   - `context_scope`
   - `supersedes_reason`
2. Update parser/serializer to preserve these fields.
3. Add docs explaining:
   - use `valid_*` for time-bounded facts
   - use `context_scope` for project/persona/environment-specific facts
   - do not use `supersedes` for unrelated temporal episodes
4. Add recall metadata for expired or context-mismatched facts.

Acceptance:

- Existing memories without these fields load unchanged.
- New fields round-trip through parse/write.
- Expired facts can be marked or filtered in search metadata.

Files:

- `core.py`
- `docs/MEMORY_FORMAT.md`
- `tools.py`
- `cluqi.py`

Validation:

- frontmatter round-trip smoke
- expired memory recall metadata smoke

### WS-7: Regression and Host-Contract Coverage

Problem:

Recent review found host contract drift. beta2 should preserve this with
repeatable tests/smokes.

Tasks:

1. Add a small host simulation script or test fixture that validates:
   - package-level `register(ctx)` registers 17 tools
   - 4 hooks register
   - 8 slash commands register
   - `pre_llm_call` accepts Hermes current kwargs
   - `post_tool_call` accepts Hermes current kwargs
2. Add CLUQI graph smoke:
   - write two memories
   - associate them
   - query via CLUQI
   - inspect neighbors
3. Add frontmatter round-trip smoke for new fields.

Acceptance:

- All smokes run without network or external model.
- Smokes isolate `HERMES_HOME` in a temp directory.
- CI/local command is documented.

Files:

- `tests/` or `scripts/`
- `docs/DATA_SAFETY.md`
- `README.md`

Validation:

- `python -m py_compile ...`
- host contract smoke
- CLUQI smoke
- frontmatter round-trip smoke

## Proposed Sequence

1. WS-7 host-contract regression first, so every later change has a safety net.
2. WS-1 supersedes governance.
3. WS-2 lineage-aware recall.
4. WS-3 reflection quality audit.
5. WS-5 memory health metrics.
6. WS-4 graph semantic labels.
7. WS-6 temporal/context hints.

Reasoning:

- The biggest operational risk is regression in the host plugin contract.
- Supersedes semantics should land before recall changes so CLUQI/search can use stable helpers.
- Reflection audit should land before health metrics so health has evidence inputs.
- Temporal hints are valuable but should not delay the trust and recall fixes.

## Beta2 Acceptance Checklist

- [x] Package-level `register(ctx)` host smoke passes with 17 tools.
- [x] Default search and CLUQI prefer latest active memory in a supersedes chain.
- [x] `srh_memory_history` reports chain depth, current node, and cycle errors.
- [x] Reflection logs include decision reason, conflict ID, supersedes IDs, and zone.
- [x] Graph APIs identify themselves as associative/co-activation graph APIs.
- [x] Memory health output includes duplicate clusters and longest chain.
- [x] Optional temporal/context fields round-trip through frontmatter.
- [x] README, tool docs, memory format docs, dashboard docs, and changelog are updated.

## Risk Register

| Risk | Mitigation |
|---|---|
| Supersedes governance becomes too strict and blocks useful writes | Provide clear override path and error messages |
| Lineage filtering hides useful historical context | Add `include_history` and keep `srh_memory_history` explicit |
| Reflection audit creates noisy logs | Keep fields structured and optional; cap/rotate logs |
| Health metrics become expensive on large stores | Start with simple O(n) scans and document scale limits |
| Temporal hints are mistaken for full temporal KG | Label them as lightweight hints, not temporal reasoning guarantees |
