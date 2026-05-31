# mem-reflection-hermes

Self-evolving memory & reflection system for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Ported from [small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes) with significant performance enhancements, a full-featured dashboard, and graph memory integration.

**Current version: v0.8.0** — 6-module architecture, 17 SRH tools, ahe_graph integration.

## Features

- **Structured Memories**: Markdown + YAML frontmatter with zone, rank, version, supersedes chains
- **Dual Scope**: User-level (`~/.hermes/memories/`) and project-level (`./.hermes/memories/`)
- **Memory Palace**: Zone-based organization (core, work, episode, general, project:*) with tool-driven navigation
- **TF-IDF / BM25 Search**: Pure Python, zero dependencies, ~0.8ms for 50 memories
- **Semantic Search**: ONNX Runtime + all-MiniLM-L6-v2, 16x faster than PyTorch (optional)
- **Conflict Detection**: Automatic similarity checking on write with version lineage
- **Effectiveness Tracking**: Per-memory scoring with exponential time decay
- **Micro-Reflection**: Per-turn background reflection with backpressure queue
- **Full Reflection**: Session-end structured summary with human approval for skills
- **Skill Auto-Matching**: Token overlap + optional embedding hybrid
- **Profile Compilation**: LLM-driven compilation into structured profile documents
- **Graph Memory (ahe_graph)**: Associate memories, graph traversal, stats, visualization
- **Dashboard Memory Manager**: Full CRUD + reorder UI

## Documentation

| Document | Description |
|----------|-------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System overview, module layout, context layering, import order |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Version history from v0.1.0 to v0.8.0 |
| [docs/TOOLS.md](docs/TOOLS.md) | Complete SRH tool reference with examples |
| [docs/DASHBOARD.md](docs/DASHBOARD.md) | Dashboard UI features and API endpoints |
| [docs/MEMORY_FORMAT.md](docs/MEMORY_FORMAT.md) | Frontmatter schema and file structure |
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
