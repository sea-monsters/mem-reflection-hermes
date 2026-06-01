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
│   └── memory-stats.jsonl           # Effectiveness tracking
├── plugins/
│   └── mem-reflection-hermes/
│       ├── __init__.py              # Registration, exports, graph tools, bootstrap (~1,416 lines)
│       ├── core.py                  # MemoryStore, SkillStore, models (~652 lines)
│       ├── search/
│       │   └── embed.py             # ONNX embedding engine (~411 lines)
│       ├── reflection/
│       │   └── engine.py            # Micro/full reflection, rebalance (~1,085 lines)
│       ├── hooks/
│       │   └── lifecycle.py         # Session hooks (~276 lines)
│       ├── tools/
│       │   └── handlers.py          # 12 SRH tool handlers (~830 lines)
│       ├── graph/
│       │   ├── ahe_graph/__init__.py # SQLite-backed graph memory (~687 lines)
│       │   ├── cluqi.py             # Cross-layer query orchestration (~217 lines)
│       │   ├── pagerank.py          # PageRank centrality (~81 lines)
│       │   └── cross_zone.py        # Cross-zone analysis (~112 lines)
│       ├── query/
│       │   └── cache.py             # TTL query cache (~163 lines)
│       ├── plugin.yaml              # Plugin manifest
│       ├── README.md                # Index document
│       ├── scripts/
│       │   └── bench_latency.py     # Performance benchmark
│       ├── PERF_REPORT.md           # Optimization report
│       └── dashboard/
│           ├── plugin_api.py        # FastAPI routes (13 endpoints)
│           ├── dist/index.js        # React frontend (Memory Manager)
│           └── manifest.json         # Dashboard tab registration
└── models/                          # ONNX model (optional)
    └── all-MiniLM-L6-v2-onnx/
        ├── model.onnx
        └── tokenizer.json
```
