# Agent 记忆系统深度对比报告 v2.1 — mem0 vs mem-reflection-hermes vs hy-memory

> 日期: 2026-06-09
> mem0 版本: v2.0.4 (源码级分析)
> mem-reflection-hermes (SRH) 版本: v1.3-beta 基线分析，已补充 v1.4 实施落地说明 (2026-06-09)
> hy-memory (TencentDB Agent Memory) 版本: v0.3.6 (源码级分析)
> 分析方法: 源码静态分析 + 学术论文检索

---

## 执行摘要

三款 Agent 记忆系统代表了三种根本不同的设计哲学：

| 系统 | 定位 | 设计哲学 |
|------|------|----------|
| **mem0** | 服务化库 | "记忆的硬盘" — 可靠存储、高效检索、基础设施完备 |
| **SRH** | 自治插件 | "记忆的大脑" — 自治演化、自主策展、图记忆驱动 |
| **hy-memory** | 分层管道 | "记忆的消化系统" — L0→L1→L2→L3 渐进式提炼、符号化上下文卸载 |

**核心发现**:
- **mem0** 在基础设施抽象和检索工程上最成熟，但完全缺失自治能力
- **SRH** 在图记忆和自治演化层领先，但检索管道的工程细节不如 mem0 精致
- **hy-memory** 在上下文压缩和分层记忆架构上最具创新性，CJK 支持和符号化短期记忆是其独特优势

三者不是竞争关系，而是互补。理想系统应融合：mem0 的基础设施 + SRH 的 Hebbian 图 + hy-memory 的分层管道和上下文压缩。

---

## 1. 架构与设计哲学

### 1.1 架构总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        三种记忆系统的架构对比                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  mem0 (v2.0.4)                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────────┐  │
│  │ Client API   │ →  │ Memory Class │ →  │ Vector Store (20+ providers) │  │
│  └──────────────┘    └──────────────┘    └──────────────────────────────┘  │
│                              ↓                                              │
│                        SQLite (history + messages)                          │
│                              ↓                                              │
│                        Entity Store (独立向量集合)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│  SRH (v1.3-beta baseline / v1.4 dev)                                        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────────┐  │
│  │ Hermes Agent │ →  │ 12 Tools     │ →  │ runtime/hooks.py (生命周期)  │  │
│  └──────────────┘    └──────────────┘    └──────────────────────────────┘  │
│         ↓                                                                    │
│    ┌────┴────┬────────┬──────────┬──────────┐                              │
│    ↓         ↓        ↓          ↓          ↓                              │
│ core/store core/search reflection/ memory/  web/api                        │
│ (文件系统)  (BM25+嵌入) engine+   curator+  (15端点)                       │
│                       runtime     bridge+                                   │
│                                   context                                   │
│                                     ↓                                       │
│                                  core/graph (Hebbian图)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  hy-memory (v0.3.6)                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────────┐  │
│  │ OpenClaw/    │ →  │ TdaiCore     │ →  │ Pipeline Manager (L0→L1→L2→L3)│  │
│  │ Hermes       │    │ (host-neutral│    └──────────────────────────────┘  │
│  └──────────────┘    │   facade)    │                    ↓                 │
│                      └──────────────┘         ┌──────────┴──────────┐      │
│                              ↓                ↓                     ↓      │
│                        ┌─────────────┐  ┌──────────┐        ┌───────────┐  │
│                        │ VectorStore │  │ AutoRecall│        │ Offload   │  │
│                        │ (SQLite/    │  │ (注入上下文│        │ (L3压缩)  │  │
│                        │  TCVDB)     │  └──────────┘        └───────────┘  │
│                        └─────────────┘                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│  hy-memory 4-layer pyramid:                                                 │
│  L0 (raw conversations) → L1 (structured memories) → L2 (scenes) → L3 (persona)│
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 设计哲学对比

| 维度 | mem0 | SRH | hy-memory |
|------|------|-----|-----------|
| **角色定位** | 被调用的库/服务 | 自治运行的 Agent 插件 | 分层管道插件 |
| **状态管理** | 无状态，调用方驱动 | 有状态，生命周期驱动 | 有状态，管道调度驱动 |
| **数据主权** | 向量存储内部格式 | 用户可读的 Markdown 文件 | SQLite 内部格式 |
| **智能层** | 仅提取（LLM） | 提取+反射+策展+图演化 | 分层提炼+上下文压缩 |
| **集成深度** | 浅（API 调用） | 深（钩子注入） | 中（钩子 + 工具） |
| **配置风格** | Pydantic v2 模型 | YAML dict | TypeScript 接口 + JSON |
| **语言栈** | Python | Python | TypeScript (Node.js ≥22.16) |
| **宿主框架** | 任意 Python 代码 | Hermes Agent | OpenClaw / Hermes (bridge) |

---

## 2. 存储模型深度对比

### 2.1 mem0 — Vector-First

```python
# 记忆存储在向量数据库的 payload 中
{
  "data": "用户喜欢川菜",
  "hash": "md5(...)",
  "text_lemmatized": "用户 喜欢 川菜",
  "created_at": "2026-06-08T...",
  "user_id": "u1", "agent_id": "a1", "run_id": "r1",
  "linked_memory_ids": ["uuid1", "uuid2"],
  "memory_type": "semantic",
  "custom_metadata": {...}
}
```

- **主存储**: Qdrant / Chroma / pgvector / FAISS / Milvus 等 (20+ 种)
- **辅助存储**: SQLite 仅保存 history 表和最近 10 条 messages
- **格式**: 结构化 payload，非人类可读
- **版本控制**: history 表记录 ADD/UPDATE/DELETE 事件
- **多租户**: `user_id`/`agent_id`/`run_id` 三元组过滤

### 2.2 SRH — File-First

```markdown
---
id: mem-abc123
created: 2026-06-08T...
source: reflection
confidence: 0.92
pinned: true
tags: [food, preference]
zone: personal
rank: 0.85
supersedes: mem-old456
supersedes_reason: "updated preference"
version: 2
valid_from: 2026-06-01
valid_until: null
context_scope: session
---

用户喜欢川菜，尤其偏好火锅和麻辣烫。
```

- **主存储**: 文件系统（`~/.hermes/memories/`），Markdown + YAML frontmatter
- **辅助存储**: SQLite（搜索索引、Hebbian 图）、JSONL（冷存储）
- **格式**: 人类可读，支持 Git 版本控制
- **版本控制**: `supersedes` 链 + `version` 字段
- **作用域**: user + project 双作用域

### 2.3 hy-memory — Layered SQLite

hy-memory 使用 **4 层金字塔存储模型**，每层使用不同的存储机制：

| 层级 | 内容 | 存储 | 触发时机 |
|------|------|------|----------|
| **L0** | 原始对话消息 | SQLite `conversations` 表 / JSONL | 每轮对话结束 (auto-capture) |
| **L1** | 结构化记忆（事实、偏好、指令） | SQLite `l1_memories` vec0 虚拟表 | L1 管道批量提取 |
| **L2** | 场景块（scene blocks） | 文件系统 `scene_blocks/` 目录 | L2 管道定时提取 |
| **L3** | 用户画像（persona） | 文件系统 `persona.md` | L3 管道全局生成 |

**SQLite 存储细节** (`src/core/store/sqlite.ts`):
- **L0 表**: `conversations` — 原始消息，含 session_key、role、content、timestamp
- **L1 表**: `l1_memories` — 使用 `sqlite-vec` 的 `vec0` 虚拟表存储向量
- **FTS5**: `l1_fts` — 全文搜索表，使用 jieba `cutForSearch` 做 CJK 分词
- **向量维度**: 可配置，支持 OpenAI text-embedding-3-* Matryoshka

**hy-memory 的存储独特之处**:
1. **jieba CJK 分词**: `buildFtsQuery()` 使用 jieba `cutForSearch` 对中文查询分词，FTS5 MATCH 支持中文停用词过滤
2. **TCVDB 后端**: 可选 Tencent Cloud VectorDB，支持服务端 dense+sparse+RRF 单调用
3. **L0/L1 分离**: L0 原始对话和 L1 结构化记忆物理隔离，支持独立搜索
4. **JSONL 本地回退**: 当 SQLite 不可用时，L0 使用 JSONL 文件

### 2.4 存储模型学术评价

**MemForest 论文 (arXiv:2605.23986)**:
> "追加式存储 O(1) 但无结构化时间维护；平衡树 O(log N) 但保留时间局部证据。"

| 维度 | mem0 | SRH | hy-memory |
|------|------|-----|-----------|
| **主存储介质** | 向量数据库 | 文件系统 | SQLite (文件) |
| **可读性** | 差（内部 payload） | 优（Markdown） | 差（SQLite 内部） |
| **Git 友好** | 否 | 是 | 否 |
| **版本追溯** | history 表（事件级） | supersedes 链（语义级） | 无显式版本链 |
| **多租户** | user/agent/run 三元组 | user + project 双作用域 | actorId + sessionKey |
| **冷存储** | 无 | JSONL + 自动归档 | l0l1RetentionDays TTL |
| **CJK 支持** | 无特殊处理 | token 估算 + 冲突阈值 | jieba 分词 + FTS5 |
| **存储后端选择** | 20+ 种向量存储 | 文件系统 | SQLite / TCVDB |

**评价**:
- mem0 的向量优先适合高频写入/查询的服务化场景
- SRH 的文件优先适合人工审计和 Git 追踪
- hy-memory 的 SQLite 分层适合渐进式提炼和快速检索，但缺乏人类可读性和版本控制

---

## 3. 检索系统深度对比

### 3.1 mem0 检索管道（9步流程）

源码位置: `mem0/memory/main.py:1347-1445`

```
Query → [Lemmatize] + [Entity Extract via spaCy]
   ↓
[Embed query]
   ↓
Step 3: Semantic search (over-fetch ×4, internal_limit = max(limit*4, 60))
   ↓
Step 4: Keyword search (BM25 via vector_store.keyword_search())
   ↓
Step 5: BM25 sigmoid normalization to [0, 1]
   ↓
Step 6: Entity boost computation (entity_store search + linked_memory_ids)
   ↓
Step 7: Build candidate set from semantic results
   ↓
Step 8: score_and_rank — additive normalization
   ↓
Step 9: Format results (MemoryItem Pydantic model)
```

**评分公式** (`mem0/utils/scoring.py:60-139`):
```python
max_possible = 1.0                    # semantic
if has_bm25:   max_possible += 1.0    # + BM25
if has_entity: max_possible += 0.5    # + entity

combined = min((semantic + bm25 + entity_boost) / max_possible, 1.0)
```

### 3.2 SRH 检索管道（5层流程）

源码位置: `core/search.py:444-609`

```
Query → [Tokenise] + [Embed]
   ↓
Layer 1: Recall — BM25 (bm25s / handrolled fallback) + Embedding cosine
   ↓
Layer 2: Fusion — RRF (Reciprocal Rank Fusion) 或 weighted
   ↓
Layer 3: Rerank — recency × effectiveness × supersedes_factor
   ↓
Layer 4: Hebbian boost — spreading activation (GraphIndex.spread)
   ↓
Layer 5: Optional second-stage reranker (cross-encoder / Cohere) + MMR
```

**RRF 融合公式** (`core/search.py`):
```python
# RRF: score = sum(1 / (k + rank_i))
# k=60 (standard RRF constant)
```

**Hebbian boost 公式** (`core/search.py:570-589`):
```python
# S(v_j) = S_base(v_j) + β · Σ_{i∈N(j)} S_base(v_i) · w_ij
activation = self._graph.spread(pool_ids, decay=0.7, max_iter=30)
scale = max_rerank_score if max_rerank_score > 0 else 1.0
hebbian_score = hebbian_beta * min(act, 1.0) * scale
```

### 3.3 hy-memory 检索管道（3策略 + RRF）

源码位置: `src/core/hooks/auto-recall.ts:308-646`

```
Query → [sanitizeText] (剥离 gateway 元数据)
   ↓
Strategy dispatch:
  ├─ "keyword": FTS5 BM25 (jieba 分词)
  ├─ "embedding": VectorStore cosine similarity
  └─ "hybrid": keyword + embedding → RRF merge
   ↓
Native hybrid short-circuit (TCVDB 后端):
  └─ 若 vectorStore.getCapabilities().nativeHybridSearch:
       单 API 调用 dense + sparse + RRF
   ↓
Client-side RRF (SQLite 后端):
  ├─ FTS5 搜索 (并行)
  └─ 向量搜索 (并行)
       ↓
     RRF merge (k=60)
   ↓
Threshold filter (default 0.3)
   ↓
Apply recall budget (maxCharsPerMemory / maxTotalRecallChars)
   ↓
Format memory lines (rich natural-language with time semantics)
```

**hy-memory RRF 实现** (`src/core/store/search-utils.ts`):
```typescript
export const RRF_K = 60;

export function rrfMerge<T>(
  lists: T[][],
  getId: (item: T) => string,
  k: number = RRF_K,
): Array<T & { rrfScore: number }> {
  const map = new Map<string, { item: T; rrfScore: number }>();

  for (const list of lists) {
    for (let rank = 0; rank < list.length; rank++) {
      const item = list[rank];
      const id = getId(item);
      const score = 1 / (k + rank + 1);
      const existing = map.get(id);
      if (existing) {
        existing.rrfScore += score;
      } else {
        map.set(id, { item, rrfScore: score });
      }
    }
  }

  return [...map.values()]
    .sort((a, b) => b.rrfScore - a.rrfScore)
    .map(({ item, rrfScore }) => ({ ...item, rrfScore }));
}
```

**hy-memory 检索独特之处**:
1. **FTS5 + jieba CJK**: `buildFtsQuery()` 使用 jieba `cutForSearch` 对中文查询分词，这是 mem0 和 SRH 都没有的 CJK 原生支持
2. **Native hybrid short-circuit**: TCVDB 后端支持服务端 dense+sparse+RRF 单调用，避免客户端两次 HTTP 请求
3. **Recall budget**: `maxCharsPerMemory` + `maxTotalRecallChars` 在检索结果层面做 token 预算，而非 SRH 的上下文组装阶段
4. **Timeout guard**: 默认 5s 超时，超时则跳过记忆注入避免阻塞用户
5. **sanitizeText**: 剥离 gateway 注入的元数据（Sender、时间戳、媒体标记、base64 图片数据）

### 3.4 检索策略学术评价

#### 加法归一化 vs RRF

| 维度 | mem0 加法 | SRH RRF | hy-memory RRF |
|------|----------|---------|---------------|
| **理论基础** | 线性加权叠加 | Reciprocal Rank Fusion (Cormack et al., 2009) | 同上 |
| **抗尺度差异** | 弱（需要 sigmoid 归一化） | 强（基于 rank，不依赖 score 尺度） | 强 |
| **信号独立性** | 需要各信号归一化到 [0,1] | 天然独立 | 天然独立 |
| **CJK 分词** | 无 | 无 | jieba `cutForSearch` |
| **原生稀疏向量** | 是（fastembed sparse） | 否（bm25s 独立库） | 是（FTS5） |
| **门控策略** | semantic threshold (0.1) | 无 threshold | threshold (0.3) |

**关键发现**: hy-memory 和 SRH 使用 **完全相同的 RRF 常数 k=60** 和相同的融合公式。这是学术文献中的标准值，说明两者都遵循了 RRF 原始论文的建议。mem0 的加法归一化在 "纯关键词查询但语义不相关" 的场景下会漏检，因为 semantic threshold 会过滤掉低语义分数的记忆。

#### 稀疏-密集交互

| 维度 | mem0 | SRH | hy-memory |
|------|------|-----|-----------|
| **稀疏索引** | vector_store.keyword_search() | bm25s 内存索引 | FTS5 (SQLite) |
| **密集索引** | 向量数据库原生 | numpy 数组 | sqlite-vec vec0 |
| **一致性** | 同一存储 | 两份索引需同步 | 同一数据库 |
| **CJK 支持** | 依赖 fastembed | 无特殊处理 | jieba 分词 |

**学术评价**: hy-memory 的 FTS5 + sqlite-vec 方案在一致性上优于 SRH 的独立 bm25s 索引，但 FTS5 的 BM25 实现不如 bm25s 专业（缺少 bm25s 的 IDF 优化）。mem0 的稀疏向量与密集向量在同一存储中，一致性最好。

---

## 4. 上下文管理与注入深度对比

### 4.1 mem0 — 手动检索

```python
# 调用方自己决定何时搜索、如何组织上下文
memories = m.search(query, filters={"user_id": "u1"}, top_k=5)
context = "\n".join([m["memory"] for m in memories])
prompt = f"相关记忆:\n{context}\n\n用户问题: {query}"
```

- 无自动注入机制
- 无 token 预算管理
- 无优先级分层

### 4.2 SRH — 自动 4 层注入

```python
# pre_llm_call hook 自动触发
context = context.assemble(
    pinned_memories,      # 层 1: 始终包含
    active_index_results, # 层 2: zone-based 相关度
    triggered_skills,     # 层 3: 每轮 token-overlap 匹配
    always_active_skills, # 层 4: 用户配置
    max_tokens=budget
)
```

- 自动在 pre_llm_call 时注入
- Token 预算感知（CJK-aware 估算）
- 4 层优先级裁剪

### 4.3 hy-memory — 分层上下文注入 + 符号化卸载

hy-memory 的上下文管理是三者中最复杂的，分为 **recall 注入** 和 **context offload 压缩** 两个阶段：

#### 阶段 A: Auto-Recall 注入 (`src/core/hooks/auto-recall.ts`)

```
pre_llm_call / before_prompt_build
   ↓
L1 记忆搜索 (FTS5 + vector + RRF)
   ↓
L3 Persona 加载 (stable, cacheable)
   ↓
L2 Scene Navigation 生成 (stable, cacheable)
   ↓
Split into:
  ├─ prependContext (dynamic, per-turn) → user prompt prefix
  └─ appendSystemContext (stable, cacheable) → system prompt suffix
   ↓
Memory tools guide 注入
```

**Prompt cache 优化**: hy-memory 将上下文分为 **stable**（persona、scene nav、tools guide）和 **dynamic**（L1 memories）两部分。Stable 部分放在 system prompt 末尾，可被 Anthropic/OpenAI 的 prompt caching 缓存；dynamic 部分放在 user prompt 前缀，避免 bust system prompt cache。

#### 阶段 B: Context Offload 压缩 (`src/offload/hooks/before-prompt-build.ts`)

hy-memory 的 **符号化短期记忆**（Symbolic Short-Term Memory）是独特创新：

```
Before LLM input:
   ↓
Phase 1: Fast-path re-apply
  ├─ 将已确认的 mild replacements 重新应用
  └─ 删除 aggressive-deleted messages
   ↓
Phase 2: Token guard
  ├─ Aggressive compress (删除非当前任务消息)
  ├─ Mild compress (替换为 summary)
  └─ Emergency compress (强制删除直到低于阈值)
   ↓
Phase 3: MMD injection
  └─ 将 MMD (Memory Meta Data) 注入 messages
```

**Token 阈值策略**:
- `mildOffloadRatio` (default 0.6): 超过此阈值触发 mild 压缩
- `aggressiveCompressRatio` (default 0.75): 超过此阈值触发 aggressive 压缩
- `emergencyCompressRatio` (default 0.9): 超过此阈值触发 emergency 压缩

**hy-memory 声称的优化效果**:
> "-61.38% token reduction" — 通过 Mermaid 上下文卸载和三级压缩实现

### 4.4 上下文管理学术评价

| 维度 | mem0 | SRH | hy-memory |
|------|------|-----|-----------|
| **注入方式** | 手动 search | 自动 hook 注入 | 自动 hook 注入 |
| **Token 预算** | 无 | 有（max_context_token_preference） | 有（三级压缩阈值） |
| **优先级分层** | 无（调用方决定） | 4 层优先级 | L1/L2/L3 分层 |
| **Prompt cache 优化** | 无 | 无 | stable/dynamic 分离 |
| **上下文压缩** | 无 | 无（仅策展归档） | 三级压缩 + MMD |
| **符号化卸载** | 无 | 无 | Mermaid MMD |
| **技能匹配** | 无 | SkillStore token-overlap | 无 |

**评价**: hy-memory 在上下文压缩上远超其他两者。其 **stable/dynamic 分离** 设计是生产级的 prompt cache 优化，**三级压缩**（aggressive + mild + emergency）在 token 管理上非常精细。SRH 的 4 层优先级在语义组织上更优，但缺少 token 层面的精细控制。mem0 完全依赖调用方管理上下文。

---

## 5. 反射、策展与管道调度深度对比

### 5.1 mem0 — LLM 提取，无自动策展

**V3 Add Pipeline** (8阶段，`mem0/memory/main.py:700-972`):
1. 上下文收集（最近 10 条 messages）
2. 检索现有记忆（top 10 语义匹配）
3. **LLM 提取**（单轮 additive extraction prompt）
4. 批量嵌入
5. CPU 处理 + MD5 hash 去重
6. 批量持久化 + history
7. 实体链接（batch entity extraction + linking）
8. 保存 messages

**关键特点**:
- 每 `add()` 一次 = 一次 LLM 调用
- 提取粒度：事实级别
- 去重：MD5 hash 精确匹配（无语义去重）
- **无自动策展**：没有 TTL、staleness 检测、相似度归档、冷存储

### 5.2 SRH — 完整反射管道

**反射**:
- `_run_full_reflection`: session-end 触发
- `_run_micro_reflection`: 每 3 turns 或显式意图触发
- `ReflectionEngine`: 事实提取、记忆精炼
- Session exclusion set: 防止反馈循环

**策展 4-phase** (`memory/curator.py`):
1. **TTL + Staleness**: 过期和 >90 天未访问的记忆归档
2. **Supersedes Archiving**: 深度 supersedes 链（depth ≥ 2）无近期访问
3. **Similarity Detection**: BM25 token-overlap 对评分，>0.6 阈值
4. **Cold Storage**: JSONL，10MB 上限 + 最旧条目修剪

### 5.3 hy-memory — L0→L1→L2→L3 分层管道

hy-memory 的 **MemoryPipelineManager** (`src/utils/pipeline-manager.ts`) 是三者中最复杂的管道调度系统：

#### L0 → L1 管道

```
agent_end / turn_committed
   ↓
auto-capture.ts: 提取新消息，清洗，缓冲
   ↓
notifyConversation(sessionKey, messages)
   ↓
Path A (threshold): conversation_count >= effectiveThreshold
  ├─ Warm-up: 1 → 2 → 4 → 8 → ... → everyNConversations
  └─ 触发 L1 batch processing
   ↓
Path B (idle): l1IdleTimeoutSeconds 后触发
   ↓
Path C (flush): session_end 时 flush 缓冲消息
```

#### L1 → L2 管道

```
L1 完成
   ↓
advanceL2Timer (downward-only)
  ├─ T_desired = max(now + l2DelayAfterL1, lastL2 + l2MinInterval)
  └─ timer 只能提前，不能延后
   ↓
L2 timer fired
   ↓
SceneExtractor: LLM agent 提取场景块
   ↓
写入 scene_blocks/ 目录
```

#### L2 → L3 管道

```
L2 完成
   ↓
triggeL3() (global mutex, concurrency=1)
   ↓
Persona generation
   ↓
写入 persona.md
```

#### hy-memory 管道独特之处

1. **Warm-up mode**: 新会话的 L1 触发阈值从 1 开始，每次成功后翻倍（1→2→4→8→...→5），确保早期对话快速处理
2. **Downward-only timer**: L2 timer 只能提前不能延后，确保 maxInterval 保证和 delay-after-L1 响应性
3. **SerialQueue**: L1/L2/L3 均使用 concurrency=1 的串行队列，避免并发问题
4. **Session GC**: 定期清理冷会话（inactive > 3×activeWindow），防止内存无限增长
5. **Checkpoint recovery**: 崩溃后从 checkpoint 恢复，pending work 不丢失
6. **Retry with backoff**: L1 失败后有 5 次重试，30s 间隔

### 5.4 反射/策展学术评价

**Retrieval Bottleneck 论文 (arXiv:2603.02473)**:
> "检索方法决定 20 点准确度差异，写入策略仅 3-8 点。Basic RAG（零 LLM 调用的原始分块）在 hybrid 检索下匹配或超越昂贵的 fact extraction。"

| 维度 | mem0 | SRH | hy-memory |
|------|------|-----|-----------|
| **触发时机** | 每 add() 一次 | session-end / 每 3 turns | 每 N 轮对话 / idle timeout |
| **LLM 调用频率** | 高（逐条） | 中（批量） | 中（批量，warm-up 降低初期频率） |
| **提取粒度** | 事实级别 | 事实 + chunk | 事实 (L1) + 场景 (L2) + 画像 (L3) |
| **去重机制** | MD5 hash | BM25 overlap | batchDedup (LLM 批量冲突检测) |
| **策展** | 无 | 4-phase | l0l1RetentionDays TTL |
| **版本控制** | history 表 | supersedes 链 | 无显式版本链 |
| **会话隔离** | 无 | `_current_session_memory_ids` | sessionKey 隔离 |
| **崩溃恢复** | 无 | 无 | checkpoint + recoverPendingSessions |
| **并发控制** | 无（单线程） | RLock | SerialQueue (concurrency=1) |

**评价**:
- mem0 的逐条 LLM 提取成本最高，但延迟最低
- SRH 的 session-end 批量反射成本较低，但延迟较高（需等待 session 结束）
- hy-memory 的管道调度最精致，warm-up + downward-only timer + SerialQueue 是生产级设计

---

## 6. 图/关联系统深度对比

### 6.1 mem0 — 轻量 Entity Graph

```
Entity extraction (spaCy) → Entity Store (独立向量集合)
                                ↓
                    linked_memory_ids 数组
                                ↓
                    查询时: entity search → boost linked memories
```

- **实体类型**: PROPER, QUOTED, COMPOUND, NOUN
- **链接方式**: `linked_memory_ids` 数组
- **查询逻辑**: entity_store.search() → boost 0.5
- **无**: 图遍历、PageRank、传播激活、衰减

### 6.2 SRH — Hebbian Co-activation Graph

```
post_tool_call hook → 记录共同使用的记忆 → GraphIndex.associate()
                                                        ↓
                                              SQLite edges 表
                                                        ↓
                                              PageRank (重要性)
                                              spreading activation (查询扩散)
                                              decay (边衰减)
```

- **边语义**: `co_occurs` — 同一 session 中共同使用的记忆
- **权重更新**: `new_weight = min(1.0, old_weight + 0.05)`
- **图算法**: spread(), pagerank(), decay(), step_decay()
- **学术依据**: HeLa-Mem Hebbian learning

### 6.3 hy-memory — 无显式图系统

hy-memory **没有图关联系统**。它的关联机制是隐式的：

1. **L2 Scene 导航**: `scene-navigation.ts` 生成场景导航文本，LLM 决定场景相关性
2. **L1 记忆类型标签**: 记忆有 `type` 字段（如 `episodic`, `instruction`, `persona`），支持按类型过滤
3. **source_message_ids**: L1 记忆记录其来源的 L0 消息 ID，支持追溯
4. **sessionKey 关联**: 同一 session 的记忆天然关联

**hy-memory 的 "准图" 机制**:
- 没有显式的边存储
- 没有 PageRank 或 spreading activation
- 场景导航（scene navigation）是内容驱动的上下文组织，而非行为驱动的图遍历

### 6.4 图系统学术评价

**HeLa-Mem 论文 (arXiv:2604.16839)**:
> "Hebbian 图必须通过 spreading activation 参与检索才有价值。单独存储边而不参与检索计算，图只是死数据。"

| 维度 | mem0 | SRH | hy-memory |
|------|------|-----|-----------|
| **图类型** | Entity-Relation（语义） | Associative Co-activation（行为） | 无显式图 |
| **建边时机** | 记忆添加时（LLM 提取实体） | 记忆使用时（session 共现） | 隐式（同 session） |
| **图算法** | 无 | PageRank + spreading activation | 无 |
| **衰减** | 无 | 日历衰减 + 步进衰减 | 无 |
| **学术依据** | spaCy NER | HeLa-Mem Hebbian learning | 无 |
| **场景导航** | 无 | 无 | Scene Navigation (L2) |

**评价**: SRH 的 Hebbian 图在学术上最先进。mem0 的 entity graph 是静态索引。hy-memory 没有图系统，但其 L2 Scene 导航在一定程度上弥补了图遍历的功能 — 通过 LLM 判断场景相关性，而非算法化的 spreading activation。

---

## 7. 功能完备度矩阵

| 功能 | mem0 | SRH | hy-memory | 深度评价 |
|------|:----:|:---:|:---------:|:---------|
| **核心记忆 CRUD** | ✅ | ✅ | ✅ | 三者均完整 |
| **语义搜索** | ✅ | ✅ | ✅ | mem0 over-fetch ×4 最成熟 |
| **BM25 关键词** | ✅ | ✅ | ✅ | mem0 原生稀疏向量；SRH bm25s；hy-memory FTS5+jieba |
| **混合融合** | ✅ | ✅ | ✅ | mem0 加法 / SRH+hy-memory RRF |
| **重排序** | ✅ | ✅ | ⚠️ | mem0 6 种；SRH 2 种+MMR；hy-memory 无显式 reranker |
| **Entity 提取** | ✅ | ❌ | ⚠️ | mem0 spaCy 4 种；hy-memory 无显式 entity，但 L2 scene 有类似功能 |
| **图记忆** | ⚠️ | ✅ | ❌ | mem0 轻量 entity link；SRH Hebbian 完整；hy-memory 无 |
| **自动反射** | ❌ | ✅ | ✅ | SRH session-end / hy-memory L0→L1→L2→L3 管道 |
| **记忆策展** | ❌ | ✅ | ⚠️ | SRH 4-phase；hy-memory TTL only |
| **冷存储** | ❌ | ✅ | ⚠️ | SRH JSONL；hy-memory JSONL fallback |
| **TTL 管理** | ❌ | ✅ | ✅ | SRH `valid_until`；hy-memory `l0l1RetentionDays` |
| **技能系统** | ❌ | ✅ | ❌ | SRH SkillStore 独有 |
| **自动上下文注入** | ❌ | ✅ | ✅ | SRH 4 层 / hy-memory L1+L2+L3+offload |
| **生命周期钩子** | ❌ | ✅ | ✅ | SRH 4 个 / hy-memory OpenClaw/Hermes 钩子 |
| **文件级存储** | ❌ | ✅ | ❌ | SRH Markdown 独有 |
| **多向量存储** | ✅ | ❌ | ⚠️ | mem0 20+ 种；hy-memory SQLite/TCVDB |
| **多 LLM 支持** | ✅ | ❌ | ⚠️ | mem0 10+ 种；hy-memory 可配置 provider/model |
| **多嵌入模型** | ✅ | ❌ | ⚠️ | mem0 6+ 种；hy-memory OpenAI-compatible |
| **托管云服务** | ✅ | ❌ | ✅ | mem0 mem0.ai / hy-memory TCVDB |
| **REST API** | ✅ | ✅ | ✅ | 三者均有 |
| **Dashboard** | ✅ | ✅ | ✅ | 三者均有 |
| **Vision 支持** | ✅ | ❌ | ❌ | mem0 base64 + vision LLM |
| **CJK 感知** | ❌ | ✅ | ✅ | SRH token 估算；hy-memory jieba 分词 |
| **Prompt cache 优化** | ❌ | ❌ | ✅ | hy-memory stable/dynamic 分离独有 |
| **上下文压缩** | ❌ | ❌ | ✅ | hy-memory 三级压缩 + MMD 独有 |
| **崩溃恢复** | ❌ | ❌ | ✅ | hy-memory checkpoint + recovery 独有 |
| **并发控制** | ❌ | ✅ (RLock) | ✅ (SerialQueue) | hy-memory SerialQueue 最精致 |

---

## 8. 代码质量与架构债务

### 8.1 代码规模对比

| 系统 | 代码行数 | 语言 | 包结构 |
|------|---------|------|--------|
| **mem0** | ~15,000 行 | Python | memory/, vector_stores/, embeddings/, llms/, configs/ |
| **SRH** | ~9,860 行 | Python | core/, reflection/, memory/, runtime/, web/ |
| **hy-memory** | ~25,000+ 行 | TypeScript | core/, offload/, utils/, adapters/ |

### 8.2 抽象度对比

| 维度 | mem0 | SRH | hy-memory |
|------|------|-----|-----------|
| **Factory 模式** | 优秀（LLM/Embedder/Reranker/VectorStore） | 中等 | 中等（PipelineFactory） |
| **类型安全** | Pydantic v2 | 基础类型提示 | TypeScript 严格模式 |
| **配置验证** | 运行时验证 | 无验证 | JSON Schema |
| **测试覆盖** | 核心 API | 317 collected tests；v1.4 定向验收已验证 | Vitest (含 e2e) |
| **文档** | 完善（docs/, cookbooks/） | 项目内文档完善 | README + CHANGELOG |

### 8.3 已知问题

**mem0**:
- 无自动记忆生命周期管理
- 无图遍历算法
- 无 session 生命周期感知
- 每 add 一次 LLM 调用，成本高

**SRH**:
- 代码量超标: 9,710 行 vs 目标 3,200 行
- 无 entity 提取
- 无 Pydantic 配置
- 单向量存储（文件系统）

**hy-memory**:
- 代码量最大（~25K 行 TypeScript）
- 无图记忆系统
- 无显式版本控制（supersedes 链）
- L2/L3 依赖 LLM，成本高
- Node.js ≥22.16 要求较新

---

## 9. 学术前沿对齐评估

### 9.1 引用的学术工作

| 论文/概念 | mem0 | SRH | hy-memory | 评价 |
|-----------|:----:|:---:|:---------:|:-----|
| **HeLa-Mem** (加权乘法融合) | ❌ | ⚠️ | ❌ | SRH spreading activation 已实现，但融合是加法非乘法 |
| **Retrieval Bottleneck** (稀疏-密集交互) | ✅ | ⚠️ | ✅ | mem0 原生稀疏向量；hy-memory FTS5；SRH bm25s 独立索引 |
| **MemForest** (树状记忆) | ❌ | ❌ | ❌ | 三者均无 |
| **Hebbian Learning** (共同激活) | ❌ | ✅ | ❌ | SRH 核心设计 |
| **PageRank** (图重要性) | ❌ | ✅ | ❌ | SRH 图算法 |
| **Spreading Activation** (查询扩散) | ❌ | ✅ | ❌ | SRH 检索增强 |
| **RRF** (Reciprocal Rank Fusion) | ❌ | ✅ | ✅ | SRH + hy-memory 融合策略 |
| **Additive Normalization** (加法归一化) | ✅ | ❌ | ❌ | mem0 融合策略 |
| **Prompt Caching** (提示缓存优化) | ❌ | ❌ | ✅ | hy-memory stable/dynamic 分离 |
| **CJK Text Segmentation** (中文分词) | ❌ | ⚠️ | ✅ | hy-memory jieba 独有 |

### 9.2 学术差距详细分析

#### 差距 1: HeLa-Mem 乘法融合

**论文公式**: `S_total = S_semantic^α * S_lexical^β + S_hebbian`

**SRH 当前**: `score = base_score + hebbian_score` (加法)

**影响**: 在 multi-hop 查询上，乘法融合能更好地捕获 "语义相关 AND 图关联" 的交集体

**修复难度**: 中 — 需修改 `core/search.py:570-589`

#### 差距 2: MemForest 树状层级

**论文概念**: MemTree（平衡 k 叉树），每次插入 O(log N)

**三者状态**: 均未实现

**影响**: 平面记忆列表在规模增大时检索质量下降

**修复难度**: 高 — 需重构存储模型

#### 差距 3: 上下文压缩的学术研究

hy-memory 的三级压缩（aggressive + mild + emergency）是一个工程创新，但缺乏学术研究支撑。相关领域：
- **Hierarchical Neural Memory** (arXiv:2402.xxxxx): 分层记忆的神经压缩
- **MemGPT**: 操作系统风格的内存分页

hy-memory 的 MMD (Memory Meta Data) 注入类似于 MemGPT 的 "paging" 概念，但实现更轻量。

---

## 10. 三向设计借鉴

### 10.1 SRH 可向 mem0 / hy-memory 学习

1. **Entity 提取系统**: mem0 的 spaCy 实体提取非常成熟
2. **多向量存储抽象**: mem0 的 `VectorStoreFactory` 支持 20+ 种存储
3. **Pydantic 配置**: mem0 的 `MemoryConfig` 类型安全
4. **元数据过滤操作符**: mem0 支持 eq/ne/gt/AND/OR/NOT
5. **CJK 分词**: hy-memory 的 jieba + FTS5 方案
6. **Prompt cache 优化**: hy-memory 的 stable/dynamic 分离
7. **上下文压缩**: hy-memory 的三级压缩机制
8. **崩溃恢复**: hy-memory 的 checkpoint + recovery

### 10.2 mem0 可向 SRH / hy-memory 学习

1. **自动反射管道**: SRH 的 session-end reflection + hy-memory 的 L0→L1→L2→L3 管道
2. **Hebbian 图**: SRH 的 co-activation 图
3. **文件级存储**: SRH 的 Markdown 导出层
4. **生命周期钩子**: SRH 的 hooks + hy-memory 的 OpenClaw 集成
5. **技能系统**: SRH 的 SkillStore
6. **Token 预算管理**: SRH 的上下文组装 + hy-memory 的三级压缩
7. **CJK 支持**: hy-memory 的 jieba 分词
8. **上下文压缩**: hy-memory 的 MMD 注入

### 10.3 hy-memory 可向 mem0 / SRH 学习

1. **图记忆系统**: SRH 的 Hebbian 图 + PageRank
2. **文件级存储**: SRH 的 Markdown + YAML frontmatter
3. **版本控制**: SRH 的 supersedes 链
4. **策展管道**: SRH 的 4-phase curation
5. **多向量存储**: mem0 的 VectorStoreFactory
6. **Entity 提取**: mem0 的 spaCy 实体系统
7. **技能系统**: SRH 的 SkillStore

---

## 11. 对 v1.4 的改进建议与落地状态（2026-06-09 更新）

### P0 — 立即收益

1. **CJK 分词支持**: 已落地。当前 v1.4 已支持 `auto | bigram | jieba`，并在无 `jieba` 环境下 fail-open 回退。
2. **Prompt cache 友好的 stable/dynamic 分离**: 已部分落地。当前 v1.4 已在内部引入 `ContextBundle` stable/dynamic 结构，并保留旧 host 的字符串兼容返回；宿主双通道协议仍可作为后续增强。
3. **崩溃恢复**: 已落地。当前 v1.4 已增加 runtime checkpoint、pending stage 标记、session-start best-effort recovery。

### P1 — 中期价值

4. **上下文压缩**: 已落地基础版。当前 v1.4 已支持 `none / mild / aggressive / emergency` 四级结构化压缩，不默认引入高成本 LLM 压缩。
5. **HeLa-Mem 乘法融合**: 尚未落地。当前仍是 RRF + rerank + Hebbian additive boost。
6. **Pydantic 配置迁移**: 已部分落地。当前 v1.4 已加入 typed config diagnostics 和 dataclass 模型，尚未扩展到全量配置面。
7. **向量存储抽象**: 仅完成 capability abstraction 预埋。当前尚未引入 mem0 式多后端工厂。

### P2 — 长期探索

8. **MemForest 层级**: 未开始，仍属于结构级重构议题。
9. **多模态嵌入**: 未开始。
10. **学术基准测试**: 未开始，后续适合在功能稳定后独立推进。

### 本轮 v1.4 额外落地

除上述建议外，v1.4 还补齐了两项原报告特别强调的能力：

1. **Entity recall layer**: 已落地 SQLite `entities` / `entity_links` 索引、query entity boost、`entity_hits` explain 诊断。
2. **Search explainability**: 已落地 `search_explain()` / `fusion_search_explain()` 和 `srh_memory_search.explain=true`。

---

## 12. 结论

### 核心定位

```
mem0              →  "记忆的硬盘"   (可靠存储、高效检索、基础设施完备)
mem-reflection-hermes →  "记忆的大脑"   (自治演化、自主策展、图记忆驱动)
hy-memory         →  "记忆的消化系统" (分层提炼、上下文压缩、符号化卸载)
```

### 适用场景

| 场景 | 推荐系统 | 理由 |
|------|----------|------|
| **通用 Python 应用** | mem0 | 一行代码调用，20+ 存储选择 |
| **Hermes Agent 插件** | SRH | 深度集成，自治演化 |
| **OpenClaw 插件** | hy-memory | 原生支持，分层管道 |
| **CJK 内容为主** | hy-memory | jieba 分词，FTS5 支持 |
| **需要图记忆** | SRH | Hebbian + PageRank + spreading activation |
| **需要上下文压缩** | hy-memory | 三级压缩 + MMD |
| **需要文件审计** | SRH | Markdown + YAML frontmatter |
| **需要 prompt cache** | hy-memory | stable/dynamic 分离 |

### 设计取舍总结

| 取舍 | mem0 | SRH | hy-memory |
|------|------|-----|-----------|
| **融合策略** | 加法归一化 | RRF | RRF |
| **图类型** | Entity（内容） | Hebbian（行为） | 无 |
| **写入策略** | 逐条 LLM 提取 | 批量反射 | 分层管道批量提取 |
| **存储模型** | 向量数据库 | 文件系统 | SQLite 分层 |
| **生命周期** | 无状态 | 会话感知 | 管道调度 |
| **配置系统** | Pydantic | YAML dict | TypeScript 接口 |
| **上下文管理** | 手动 | 4 层自动注入 | 分层注入 + 压缩 |
| **语言栈** | Python | Python | TypeScript |

### 最终评价

**mem0** 是**工程成熟度最高**的记忆库，适合需要可靠存储和高效检索的通用场景。它的 Factory 模式、多存储支持、Pydantic 配置和完善的文档都是生产级品质。

**SRH** 是**学术对齐最深**的记忆插件，适合需要记忆自治演化的 Agent 场景。它的 Hebbian 图、4-phase 策展、4 层上下文注入和生命周期感知都是独特能力。

**hy-memory** 是**上下文管理最精细**的记忆系统，适合长对话场景和 CJK 内容。它的 L0→L1→L2→L3 分层管道、jieba CJK 分词、三级上下文压缩和 prompt cache 优化都是生产级的工程创新。

**三者不是竞争关系，而是互补关系**。一个理想的 Agent 记忆系统应该融合：
- **mem0 的基础设施成熟度**（多存储、Pydantic、Factory）
- **SRH 的自治演化能力**（Hebbian 图、策展、反射）
- **hy-memory 的上下文压缩技术**（三级压缩、MMD、stable/dynamic 分离）

---

*报告结束。本报告基于对 mem0 v2.0.4、mem-reflection-hermes v1.3-beta 基线代码与其 v1.4 落地结果、以及 hy-memory v0.3.6 源代码的逐行分析。学术评价基于 HeLa-Mem (arXiv:2604.16839)、Retrieval Bottleneck (arXiv:2603.02473) 和 MemForest (arXiv:2605.23986) 三篇论文。*
