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
│       ├── __init__.py              # Registration, exports, bootstrap (~1,588 lines)
│       ├── core.py                  # MemoryStore, SkillStore, models (~791 lines)
│       ├── embed.py                 # ONNX embedding engine (~484 lines)
│       ├── reflection.py            # Micro/full reflection, rebalance (~1,248 lines)
│       ├── hooks.py                 # Session hooks (~335 lines)
│       ├── tools.py                 # 17 SRH tool handlers (~945 lines)
│       ├── plugin.yaml              # Plugin manifest
│       ├── README.md                # Index document
│       ├── bench_latency.py         # Performance benchmark
│       ├── PERF_REPORT.md           # Optimization report
│       └── dashboard/
│           ├── plugin_api.py        # FastAPI routes (12 endpoints)
│           ├── dist/index.js        # React frontend (Memory Manager)
│           └── manifest.json         # Dashboard tab registration
└── models/                          # ONNX model (optional)
    └── all-MiniLM-L6-v2-onnx/
        ├── model.onnx
        └── tokenizer.json
```
