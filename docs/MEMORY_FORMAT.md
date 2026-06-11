# Memory Format

Memories are stored as plain Markdown files with YAML frontmatter:

```markdown
---
id: mem_abc123
created: 2024-01-15T10:30:00Z
source: micro_reflection
confidence: high
pinned: false
tags:
  - python
  - rust
zone: general
rank: 0
supersedes: []
supersedes_reason: ""
valid_from: null
valid_until: null
context_scope: null
version: "1.0"
---

Always use anyhow for app-level error handling in Rust.
```

## Frontmatter Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier (auto-generated) |
| `created` | ISO 8601 | Creation timestamp |
| `source` | string | Origin: `micro_reflection`, `full_reflection`, `manual`, etc. |
| `confidence` | enum | `low` \| `medium` \| `high` |
| `pinned` | boolean | Whether to always inject into context |
| `tags` | string[] | Searchable tags |
| `zone` | string | Memory Palace zone: `core`, `work`, `episode`, `general`, `project:*` |
| `rank` | int (v0.6.1+) | Explicit ordering. Higher rank = appears earlier. Default 0 |
| `supersedes` | string[] | Memory IDs this memory replaces (version lineage) |
| `version` | string (v0.8.0+) | Optional version string for tracking iterations |
| `supersedes_reason` | string | Human-readable reason why this memory supersedes the referenced IDs |
| `valid_from` | ISO 8601 | Earliest date this memory is considered active |
| `valid_until` | ISO 8601 | Expiration date; memories past this date are flagged as expired in health checks |
| `context_scope` | string | Context qualifier (e.g., `project:X`, `domain:backend`) for scoped filtering |
| `user_id` | string (v1.6) | User identifier for memory event ledger attribution |
| `agent_id` | string (v1.6) | Agent identifier for memory event ledger attribution |
| `run_id` | string (v1.6) | Run/session identifier for memory event ledger attribution |

## Supersedes Semantics

Use `supersedes` for semantic replacement, not for every related or later
memory. A new memory should supersede an older memory when it is the current
version of the same claim.

Good uses:

- A corrected preference replaces an older preference.
- A newer project convention replaces an older convention for the same scope.
- A consolidated memory intentionally merges several older duplicate memories.

Avoid using `supersedes` when:

- two memories are both true in different projects or zones
- the older memory is a historical episode rather than a stale claim
- the relationship is merely associative; use the graph layer for that

See [DESIGN_EVALUATION.md](DESIGN_EVALUATION.md) for the design rationale and
limits of supersedes chains versus temporal knowledge graphs.

## Reflection Log Format

The reflection pipeline writes to `~/.hermes/plugins/mem-reflection-hermes/reflect-log.jsonl`.
Each line is a JSON object:

```json
{
  "timestamp": "2026-06-01T12:00:00+00:00",
  "mode": "embedding",
  "summary": "Session novelty: 0.75...",
  "skill_candidates": 0,
  "memory_candidates": 2,
  "accepted_memories": 1,
  "conflicts": 0,
  "novelty": 0.75,
  "audit_entries": [
    {
      "candidate_id": "cand_abc123",
      "decision": "accepted",
      "decision_reason": "novelty sufficient, no conflict",
      "novelty_score": 0.75,
      "conflict_id": "",
      "supersedes_ids": [],
      "supersedes_reason": "",
      "assigned_zone": "episode",
      "graph_migration": {}
    }
  ]
}
```

Audit entries are written by all reflection modes (`full_llm`, `micro_llm`,
`embedding`, `embedding_micro`). Older entries without `audit_entries` remain
valid and readable.

## File Structure

```
~/.hermes/
├── memory/
│   ├── memories/                     # User-level memories
│   │   └── 2024-01-15-mem_abc12.md
│   ├── skills/                       # User-level skills
│   │   └── rust-error-handling/
│   │       └── SKILL.md
│   ├── zone-cache/                   # Per-zone summary caches
│   ├── palace-index.md              # Palace zone index
│   ├── memory-stats.jsonl           # Effectiveness tracking
│   └── cold_store.jsonl             # Curator cold storage archive (v1.2)
├── plugins/
│   └── mem-reflection-hermes/
│       ├── __init__.py              # Plugin registration and runtime singletons
│       ├── plugin.yaml              # Plugin manifest
│       ├── README.md                # Index document
│       ├── core/                    # Storage, search, graph
│       │   ├── store.py             # MemoryStore, SkillStore, frontmatter, lineage
│       │   ├── store_methods.py     # Method bodies (entity boosts, etc.)
│       │   ├── models.py            # MemoryFrontmatter, SkillFrontmatter
│       │   ├── utils.py             # normalize_zone, sanitize_zone_filename
│       │   ├── search.py            # SearchIndex, BM25/embedding fusion
│       │   ├── graph.py             # GraphIndex, PageRank, cross-zone analysis
│       │   ├── config.py            # Typed config models (v1.4)
│       │   ├── backend.py           # Backend capability protocol (v1.4)
│       │   ├── entities.py          # Entity extraction (v1.4)
│       │   ├── tokenization.py      # CJK-aware tokenizer
│       │   ├── skill_store.py       # SkillStore implementation
│       │   ├── lineage.py           # Supersedes chain helpers
│       │   ├── intent.py            # Intent classification
│       │   ├── reranker.py          # Second-stage reranker
│       │   ├── async_writer.py      # Background file I/O (v1.4)
│       │   └── store_health.py      # Store health checks
│       ├── reflection/              # Reflection engine and runtime
│       │   ├── engine.py            # ReflectionEngine, fact extraction
│       │   └── runtime.py           # Full/micro reflection, audit, compaction
│       ├── memory/                  # Curation, bridge, context assembly
│       │   ├── curator/             # Composable action pipeline (v1.3+)
│       │   ├── bridge.py            # Bidirectional host memory sync (v1.1)
│       │   └── context.py           # 4-layer context assembly
│       ├── runtime/                 # Tools and lifecycle hooks
│       │   ├── tools.py             # 8 base SRH tool handlers
│       │   ├── hooks.py             # Session hooks and slash commands
│       │   └── graph.py             # 5 graph/health tools + compat surface
│       ├── web/                     # FastAPI dashboard
│       │   └── api.py               # 15 API endpoints
│       ├── scripts/
│       │   ├── bench_latency.py     # Performance benchmark
│       │   ├── smoke_host_contract.py  # Host contract validation
│       │   └── migrate_memory_index.py # One-time index migration
│       ├── docs/                    # Documentation
│       │   ├── ARCHITECTURE.md
│       │   ├── CHANGELOG.md
│       │   ├── TOOLS.md
│       │   ├── DASHBOARD.md
│       │   ├── DATA_SAFETY.md
│       │   ├── MEMORY_FORMAT.md
│       │   └── testing/
│       │       └── test-coverage.md
│       └── dashboard/               # Dashboard frontend (built)
│           └── dist/
└── models/                          # ONNX model (optional)
    └── all-MiniLM-L6-v2-onnx/
        ├── model.onnx
        └── tokenizer.json
```
