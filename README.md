# mem-reflection-hermes

Self-evolving memory & reflection system for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Ported from [small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes) with significant performance enhancements, a full-featured dashboard, and graph memory integration.

**Current version: v0.8.0** — 6-module architecture, 17 SRH tools, ahe_graph integration.

## Features

- **Structured Memories**: Markdown + YAML frontmatter (id, created, source, confidence, pinned, tags, supersedes, zone, rank, version)
- **Dual Scope**: User-level (`~/.hermes/memories/`) and project-level (`./.hermes/memories/`)
- **Memory Palace**: Zone-based organization (core, work, episode, general, project:*) with tool-driven navigation
- **TF-IDF / BM25 Search**: Pure Python implementation, zero external dependencies, ~0.8ms for 50 memories
- **Semantic Search**: ONNX Runtime + all-MiniLM-L6-v2, 16x faster than PyTorch (optional)
- **Conflict Detection**: Automatic similarity checking on write with supersedes chains and version lineage
- **Effectiveness Tracking**: Per-memory effectiveness scoring with exponential time decay
- **Micro-Reflection**: Per-turn background reflection with backpressure queue and CJK-aware token estimation
- **Full Reflection**: Session-end structured summary with human approval for skills
- **Skill Auto-Matching**: Token overlap + optional embedding hybrid for context injection
- **Context Layering**: Pinned → Active Index → Triggered Skills → Always-Active Skills
- **Profile Compilation**: LLM-driven compilation of all memories into structured profile documents
- **Graph Memory (ahe_graph)**: Associate memories, retrieve via graph traversal, stats, and visualization
- **Dashboard Memory Manager**: Full CRUD + reorder UI with search, zone filter, and sort controls

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                 Hermes Agent Session                  │
├──────────────────────────────────────────────────────┤
│  pre_llm_call hook                                    │
│    ├── Inject palace index (zone map)                 │
│    ├── Inject compiled profile                        │
│    ├── Inject triggered/always skills                 │
│    └── Inject pinned memories                         │
├──────────────────────────────────────────────────────┤
│  post_tool_call hook                                  │
│    ├── Record tool effectiveness                      │
│    ├── Update memory stats                            │
│    └── Build ahe_graph associations                   │
├──────────────────────────────────────────────────────┤
│  on_session_end hook                                  │
│    ├── Full reflection pipeline                       │
│    ├── Generate skill candidates                      │
│    └── Write session summary                          │
├──────────────────────────────────────────────────────┤
│  Background Tasks                                     │
│    ├── Micro-reflection queue (backpressure)          │
│    ├── Async memory write thread                      │
│    └── Async stat flush thread                        │
└──────────────────────────────────────────────────────┘
```

### Module Layout (v0.8.0)

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `core.py` | ~791 | MemoryStore, SkillStore, LoadedMemory, LoadedSkill, config, paths |
| `embed.py` | ~484 | ONNX embedding engine, cosine similarity, intent classification |
| `reflection.py` | ~1,248 | Micro/full reflection, auto-rebalance, profile compilation |
| `hooks.py` | ~335 | Session hooks (on_session_start/end, pre_llm_call, post_tool_call) |
| `tools.py` | ~945 | 17 SRH tool handlers exposed to Hermes Agent |
| `__init__.py` | ~1,588 | Registration, exports, backward compat, standalone bootstrap |

### v0.8.0 — Module Split + ahe_graph Integration
- **6-way module split**: `__init__.py` reduced from ~4,500 to ~1,588 lines; logic moved to `core`, `embed`, `reflection`, `hooks`, `tools`
- **17 SRH tools**: `srh_memory_search/write/delete/history`, `srh_skill_search`, `srh_reflect_now`, `srh_palace_zones/read_zone/recall/search/rebalance`, `srh_compile_profile`, `srh_associate/graph_retrieve/graph_stats/graph_viz`
- **ahe_graph integration**: Graph memory with `srh_associate`, `srh_graph_retrieve`, `srh_graph_stats`, `srh_graph_viz`
- **Backpressure queue**: Micro-reflection bounded queue (max 20) with overflow logging
- **CJK adaptive threshold**: Per-language similarity threshold (CJK 0.55, Latin 0.65)
- **LRU embed cache**: 128-entry LRU cache for embedding vectors
- **Zone rebalance**: Automatic zone split/merge with dry-run mode
- **Memory history**: `srh_memory_history` traces supersedes chains
- **Codex review fixes**: P0/P1/P2 issues resolved with audit script verification

### v0.7.0 — ahe_graph Deep Integration
- Graph memory layer with associate/retrieve/stats/viz tools
- `post_tool_call` hook builds tool result associations
- Intent classification via embedding similarity

### v0.6.1 — Atomic Store Refactor
- **Atomic `MemoryStore.update()`**: Single method handles file write, cache invalidation, and index rebuild. Prevents 5 classes of cache inconsistency bugs.
- **Atomic `MemoryStore.reorder()`**: Assigns explicit `rank` values instead of manipulating timestamps. Stable across filtering and sorting modes.
- **`rank` field**: New `rank: int = 0` in `MemoryFrontmatter`. Higher rank = earlier in default sort. Backward compatible.
- **Simplified HTTP layer**: `plugin_api.py` delegates all mutation logic to Store methods — HTTP layer only validates and translates errors.
- **Post-review fixes**: Plugin registration API compat (`__HERMES_PLUGINS__.register`), atomic write via `os.replace()`, reflections field name fix, scope validation, zone list refresh.
│    ├── /pending-skills                                │
│    ├── /approve-skill <id>                            │
│    ├── /reject-skill <id>                             │
│    ├── /reflect                                       │
│    └── /compile-profile                               │
├──────────────────────────────────────────────────────┤
│  Dashboard API (FastAPI)                              │
│    GET    /memories                    List all       │
│    POST   /memories                    Create         │
│    GET    /memories/{id}               Get one        │
│    PUT    /memories/{id}               Update (atomic)│
│    DELETE /memories/{id}               Delete         │
│    POST   /memories/reorder            Reorder (rank) │
│    GET    /zones                       List zones     │
│    GET    /graph                       Memory graph   │
│    GET    /skills                      Skill list     │
│    GET    /reflections                 Reflection log │
│    GET    /stats                       Aggregate stats│
└──────────────────────────────────────────────────────┘
```

## Performance

### v0.6.1 (50 memories, 10 skills, TF-IDF only, palace mode)

| Metric | v0.4.0 | v0.6.1 | Improvement | Mechanism |
|--------|--------|--------|-------------|-----------|
| Context Block (warm) | 1.74ms | 1.31ms | ↓ 25% | Write-on-change + index cache |
| Memory Write (agent perceived) | 11.69ms | 0.57ms | ↓ 95% | Async I/O (P2-2) |
| Memory Delete | 10.68ms | 0.14ms | ↓ 99% | O(1) id→path index (P0-2) |
| Token Estimation | 2.96ms | 0.6µs | ↓ 5000x | Byte-based fast estimation (P1-1) |
| Stat Flush | 206µs | 4.4µs | ↓ 98% | Async queue (P1-2) |
| Skill Search | 7µs | 3µs | ↓ 57% | SkillStore lazy cache |
| **Total Hot Path** | **76.9ms** | **34.0ms** | **↓ 56%** | |

> Full benchmark: `python bench_latency.py` (requires 50 test memories)

## Dashboard Memory Manager

The dashboard provides a full-featured UI for managing memories directly:

| Feature | Description |
|---------|-------------|
| **Search** | Real-time filter by content or tags |
| **Zone Filter** | Dropdown to show only memories from a specific zone |
| **Sort** | By rank (default), date, confidence, or zone |
| **Create** | `+ New Memory` button opens edit dialog |
| **Edit** | ✏️ button to modify content, zone, confidence, tags, pinned status |
| **Delete** | 🗑️ button with confirmation dialog |
| **Reorder** | ↑↓ buttons to move memories — persists via explicit `rank` field |

The dashboard communicates with MemoryStore through **atomic store methods** (`update()` and `reorder()`) that handle file I/O, cache invalidation, and index rebuilds in a single operation — preventing the cache inconsistency bugs common in earlier approaches.

**API Endpoints (FastAPI, mounted at `/api/plugins/mem-reflection-hermes/`):**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/memories` | List all active memories |
| `POST` | `/memories` | Create new memory |
| `GET` | `/memories/{id}` | Get single memory |
| `PUT` | `/memories/{id}` | **Atomic update** (write-then-delete swap, cache + index) |
| `DELETE` | `/memories/{id}` | Delete memory |
| `POST` | `/memories/reorder` | **Atomic reorder** via explicit `rank` assignment |
| `GET` | `/zones` | All zones with counts |
| `GET` | `/graph` | Memory graph (nodes + edges) |
| `GET` | `/skills` | All skills with metadata |
| `GET` | `/reflections` | Recent reflection outcomes |
| `GET` | `/stats` | Aggregate statistics (by scope, confidence, zone, tags) |

## Changelog

### v0.6.1 — Atomic Store Refactor
- **Atomic `MemoryStore.update()`**: Single method handles file write, cache invalidation, and index rebuild. Prevents 5 classes of cache inconsistency bugs.
- **Atomic `MemoryStore.reorder()`**: Assigns explicit `rank` values instead of manipulating timestamps. Stable across filtering and sorting modes.
- **`rank` field**: New `rank: int = 0` in `MemoryFrontmatter`. Higher rank = earlier in default sort. Backward compatible.
- **Simplified HTTP layer**: `plugin_api.py` delegates all mutation logic to Store methods — HTTP layer only validates and translates errors.
- **Post-review fixes**: Plugin registration API compat (`__HERMES_PLUGINS__.register`), atomic write via `os.replace()`, reflections field name fix, scope validation, zone list refresh.

### v0.6.0 — Dashboard Memory Manager + Bug Fixes
- **Dashboard Memory Manager**: Full CRUD UI with search, zone filter, sort controls, and reorder
- **6 API endpoints**: Create, read, update, delete, reorder, zones
- **Bug fix**: Duplicate memory scanning when `cwd` is `~` (user root == project root)
- **Bug fix**: Python 3.11 `importlib.util` + `@dataclass` compatibility (full fallback chain)
- Dashboard version: 1.0.0 → 1.1.1; path: `/memory-graph` → `/memory-manager`

### v0.5.0 — Performance Optimization Release
- **P0-1**: Palace index write-on-change — skip disk write when content hasn't changed
- **P0-2**: O(1) delete via id→path reverse index — eliminates directory scan
- **P1-1**: Fast byte-based token estimation — 5000x faster than char-by-char CJK check
- **P1-2**: Async stat flush — background thread for JSONL recording
- **P2-1**: Event-driven palace index rebuild — only rebuild on memory mutation
- **P2-2**: Async memory write — background thread for file I/O
- **SkillStore lazy cache**: One-time file read per session
- **build_palace_index cache**: Cached result string on MemoryStore
- Fix: `build_palace_index` sort key type error (int vs tuple comparison)

### v0.4.0 — Memory Palace + Profile
- Memory Palace zone-based organization with 3 navigation tools
- Effectiveness tracking with time decay
- LLM-compiled profile generation (profile.md, palace-index.md, zone-cache)
- Always-active skills, supersedes chains
- CJK-aware token estimation for context limits
- Configurable caps (memory/skill/trigger limits)

## Known Bugs & Fixes

### Python 3.11 `importlib.util` + `@dataclass` Loading Failure (Fixed in v0.6.0)

When Hermes loads the plugin via `importlib.util`, `@dataclass` definitions fail with `AttributeError: 'NoneType' object has no attribute '__dict__'`. **Fixed** with a full fallback chain that safely registers the module in `sys.modules` before any dataclass decorators execute:

```python
if __name__ != "__main__" and __name__ not in sys.modules:
    mod_name = getattr(__spec__, "name", None) if "__spec__" in globals() else None
    if mod_name is None:
        mod_name = __name__
    sys.modules[mod_name] = sys.modules.get(mod_name) or sys.modules.get(__name__) or types.ModuleType(mod_name)
```

### Duplicate Memory Scanning (Fixed in v0.6.0)

When `cwd` is `~`, `_project_memories_dir()` resolved to the same path as `_user_memories_dir()`, causing every memory to appear twice. **Fixed** with path resolution comparison.

### ONNX Fallback Cache Guard (Known, P3)

When ONNX is unavailable and sentence-transformers fallback is used, `_onnx_tokenizer` is set to `None`, causing every call to re-enter the critical section and re-load the model (~5s each). Impact: only when embeddings are enabled and ONNX model is unavailable.

## Data Safety Patterns

### Write-Then-Delete Swap
Mutations write the new file first, then delete the old file. If the write fails (disk full, permission error), the original file is preserved.

### Atomic In-Place Writes (v0.6.1)
When the new file path equals the old path (same-day updates, rank-only changes), the write goes to a `.tmp` file first, then `os.replace()` atomically swaps it — preventing file corruption on partial writes.

### Cache Consistency
`MemoryStore.update()` and `reorder()` call `_invalidate_cache()` before `_update_cache_for_put()` to prevent duplicate entries, and explicitly set `_index_dirty = True` + `_cached_index = ""` for same-path updates.

## Installation

### Prerequisites

- Python 3.10+
- Hermes Agent v3.2.2+
- ONNX Runtime (optional, for semantic search)

### Quick Start

```bash
# 1. Clone into Hermes plugins directory
cd ~/.hermes/plugins
git clone https://github.com/sea-monsters/mem-reflection-hermes.git

# 2. Enable in Hermes config
cat >> ~/.hermes/config.yaml << 'EOF'
plugins:
  enabled:
    - mem-reflection-hermes
  mem_reflection_hermes:
    embeddings: false         # TF-IDF only (fast, zero deps)
    micro_reflection: true    # Auto-reflect per turn
    palace_mode: true         # Memory Palace navigation
    profile_mode: false       # LLM-compiled profile injection
EOF

# 3. Restart Hermes Agent
hermes restart
```

### Optional: ONNX Model Setup

For best performance, use the ONNX model instead of sentence-transformers fallback:

```bash
pip install onnxruntime tokenizers

python -c "
from optimum.onnxruntime import ORTModelForFeatureExtraction
from transformers import AutoTokenizer
import os

model_id = 'sentence-transformers/all-MiniLM-L6-v2'
output_dir = os.path.expanduser('~/.hermes/models/all-MiniLM-L6-v2-onnx')

model = ORTModelForFeatureExtraction.from_pretrained(model_id, export=True)
tokenizer = AutoTokenizer.from_pretrained(model_id)

model.save_pretrained(output_dir)
tokenizer.save_pretrained(output_dir)
print(f'Model saved to {output_dir}')
"
```

Or set a custom model directory:

```bash
export SRH_MODEL_DIR=/path/to/your/onnx-model
```

## Configuration

```yaml
plugins:
  enabled:
    - mem-reflection-hermes

  mem_reflection_hermes:
    # Core features
    embeddings: false              # Enable semantic search (default: true)
    micro_reflection: true         # Auto-reflect per turn (default: false)
    palace_mode: true              # Memory Palace navigation (default: true)
    profile_mode: false            # LLM-compiled profile injection (default: false)
    palace_instructions: true      # Inject palace usage instructions (default: true)

    # Capacity limits
    active_memory_index_cap: 50    # Max memories in active index (default: 50)
    skill_index_cap: 50            # Max skills in index (default: 50)
    relevant_memory_cap: 3         # Max per-turn relevant memories (default: 3)
    triggered_skill_cap: 3         # Max per-turn triggered skills (default: 3)
    max_context_token_preference: 6000  # Token budget for context block
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `HERMES_HOME` | Hermes configuration directory | `~/.hermes` |
| `SRH_MODEL_DIR` | Custom ONNX model directory | `~/.hermes/models/all-MiniLM-L6-v2-onnx` |

## Usage

### Automatic Behavior

Once enabled, the plugin works automatically:

1. **Session Start**: Builds palace index, loads compiled profile if available
2. **Per Turn**: Injects layered context (palace/profile/pinned) into user message
3. **Session End**: Runs full reflection, generates skill candidates

### Tools

```python
# Memory search
srh_memory_search(query="Python error handling", k=5)
srh_memory_search(query="部署流程", k=5, zone="work")  # zone filter

# Memory write
srh_memory_write(
    body="Always use anyhow for app-level error handling",
    tags=["rust", "error-handling"],
    confidence="high",
    pinned=true
)

# Memory management
srh_memory_delete(memory_id="mem_abc123")
srh_memory_history(memory_id="mem_abc123")  # version lineage

# Skill search
srh_skill_search(query="rust async", k=3)

# Reflection
srh_reflect_now(mode="full")

# Palace navigation
srh_palace_zones()                          # list all zones
srh_palace_read_zone(zone="work")           # read zone summary
srh_palace_recall(topic="editor preference") # topic-based recall
srh_palace_search(query="Docker")           # cross-zone search
srh_palace_rebalance(dry_run=true)          # auto split/merge

# Profile compilation
srh_compile_profile(mode="profile")         # compile to profile.md
srh_compile_profile(mode="palace_index")    # compile to palace index

# Graph memory (ahe_graph)
srh_associate(source_id="mem_abc", target_id="mem_def", relation="supersedes")
srh_graph_retrieve(seed_id="mem_abc", depth=2)
srh_graph_stats()
srh_graph_viz()
```

### Slash Commands

```
/memories              # List all active memories
/skills                # List all active skills
/pending-skills        # Show skills awaiting approval
/approve-skill <id>    # Approve a pending skill
/reject-skill <id>     # Reject a pending skill
/compile-profile       # Compile memories into profile via LLM
```

## Memory Format

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
---

Always use anyhow for app-level error handling in Rust.
```

**Fields:**
- `id`: Unique identifier (auto-generated)
- `created`: ISO 8601 timestamp
- `source`: Origin (`micro_reflection`, `full_reflection`, `manual`, etc.)
- `confidence`: `low` | `medium` | `high`
- `pinned`: Whether to always inject into context
- `tags`: List of searchable tags
- `zone`: Memory Palace zone (`core`, `work`, `episode`, `general`, `project:*`)
- `rank` (v0.6.1+): Explicit ordering. Higher rank = appears earlier. Default 0
- `supersedes`: List of memory IDs this memory replaces (version lineage)
- `version` (v0.8.0+): Optional version string for tracking iterations

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
│       ├── README.md                # This file
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

## Development

### Performance Benchmarking

```bash
cd ~/.hermes/plugins/mem-reflection-hermes
python bench_latency.py
```

### Module Import Order

When adding new functionality, respect the module boundaries:

1. **core.py**: Data models, store logic, config — no Hermes dependencies
2. **embed.py**: Embedding engine — imports from core only
3. **reflection.py**: Reflection pipelines — imports from core + embed
4. **hooks.py**: Session hooks — imports from core + embed + reflection
5. **tools.py**: Tool handlers — imports from all above modules
6. **__init__.py**: Registration and exports — imports from all modules

Avoid circular dependencies. Use function-level late-binding if cross-module
references are needed at import time.

## License

MIT — Ported from [coder-brzhang/small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes)

## Acknowledgments

- Original Rust implementation by [coder-brzhang](https://github.com/coder-brzhang)
- ONNX optimization inspired by [optimum](https://github.com/huggingface/optimum)
- Embedding model: [sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
