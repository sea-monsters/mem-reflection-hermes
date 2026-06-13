# mem-reflection-hermes

Self-evolving memory & reflection system for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Ported from [small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes) with significant performance enhancements, a full-featured dashboard, and graph memory integration.

**Current version: v1.7** — Memory Event Ledger, Scoped Filters, scope-aware reflection propagation, curator action pipeline, context reliability, entity recall, graph memory, and reflection system. See [CHANGELOG](docs/CHANGELOG.md) for full history.

## Features

- **Structured Memories**: Markdown + YAML frontmatter with zone, rank, version, supersedes chains
- **Scoped Memories**: Optional `user_id` / `agent_id` / `run_id` metadata with NULL = globally visible; compatible with user- and project-level storage roots
- **Memory Palace**: Zone-based organization (core, work, episode, general, project:*) with tool-driven navigation
- **BM25 Search**: Pure Python, zero dependencies, ~0.8ms for 50 memories
- **Semantic Search**: ONNX Runtime + all-MiniLM-L6-v2, 16x faster than PyTorch (optional)
- **Conflict Detection**: Automatic similarity checking on write with version lineage
- **Effectiveness Tracking**: Per-memory scoring with exponential time decay
- **Micro-Reflection**: Per-turn background reflection with backpressure queue
- **Full Reflection**: Session-end structured summary with human approval for skills
- **Skill Auto-Matching**: Token overlap + optional embedding hybrid
- **Profile Compilation**: LLM-driven compilation into structured profile documents
- **Runtime Graph Memory**: GraphIndex-backed Hebbian co-occurrence learning, decay, PageRank, and adaptive retrieval
- **Typed Fact Sidecar** (v1.7): GraphIndex now keeps a lightweight typed-fact sidecar for self-contained facts, relation lineage, and invalidation trails alongside the associative graph.
- **Runtime Query APIs**: Dashboard/search query paths combine MemoryStore, search templates/cache, graph neighbors, and supersedes-aware recall
- **PageRank**: Centrality scores for hub memory identification
- **Cross-Zone Analysis**: Bridge memories, zone centrality, zone recommendations
- **Episode Compaction** (v1.1): Clusters raw episode entries into daily summaries via LLM
- **Compaction Fallback Quality** (v1.7): When LLM summarization is unavailable, episode compaction now scores candidate fragments and prefers concise conclusion-like summaries over longest-body truncation.
- **Memory Curator** (v1.2 → v1.3 → v1.5 → v1.6 → v1.7): Automated lifecycle maintenance — TTL expiry, staleness detection, supersedes archiving, similarity detection, orphan graph-edge cleanup, cold storage with tool-noise stripping. v1.7 starts propagating scope context through reflection, compaction, and manual reflection entrypoints so hosted or multi-user/multi-agent runs can keep maintenance per-scope; use explicit `admin_global=True` only for deliberate full-store maintenance; no-filter local mode remains global for single-user compatibility.
- **Context Reliability** (v1.4): Stable/dynamic context split, timeout-protected assembly, graded compression (`none/mild/aggressive/emergency`)
- **Memory Event Ledger** (v1.6): Append-only `memory_events` log tracks all write/update/delete/archive/reflect events with `user_id`, `agent_id`, `run_id`, `memory_key`, `event_type`, and ISO timestamp; event search via `srh_memory_history` tool
- **Scoped Filters** (v1.6): `user_id` / `agent_id` / `run_id` filters on search, delete, palace, compile, and dashboard APIs; `None` matches NULL-scoped memories, while omitted filters preserve the global view
- **Scope-Aware Reflection** (v1.7): Micro/full reflection, raw chunk capture, compaction, and manual `srh_reflect_now` now inherit scope filters when the host provides them, so writes no longer fall back to accidental global scope in hosted or multi-agent sessions. Semantic supersedes resolution has also started so generic memory intents no longer auto-promote to replacement edges.
- **Explainable Search** (v1.4): Opt-in `explain=true` flag returns structured score breakdown per hit (BM25, embedding, recency, effectiveness, entity, Hebbian)
- **Entity Recall** (v1.4): SQLite-backed entity index with lifecycle hooks; entity boost in search + explain output
- **Session Checkpoint** (v1.4): Atomic JSON persistence with pending-stage recovery and corrupt-failopen behavior
- **Dashboard Memory Manager**: Full CRUD + reorder + graph visualization + runtime search + zone analysis
- **Temporal/Context Hints**: `valid_from`, `valid_until`, `context_scope` for time-bounded and scoped memories
- **Slash Commands**: `/reflect`, `/skills`, `/memories`, `/pending`, `/approve`, `/reject`, `/compile`
- **Thread Safety**: RLock on MemoryStore, locks on session state and embedding cache
- **Session-Scoped Reflection**: Reflection exclusion set prevents feedback loops within a session
- **Robust Error Handling**: Silent failures upgraded to warning-level logging across all modules
- **Protocol-Based Design**: GraphStoreProtocol uses typing.Protocol for structural typing

## Documentation

| Document | Description |
|----------|-------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System overview, module layout, context layering, import order |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Version history and release notes |
| [docs/TOOLS.md](docs/TOOLS.md) | Current SRH tool reference (13 registered tools) with examples |
| [docs/DASHBOARD.md](docs/DASHBOARD.md) | Dashboard UI features and 15 API endpoints |
| [docs/MEMORY_FORMAT.md](docs/MEMORY_FORMAT.md) | Frontmatter schema and file structure |
| [docs/testing/test-coverage.md](docs/testing/test-coverage.md) | Test coverage map and host-contract smoke status |
| [docs/DATA_SAFETY.md](docs/DATA_SAFETY.md) | Write patterns, cache consistency, known issues |
| [PERF_REPORT.md](PERF_REPORT.md) | Performance benchmark results |

## Quick Start
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

## Configuration

```yaml
plugins:
  enabled:
    - mem-reflection-hermes

  mem_reflection_hermes:
    embeddings: false              # Semantic search (default: true)
    micro_reflection: true         # Auto-reflect per turn (default: false)
    palace_mode: true              # Memory Palace (default: true)
    profile_mode: false            # Profile injection (default: false)
    palace_instructions: true      # Usage instructions (default: true)

    active_memory_index_cap: 50    # Max memories in active index
    skill_index_cap: 50            # Max skills in index
    relevant_memory_cap: 3         # Max per-turn relevant memories
    triggered_skill_cap: 3         # Max per-turn triggered skills
    max_context_token_preference: 6000
```

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `HERMES_HOME` | Hermes configuration directory | `~/.hermes` |
| `SRH_MODEL_DIR` | Custom ONNX model directory | `~/.hermes/models/all-MiniLM-L6-v2-onnx` |

## Usage

Once enabled, the plugin works automatically:

1. **Session Start**: Builds palace index, loads compiled profile
2. **Per Turn**: Injects layered context (palace/profile/pinned/skills)
3. **Session End**: Runs full reflection, generates skill candidates

See [docs/TOOLS.md](docs/TOOLS.md) for the complete tool API.

## Development

```bash
cd ~/.hermes/plugins/mem-reflection-hermes
python bench_latency.py
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module boundaries and import order.

## License

MIT — Ported from [coder-brzhang/small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes)

## Acknowledgments

- Original Rust implementation by [coder-brzhang](https://github.com/coder-brzhang)
- ONNX optimization inspired by [optimum](https://github.com/huggingface/optimum)
- Embedding model: [sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
