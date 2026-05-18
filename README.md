# mem-reflection-hermes

Self-evolving memory & reflection system for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Ported from [small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes) with significant performance enhancements.

## Features

- **Structured Memories**: Markdown + YAML frontmatter (id, created, source, confidence, pinned, tags, supersedes)
- **Dual Scope**: User-level (`~/.hermes/memories/`) and project-level (`./.hermes/memories/`)
- **TF-IDF Search**: Pure Python implementation, zero external dependencies
- **Semantic Search**: ONNX Runtime + all-MiniLM-L6-v2, 16x faster than PyTorch
- **Conflict Detection**: Automatic similarity checking on write with supersedes chains
- **Micro-Reflection**: Per-turn background reflection (~2.5ms latency, zero LLM cost)
- **Full Reflection**: Session-end structured summary with human approval for skills
- **Skill Auto-Matching**: Token overlap + embedding hybrid for context injection
- **Context Layering**: Pinned → Active Index → Triggered Skills

## Architecture

```
┌─────────────────────────────────────────┐
│           Hermes Agent Session          │
├─────────────────────────────────────────┤
│  pre_llm_call hook                      │
│    ├── Inject pinned memories           │
│    ├── Inject active index memories     │
│    ├── Inject triggered skills          │
│    └── Trigger micro-reflection         │
├─────────────────────────────────────────┤
│  on_session_end hook                    │
│    └── Run full reflection              │
├─────────────────────────────────────────┤
│  Tools                                  │
│    ├── srh_memory_search                │
│    ├── srh_memory_write                 │
│    ├── srh_memory_delete                │
│    ├── srh_skill_search                 │
│    └── srh_reflect_now                  │
├─────────────────────────────────────────┤
│  Slash Commands                         │
│    ├── /memories                        │
│    ├── /skills                          │
│    ├── /pending-skills                  │
│    ├── /approve-skill <id>              │
│    └── /reject-skill <id>               │
└─────────────────────────────────────────┘
```

## Performance

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Micro-reflection (200 memories) | ~103ms | **~2.5ms** | **41x** |
| Memory search (cached) | ~232ms | ~0.4ms | 580x |
| ONNX model load | 5.5s | 415ms | 13x |
| Embedding encode | 50ms/text | 3.1ms/text | 16x |
| Memory footprint | 825MB | **144MB** | **5.7x** |

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
  small_rust_hermes:
    embeddings: true          # Enable semantic search
    micro_reflection: true    # Auto-reflect per turn
    reflection_mode: embedding # Use local embeddings (not LLM)
EOF

# 3. Restart Hermes Agent
hermes restart
```

### Optional: ONNX Model Setup (Recommended)

For best performance, use the ONNX model instead of sentence-transformers fallback:

```bash
# Install dependencies
pip install onnxruntime tokenizers

# Download and convert model
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

Or set a custom model directory via environment variable:

```bash
export SRH_MODEL_DIR=/path/to/your/onnx-model
```

### Alternative: sentence-transformers Fallback

If you skip ONNX setup, the plugin automatically falls back to sentence-transformers:

```bash
pip install sentence-transformers
```

## Configuration

All configuration lives in `~/.hermes/config.yaml` under the `plugins.small_rust_hermes` section:

```yaml
plugins:
  enabled:
    - mem-reflection-hermes
  
  small_rust_hermes:
    # Embedding engine
    embeddings: true              # Enable semantic search (default: true)
    
    # Reflection behavior
    micro_reflection: true        # Auto-reflect per turn (default: false)
    reflection_mode: embedding    # 'embedding', 'llm', or 'hybrid'
    
    # Thresholds (optional, shown with defaults)
    # conflict_threshold: 0.85    # Similarity threshold for conflict detection
    # novelty_threshold: 0.25     # Minimum novelty score to create memory
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `HERMES_HOME` | Hermes configuration directory | `~/.hermes` |
| `SRH_MODEL_DIR` | Custom ONNX model directory | `~/.hermes/models/all-MiniLM-L6-v2-onnx` |

## Usage

### Automatic Behavior

Once enabled, the plugin works automatically:

1. **Session Start**: Loads pinned memories into context
2. **Per Turn**: Searches relevant memories, triggers micro-reflection (~2.5ms)
3. **Session End**: Runs full reflection, generates skill candidates

### Manual Tools

```
# Search memories
srh_memory_search(query="Python error handling", k=5)

# Write a memory
srh_memory_write(
    body="Always use anyhow for app-level error handling",
    tags=["rust", "error-handling"],
    confidence="high",
    pinned=true
)

# Delete a memory
srh_memory_delete(memory_id="mem_abc123")

# Search skills
srh_skill_search(query="rust async", k=3)

# Trigger manual reflection
srh_reflect_now(mode="full")
```

### Slash Commands

```
/memories              # List all active memories
/skills                # List all active skills
/pending-skills        # Show skills awaiting approval
/approve-skill <id>    # Approve a pending skill
/reject-skill <id>     # Reject a pending skill
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
supersedes: []
---

Always use anyhow for app-level error handling in Rust.
```

This format is:
- **Human-readable**: View and edit with any text editor
- **Git-friendly**: Track changes over time
- **Portable**: Easy to export/import

## File Structure

```
~/.hermes/
├── memories/                          # User-level memories
│   ├── 2024-01-15-mem_abc12.md
│   └── 2024-01-16-mem_def34.md
├── skills/                            # User-level skills
│   └── rust-error-handling/
│       └── SKILL.md
├── plugins/
│   └── mem-reflection-hermes/
│       ├── __init__.py               # Main plugin code
│       ├── plugin.yaml               # Plugin manifest
│       └── pending-skills.json       # Pending approvals
└── models/                           # ONNX model (optional)
    └── all-MiniLM-L6-v2-onnx/
        ├── model.onnx
        └── tokenizer.json
```

## Development

### Running Tests

```bash
cd ~/.hermes/plugins/mem-reflection-hermes
python -m pytest tests/ -v
```

### Performance Profiling

```bash
python -c "
import cProfile
import pstats

# Profile micro-reflection
pr = cProfile.Profile()
pr.enable()
# ... run reflection ...
pr.disable()

ps = pstats.Stats(pr).sort_stats('cumtime')
ps.print_stats(20)
"
```

## License

MIT - Ported from [coder-brzhang/small-rust-hermes](https://github.com/coder-brzhang/small-rust-hermes)

## Acknowledgments

- Original Rust implementation by [coder-brzhang](https://github.com/coder-brzhang)
- ONNX optimization inspired by [optimum](https://github.com/huggingface/optimum)
- Embedding model: [sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
