# Agent Memory System Architectures: Top 5 Reference Implementations
# vs mem-reflection-hermes

Generated: 2026-05-31

---

## Baseline: mem-reflection-hermes Architecture

| Feature | Implementation |
|---|---|
| Storage | Flat .md files + YAML frontmatter (user ~/.hermes/memories/, project ./.hermes/memories/) |
| Search Index | Pure Python TF-IDF (Counter + BOW, 0 external deps, ~0.8ms for 50 memories) |
| Embedding | ONNX Runtime + all-MiniLM-L6-v2 (lazy-loaded, optional) |
| Graph | Hebbian co-occurrence graph in SQLite with decay (ahe_graph) |
| Context Layering | Pinned -> Active Index (TF-IDF/embedding search) -> Triggered Skills -> Always-Active Skills |
| Memory Zones | Memory Palace: core/work/episode/general/project:* |
| Reflection | Micro-reflection (per-turn background) + Full reflection (session-end, LLM-powered) |
| Skill Discovery | LLM suggests skills during full reflection -> user approves (human-in-the-loop) |
| Conflict Resolution | Similarity check on write, supersedes chains, effectiveness tracking with decay |
| Profile | LLM-compiled profile from all memories (optional) |
| Dependencies | Minimal; zero-dependency indexing, optional ONNX for embeddings |

**Context injection architecture (pre_llm_call hook):**
1. Palace zone index (memory map)
2. Compiled profile (LLM-condensed user/agent model)
3. Active index -> top-k memories via TF-IDF (+ optional embedding rerank)
4. Triggered skills (matched by token overlap + optional embedding)
5. Always-active skills
6. Graph neighbors (enriched via ahe_graph SQLite)

---

## Top 5 Reference Implementations

---

### 1. Mem0 --- LLM-Powered Extraction + Managed Vector/Graph Store

**Paper:** arXiv:2504.19413 (April 2025)
**GitHub:** github.com/mem0ai/mem0

#### Architecture
User Input -> Message Pair -> Extraction Module -> Evaluation/Update -> Vector DB + Entity Graph (Mem0^g) -> Retrieval API -> LLM

- Extraction layer: LLM-call per message pair extracts salient facts, entities, relationships
- Storage: Two modes: (a) flat vector DB with embeddings, (b) Mem0^g (directed labeled graph with entities as nodes, relationships as edges)
- Retrieval: Semantic similarity + graph traversal for multi-hop reasoning
- Conflict resolution: Deduplication via similarity + temporal recency
- Multi-tenancy: User/agent/session scoping built-in

**Key Differences vs mem-reflection-hermes:**
- Storage: Managed cloud SDK or local vector DB vs Flat .md files (version-controllable)
- Extraction: LLM-call every message pair (proactive, costly) vs Passive (user decides, LLM only during reflection)
- Index: Embedding-first (always needs external model) vs TF-IDF first (0 deps), embeddings optional
- Graph: Entity-relationship graph (Mem0^g) explicit nodes/edges extracted by LLM vs Hebbian co-occurrence (implicit, weighted edges from shared presence)
- Dedup: Similarity threshold + temporal vs Supersedes chains + similarity check
- Reflection: No explicit reflection pipeline vs Micro + Full reflection cycles with skill approval
- Context Injection: API-based retrieval (top-k similar vectors) vs Layered injection (pinned > search > triggered skills)
- Vendor Lock: High (managed service) vs Zero (all local, flat files)

---

### 2. Letta / MemGPT --- OS-Inspired Memory Hierarchy

**Paper:** arXiv:2310.08560 (October 2023)
**GitHub:** github.com/letta-ai/letta-code

#### Architecture
Context Window: Core Memory (always in-context: Persona Block, Human Block, Tool Block) + Recall Memory (chronological message history DB) + Archival Memory (semantic vector DB)

- Core memory: Fixed-size text blocks rewritten by the agent itself (self-editing memory)
- Memory blocks: Extensible block architecture, agents can create/delete/modify blocks dynamically
- Recall memory: Automatic, immutable history of all messages (DB-backed)
- Archival memory: Vector DB for semantic search of stored facts
- Self-editing: Agent has tools to rewrite its own core memory blocks during conversation
- Context window as OS: Agent manages context like virtual memory, pages data in/out

**Key Differences vs mem-reflection-hermes:**
- Philosophy: LLM as Operating System (agent manages context) vs Plugin-managed index (agent doesn't manage memory directly)
- Core Memory: Agent-editable text blocks always in context vs Pinned memories + compiled profile (plugin constructs)
- Recall: Full message history DB (automatic) vs Not stored as memory (reflection extracts facts from history)
- Search: Agent-invoked tools (archival_memory_search) vs Plugin-injected context (pre_llm_call, transparent)
- Self-Modification: Agent rewrites own core memory blocks via tools vs User writes memories, LLM suggests during reflection but user approves
- Storage: Letta server + DB + vector store vs Flat .md files only
- Reflection: No explicit reflection (memory evolves via self-editing) vs Explicit micro + full reflection
- Skills: Not natively separated from memory vs Memory + skills as distinct stores with separate matching

---

### 3. Zep / Graphiti --- Temporal Knowledge Graph Memory

**Paper:** arXiv:2501.13956 (January 2025)
**GitHub:** github.com/getzep/zep

#### Architecture
Conversation -> Graphiti Engine -> Temporal Knowledge Graph (Entity nodes, Relationship edges with time windows, Fact edges, Temporal metadata) -> Graph Search -> Subgraph Extraction -> Context String -> LLM

- Temporal awareness: Every fact and relationship has time windows (when it was true)
- Dynamic knowledge graph: Grows incrementally as conversations happen
- Cross-session synthesis: Links facts across different conversation sessions
- Graphiti engine: Proprietary graph construction from unstructured dialogue
- Retrieval: Subgraph extraction -> context string construction
- Benchmark: 94.8% on DMR (MemGPT benchmark), up to 18.5% better on LongMemEval

**Key Differences vs mem-reflection-hermes:**
- Memory Model: Temporal knowledge graph (entities + relationships with time windows) vs Flat memories with zone organization + Hebbian co-occurrence graph
- Storage: Neo4j / managed graph DB vs Flat .md files
- Temporality: First-class (every edge has valid_from/valid_to) vs Simple created/updated timestamps in frontmatter
- Extraction: Automatic graph construction from text vs Manual write + reflection (user decides what to store)
- Query: Graph traversal + subgraph extraction vs TF-IDF over flat text + optional embedding
- Event History: Full conversation stored as temporal graph vs Not stored (only extracted/reflected memories persist)
- Deployment: Managed service (self-host possible but heavy) vs Zero-infrastructure (files only)
- Relationship Model: Explicit entity-relation triples vs Implicit co-occurrence (Hebbian weights)

---

### 4. Memary --- Human Memory Simulation for Autonomous Agents

**GitHub:** github.com/kingjulio8238/memary

#### Architecture
Memory Modules: Episodic (STM->LTM consolidation), Semantic (entity-relationship KG), Spatial, Procedural -> Consolidation (STM->LTM via repeated activation) -> Knowledge Graph (Neo4j) -> Retrieval -> Context -> LLM

- Human memory metaphor: 4 memory types mimicking human cognition
- STM/LTM consolidation: Short-term -> long-term via repeated activation (spacing effect)
- Graph-native: Everything stored as entities and relationships in Neo4j
- Memory modules: Separate modules for different memory types, each with its own retrieval
- Automatic updates: Memory auto-updates as agent interacts

**Key Differences vs mem-reflection-hermes:**
- Memory Types: 4 distinct (episodic, semantic, spatial, procedural, human cognition model) vs 4 zones (core, work, episode, general, functional organization)
- Consolidation: STM->LTM via repeated activation + decay vs Confidence scoring with time decay + effectiveness tracking
- Graph: Neo4j entity-relationship (full graph DB) vs SQLite Hebbian co-occurrence (lightweight, implicit)
- Extraction: Automatic during agent interaction vs Manual (user/agent writes) + reflection-powered
- Query: Graph traversal + subgraph extraction vs TF-IDF + optional embedding
- Skills: Procedural memory built into memory vs Separate skill store with discovery pipeline
- Reflection: Not core (consolidation is automatic) vs Core (micro + full reflection with skill approval)
- Infrastructure: Requires Neo4j DB server vs Zero (flat files + optional SQLite)

---

### 5. Cognee --- Knowledge Graph + RAG Pipeline

**GitHub:** github.com/topoteretes/cognee

#### Architecture
Pipeline: Chunking -> Embedding -> Entity Extraction -> Relationship Extraction -> Graph Construction -> Index (LanceDB vectors + NetworkX graph)
Query: Hybrid Search (vector similarity + graph traversal) -> Re-ranking -> Context -> LLM

- Pipeline-first: Data flows through configurable pipeline stages
- Graph + Vector: Dual index (LanceDB for vectors, NetworkX for graph)
- Isolated user spaces: Built-in multi-tenancy/user isolation
- Document-centric: Designed for processing document corpuses, not just conversations
- Modular: Each pipeline stage is swappable

**Key Differences vs mem-reflection-hermes:**
- Primary Use Case: Document knowledge base -> RAG pipeline vs Agent conversation memory + reflection
- Index: LanceDB (vectors) + NetworkX (graph) vs TF-IDF (primary) + optional ONNX embedding
- Pipeline: Deterministic pipeline (chunk -> embed -> extract -> index) vs Event-driven (write -> reflect -> index)
- Persistence: LanceDB (vector DB) + JSON/folder for graph vs Flat .md files
- Reflection: Not a concept vs Core system feature
- Skills: Not separated from memory vs Separate store + auto-discovery
- Extraction: Automatic during pipeline processing vs Manual + reflection-powered
- Graph Model: Entity-relationship (LLM-extracted) vs Hebbian co-occurrence (implicit)

---

## Cross-Cutting Comparison Summary

### Storage Approaches

| System | Storage Backend | Version Control | Zero-Infra |
|---|---|---|---|
| mem-reflection-hermes | Flat .md files + YAML | Yes (native git) | Yes |
| Mem0 | Vector DB (configurable) | No | No (needs DB) |
| Letta/MemGPT | Letta Server + DB + Vector | No | No |
| Zep/Graphiti | Neo4j / Managed Graph | No | No |
| Memary | Neo4j | No | No |
| Cognee | LanceDB + NetworkX | No | Partial |

### Search & Retrieval

| System | Primary Index | Boost/Re-rank | Hybrid |
|---|---|---|---|
| mem-reflection-hermes | TF-IDF (0 dep) | Effectiveness score + decay | Optional (ONNX) |
| Mem0 | Embedding (always) | Temporal + entity match | Graph + vector |
| Letta/MemGPT | Embedding (always) | Context window priority | No |
| Zep/Graphiti | Graph traversal | Temporal window | Graph + optional vector |
| Memary | Graph traversal | Activation frequency | No |
| Cognee | Embedding + Graph | Pipeline config dependent | Yes |

### Reflection & Self-Improvement

| System | Micro-Reflection | Full Reflection | Skill Discovery |
|---|---|---|---|
| mem-reflection-hermes | Yes (per-turn background) | Yes (session-end, LLM + human) | Yes (LLM suggests, user approves) |
| Mem0 | No | No | No |
| Letta/MemGPT | No (self-edits during convo) | No | No |
| Zep/Graphiti | No | No | No |
| Memary | No (automatic consolidation) | No | No |
| Cognee | No | No | No |

### Memory Organization

| System | Zone/Tier Model | Graph Type | Temporality |
|---|---|---|---|
| mem-reflection-hermes | 4 functional zones | Hebbian co-occurrence (SQLite, implicit) | Created/updated timestamps |
| Mem0 | Single pool + entity graph | Entity-relation (explicit, LLM-extracted) | Temporal in dedup |
| Letta/MemGPT | Core + Recall + Archival | None | Message history order |
| Zep/Graphiti | Single temporal KG | Entity-relation (explicit, automatic) | First-class time windows |
| Memary | Episodic/Semantic/Spatial/Procedural | Entity-relation (explicit, Neo4j) | Activation-based decay |
| Cognee | Single knowledge graph | Entity-relation (explicit, LLM-extracted) | Not emphasized |

### When to Choose Each

| System | Best For |
|---|---|
| mem-reflection-hermes | Privacy-first, offline agents; minimal infrastructure; needs self-improvement via reflection |
| Mem0 | Production apps needing managed memory SDK; multi-tenant SaaS |
| Letta/MemGPT | Agents that need to manage their own context; research into agent self-modification |
| Zep/Graphiti | Enterprise apps needing temporal reasoning and cross-session entity tracking |
| Memary | Research into human-like memory; prototype exploration |
| Cognee | Document-heavy pipelines needing graph+RAG hybrid; knowledge base construction |

### mem-reflection-hermes Unique Strengths
1. Flat file storage (git-versionable .md files; zero infrastructure needed)
2. Zero-dependency TF-IDF (no vector DB required; embeddings are optional)
3. Reflection pipeline (only system with explicit micro + full reflection cycles)
4. Separate skill store (distinct memory/skill separation with auto-discovery + approval)
5. Context layering: Pinned -> Search -> Skills -> Always-Active (4-tier injection)

### Trade-offs
1. No automatic extraction (writes are manual or reflection-driven; Mem0/Letta auto-extract)
2. No temporal knowledge graph (Zep temporal edges enable precise when-X-was-true queries)
3. No entity extraction (Mem0^g and Cognee build explicit entity-relation graphs)
4. No STM/LTM consolidation (Memary human-memory-inspired consolidation more biologically plausible)
5. No agent self-editing (Letta agent-managed memory allows dynamic context reconfiguration)

---

## References

1. Mem0 paper: https://arxiv.org/abs/2504.19413
2. MemGPT/Letta paper: https://arxiv.org/abs/2310.08560
3. Zep paper: https://arxiv.org/abs/2501.13956
4. Memary GitHub: https://github.com/kingjulio8238/memary
5. Cognee: https://github.com/topoteretes/cognee
6. Reflexion: Shinn et al. 2023, arXiv:2303.11366
7. Atlan Agent Memory Architectures 2026: https://atlan.com/know/agent-memory-architectures/
8. Letta Memory Blocks: https://www.letta.com/blog/memory-blocks
