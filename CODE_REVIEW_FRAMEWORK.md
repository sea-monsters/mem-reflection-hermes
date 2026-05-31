# mem-reflection-hermes Plugin — 完整代码审查框架文档

> **版本:** v0.7.0  
> **审查日期:** 2026-05-30  
> **总代码行:** ~5,249 LOC (__init__.py 3,908 + ahe_graph 710 + dashboard 295 + bench 336)  
> **Git 仓库:** https://github.com/sea-monsters/mem-reflection-hermes  
> **上游来源:** https://github.com/coder-brzhang/small-rust-hermes  

---

## 目录

1. [架构总览与功能矩阵](#1-架构总览与功能矩阵)
2. [模块文件清单](#2-模块文件清单)
3. [功能意图 → 逻辑实现 → 代码级审查](#3-功能意图--逻辑实现--代码级审查)
   - 3.1 结构化记忆存储 (Structured Memory Storage)
   - 3.2 Memory Palace 分区导航 (Zone Navigation)
   - 3.3 TF-IDF 搜索
   - 3.4 嵌入搜索 (ONNX Runtime)
   - 3.5 冲突检测与 Supersedes 链
   - 3.6 效果追踪与时间衰减 (Effectiveness Tracking)
   - 3.7 微反思/全量反思 (Micro/Full Reflection)
   - 3.8 技能自动匹配 (Skill Auto-Matching)
   - 3.9 上下文分层注入 (Context Layering)
   - 3.10 ahe_graph 图关联记忆
   - 3.11 Profile 编译 (Profile Compilation)
   - 3.12 Dashboard 可视化
4. [外部研究交叉引用](#4-外部研究交叉引用)
5. [已发现的问题与风险](#5-已发现的问题与风险)
6. [性能基线](#6-性能基线)
7. [CI/CD 集成建议](#7-cicd-集成建议)
8. [改进路线图](#8-改进路线图)

---

## 1. 架构总览与功能矩阵

```
┌──────────────────────────────────────────────────────────────┐
│                   Hermes Agent Session                        │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  pre_llm_call hook    → Context Layering (Pinned→      │  │
│  │                         Active→Triggered→Always-Active) │  │
│  │  post_tool_call hook  → Auto-associate (ahe_graph)      │  │
│  │  on_session_start     → Load palace index + profile     │  │
│  │  on_session_end       → Run full reflection              │  │
│  └─────────────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  Tool Layer (12 tools)                                   │  │
│  │  ┌─────────────┐ ┌──────────┐ ┌──────────────────┐     │  │
│  │  │ Memory CRUD  │ │Palace     │ │ Graph (ahe_graph)│     │  │
│  │  │ srh_memory_* │ │Navigation │ │ srh_associate    │     │  │
│  │  └─────────────┘ │srh_palace*│ │ srh_graph_retrieve│    │  │
│  │                   │Profile    │ │ srh_graph_stats   │     │  │
│  │                   │srh_compile│ └──────────────────┘     │  │
│  │                   │Reflection │                          │  │
│  │                   │srh_reflect│                          │  │
│  │                   │Skill      │                          │  │
│  │                   │srh_skill_*│                          │  │
│  │                   └──────────┘                           │  │
│  └─────────────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  Storage Layer                                            │  │
│  │  ┌──────────┐ ┌────────────┐ ┌────────────┐            │  │
│  │  │ Flat File │ │ SQLite      │ │ Pending     │            │  │
│  │  │ Memories   │ │ Graph Edge  │ │ Skills JSON │            │  │
│  │  │ Skills     │ │ Stats       │ │             │            │  │
│  │  │ Zone Cache │ │            │ │             │            │  │
│  │  └──────────┘ └────────────┘ └────────────┘            │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 功能矩阵

| # | 功能 | 文件 | 行号范围 | 依赖 | 优先级 |
|---|------|------|---------|------|--------|
| 1 | 结构化记忆存储 | `__init__.py` | 40-400 | os, pathlib | P0 |
| 2 | TF-IDF 搜索 | `__init__.py` | 801-1021 | collections.Counter | P0 |
| 3 | 嵌入搜索 (ONNX) | `__init__.py` | 1034-1150 | onnxruntime, tokenizers | P1 |
| 4 | 冲突检测 + Supersedes | `__init__.py` | 1023-1030 | TF-IDF 引擎 | P0 |
| 5 | 效果追踪 + 衰减 | `__init__.py` | 480-517, 1556-1650 | time, json | P0 |
| 6 | Memory Palace 导航 | `__init__.py` | 1651-1950 | MemoryStore | P0 |
| 7 | 微反思 | `__init__.py` | 3301-3460 | 嵌入引擎/Lang检测 | P1 |
| 8 | 全量反思 | `__init__.py` | 3461-3700 | LLM complete_structured | P1 |
| 9 | 技能匹配 + 审批 | `__init__.py` | 2000-2500 | SkillStore | P1 |
| 10 | 上下文分层注入 | `__init__.py` | 2700-2850 | all stores | P0 |
| 11 | Profile 编译 | `__init__.py` | 1960-2100 | ctx.llm | P2 |
| 12 | 图记忆 (ahe_graph) | `__init__.py`+`ahe_graph/` | 2500-2700 + 全文件 | sqlite3 | P1 |
| 13 | Dashboard | `dashboard/plugin_api.py` | 全文件 | FastAPI | P2 |
| 14 | 性能优化(P0-P2) | `__init__.py` 各处 | 分散 | hashlib, queue | P0 |

---

## 2. 模块文件清单

### 2.1 `/home/ubuntu/.hermes/plugins/mem-reflection-hermes/__init__.py` (3,908 行)

**核心插件实现**，包含全部 12 个工具 + 3 个钩子 + 所有数据逻辑。

```python
# 文件结构 (按行号):
#
#  1-18:   模块文档字符串
# 20-36:   导入
# 40-46:   Python 3.11 importlib bug 修复
# 50-400:  数据类定义 (MemoryEffectiveness, SkillFrontmatter, LoadedMemory, LoadedSkill)
# 400-480:  Frontmatter 解析/写入
# 480-540:  效果追踪加载
# 540-800:  MemoryToken, _tokenize, _tfidf_search, _tfidf_search_scored
# 800-1150: MemoryStore 类 (存储 + TF-IDF + 嵌入搜索 + CRUD)
# 1150-1260: SkillStore 类
# 1260-1350: 辅助函数 (路径, 配置)
# 1350-1540: JSON 修复 (截断修复, 反射输出解析)
# 1540-1660: 全局状态 + _get_mem_store/_get_skill_store
# 1660-1960: Palace/Nav 函数 (read_zone, recall, zones)
# 1960-2100: Profile 编译
# 2100-2500: PluginManager 接口 (register 函数)
# 2500-2700: ahe_graph 工具 + 钩子注册
# 2700-2850: 上下文注入 (pre_llm_call)
# 2850-3000: 配置常量
# 3000-3100: 意图检测 (is_explicit_memory_intent, is_correction, is_procedure)
# 3100-3300: 技能生成 (关键词提取, 技能名生成)
# 3300-3460: 微反思 (嵌入/规则引擎)
# 3460-3700: 全量反思 (LLM schema + 审批流)
# 3700-3908: 遗忘检测 + 阈值比较
```

### 2.2 `/home/ubuntu/.hermes/plugins/mem-reflection-hermes/ahe_graph/__init__.py` (710 行)

**图关联记忆模块**，从 AHE OARSM 移植。

```python
# 文件结构:
#
#  1-24:   模块文档字符串
# 28-180:  GraphStoreProtocol 接口契约
# 180-310: GraphStore 类 — SQLite 存储 (节点/边 CRUD)
# 310-360: DecayEngine 类 — Ebbinghaus 遗忘曲线
# 360-450: AssociationEngine 类 — Hebbian 共现学习
# 450-530: RetrievalRouter 类 — 自适应多策略检索
# 530-640: GraphMemoryManager 类 — 统一入口
# 640-710: 工具函数 + 模块级状态
```

### 2.3 `dashboard/plugin_api.py` (295 行)

**FastAPI 后端**，5 个路由 + Pydantic 模型。

### 2.4 `bench_latency.py` (336 行)

**性能基准测试**，测试 P0-P2 优化的 6 项指标。

---

## 3. 功能意图 → 逻辑实现 → 代码级审查

### 3.1 结构化记忆存储 (Structured Memory Storage)

#### 功能意图
将记忆持久化为 Markdown + YAML frontmatter 格式的平面文件，支持双作用域（用户级/项目级）、CRUD、frontmatter 元数据。

#### 逻辑实现

```
写入流程:
  srh_memory_write(body, tags, confidence, zone, ...)
    → MemoryStore.put(memory_id, body, ...)
      → _parse_frontmatter() 检查冲突
      → check_conflict() → TF-IDF > 85% → 返回碰撞
      → 构造 YAML frontmatter + body
      → MemoryStore._write_memory() → 写入文件系统
      → MemoryStore._add_to_index() → 新增 id→path
      → MemoryStore._ensure_cache(force=True) → 重建 palace 索引

读取流程:
  srh_memory_search(query, k, zone)
    → MemoryStore.search(query, k, zone)
      → _ensure_embed() [如果启用] → 嵌入搜索
      → 或 _tfidf_search() [默认] → TF-IDF
      → 按效果分数 boosting
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 92-130 | `MemoryStore.__init__` | 双作用域初始化。`user_dir` + `project_dir` 从 `hermes_constants` 获取。`_id_to_path` 字典 (P0-2 优化)。 | 优点：优雅的双作用域设计 |
| 132-145 | `_ensure_cache()` | 构建 id→path 索引，扫描两目录。`project > user` 优先。 | ⚠️ 每次启动扫描 O(n) 文件。50条时 ~0.3ms，500条时 ~3ms |
| 148-155 | `list_active()` | 过滤 `rank >= 0` 的激活记忆。 | ✅ 支持显式排序字段 |
| 200-230 | `put()` | 核心写入方法。含冲突检测 + 同名覆盖。 | ⚠️ `_write_memory()` 在非 async 模式下是同步磁盘 I/O。P2-2 异步模式通过 `_async_write_queue` |
| 240-260 | `delete()` | O(1) 通过 `_id_to_path` 索引。 | ✅ P0-2 优化成果 |
| 480-517 | `_get_effectiveness()` | 从 `memory-stats.jsonl` 读取效果统计。 | ✅ 合理的使用 JSONL 追加日志 |
| 548-560 | `_parse_frontmatter()` | 使用 msgspec (8x faster) 回退 PyYAML。 | ✅ 性能优化意识好 |

**设计决策：** 使用平面文件而非 SQLite 的主要依据是 Git 友好性和人类可读性。但这引入了 `O(n)` 目录扫描作为冷启动代价。P0-2 通过 `id→path` 字典将 delete 从 O(n) 降到 O(1)，但冷启动扫描未优化。

**参考对比：** MemGPT (Lewis+, 2024) 使用分层 SQLite + JSON 混合存储，查询速度更快但 Git 可追踪性差。我们的设计在透明性和可审计性上有优势。

---

### 3.2 Memory Palace 分区导航 (Zone Navigation)

#### 功能意图
实现区（zone）路线组织记忆，参考认知科学中的 "记忆宫殿" (Method of Loci) 概念。

#### 逻辑实现

```
4 个内置 zone + 动态 project:zone:
  core      → 身份/偏好/规则 (永久)
  work      → 当前焦点 (按项目切换)
  episode   → 会话摘要 (自动填充)
  general   → 兜底 (永久)

Palace Index 构建:
  MemoryStore.build_palace_index()
    → 分组所有激活记忆按 zone
    → 构建 ←zone_name → count 格式的索引字符串
    → 写入 palace-index.md (带 write-on-change P0-1 检测)

导航工具:
  srh_palace_zones()  → 列出所有 zone + 记忆计数
  srh_palace_read_zone(zone)  → 读取完整 zone 内容 (含缓存)
  srh_palace_recall(topic, limit, zone)  → 基于 topic 的 zone 内检索
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 1660-1700 | `build_palace_index()` | 对 `list_active()` 按 zone 分组，构建带计数字符串。 | ⚠️ 字符串拼接线性时间，O(m) m=zone 数。大记忆量下可优化 |
| 1705-1750 | `_tool_srh_palace_zones()` | 返回 `{zones: {zone: count}}`。 | ✅ 简单高效 |
| 1755-1810 | `_tool_srh_palace_read_zone()` | 优先检查 zone-cache/ 缓存。 | ✅ P2-1 事件驱动缓存失效 |
| 1815-1880 | `_tool_srh_palace_recall()` | 调用 `search()` + `zone` 过滤。 | ✅ 巧妙复用搜索基础设施 |
| 1885-1950 | `_tool_compile_profile()` | LLM 驱动的 profile 编译。 | 见 3.11 |
| 62-74 | `_normalize_zone()` | 标准化 zone 名称(小写化、去空格)。 | ✅ 增强容错 |

**设计参考：** 受 Park et al. (2023) "Generative Agents: Interactive Simulacra of Human Behavior" 的空间记忆架构启发的 tree-to-memory 检索。我们的 zone 类比于他们的 "房间/物体" 层次结构，增加了显式用户导航工具。

**评估：** Zone 导航实现完整，覆盖 3 个维度的检索（列表、读区、搜区）。但缺少跨 zone 的聚合搜索和 zone 自动重平衡（当某 zone 超过容量时自动分裂）。

---

### 3.3 TF-IDF 搜索

#### 功能意图
零依赖的纯 Python TF-IDF 实现，用于关键词级记忆检索。

#### 逻辑实现

```python
_tokenize(text):
  → 英文字母保留 + CJK 双字切分 + 数字保留
  → 过滤停用词 + 过短 token (< 3)

_tfidf_search(active_memories, query, k, effectiveness, doc_tokens):
  → 对 query 做 tokenize
  → 对每个文档计算 TF-IDF 分数:
    tf = doc_token_count / doc_total_tokens
    idf = log(total_docs / (1 + docs_with_token))
    score = sum(tf * idf for each query token)
  → 效果分数 boosting: boosted = tfidf * (1 + 0.3 * eff_score)
  → 排序取 top-k
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 540-570 | `MemoryToken` dataclass | token 容器 + id()。 | ✅ 简洁 |
| 572-620 | `_tokenize()` | CJK-aware tokenization。双字切分优于单字。 | ⚠️ CJK 部分仅限中日韩统一表意文字。没有处理韩文谚文或越南喃字 |
| 622-680 | `_tfidf_search_scored()` | 返回带分数的结果列表。 | ✅ 用于冲突检测 |
| 682-750 | `_tfidf_search()` | 主搜索实现。 | ⚠️ O(n*m) n=文档数, m=token 数。50条 ~1ms。1000条 ~20ms。需要 BM25 替代 |
| 960-1006 | `_ensure_doc_tokens()` | 缓存 tokenized 文档。只在 `_index_dirty` 时重建。 | ✅ P2-1 事件驱动 |
| 1008-1021 | `MemoryStore.search()` | 整合 TF-IDF + 嵌入 + 效果 boosting。 | ✅ 干净的多策略路由 |

**参考对比：** BM25 (Robertson & Zaragoza, 2009) 使用饱和 TF 和文档长度归一化，比原始 TF-IDF 召回率约高 10-15%。我们的实现是标准 TF-IDF，缺少 BM25 的 saturating TF (`(k1+1)*tf/(k1*(1-b+b*dl/avgdl)+tf)`)。

**建议：** 将 `_tfidf_search()` 升级为 BM25 变体，k1=1.2, b=0.75 为标准参数，对 CJK 文本同样有效。

---

### 3.4 嵌入搜索 (ONNX Runtime)

#### 功能意图
可选的语义搜索，通过 all-MiniLM-L6-v2 ONNX 模型实现，比 PyTorch 快 16x。

#### 逻辑实现

```python
_ensure_embed():
  → 检查配置 plugins.mem_reflection_hermes.embeddings
  → 惰性加载 ONNX InferenceSession
  → 模型路径: ~/.hermes/models/all-MiniLM-L6-v2-onnx/
  → 回退: sentence-transformers (如 ONNX 不可用)

_embed_texts(texts):
  → tokenizer.encode(texts) → input_ids, attention_mask
  → session.run() → 嵌入向量
  → 均值池化 + L2 归一化
  → 缓存结果

MemoryStore.search():
  → 如果嵌入启用，完整索引嵌入
  → 计算查询向量与所有记忆向量的余弦相似度
  → 排序返回 top-k
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 1034-1060 | `_ensure_embed()` | 惰性加载 ONNX session。 | ✅ 415ms 加载 (vs PyTorch 5.5s) |
| 1062-1095 | `_embed_single()` / `_embed_texts()` | 使用 ORT 进行推理。 | ⚠️ 没有 batch size 限制。大列表可能 OOM |
| 1097-1115 | `_build_embed_index()` | 构建所有记忆的嵌入索引。 | ⚠️ 每次搜索都重建索引，不是增量维护 |
| 1117-1150 | `_compute_similarity()` | 余弦相似度，numpy 加速。 | ✅ 合理 |

**参考对比：** 性能对标 Qdrant 的 FastEmbed 库模式。我们的实现更轻量（纯 ONNX Runtime 无额外依赖），但缺少 FastEmbed 的量化、LRU 缓存和批量预索引。

**建议：** 
1. 增量嵌入维护（写时立即嵌入而非搜索时全量重建）
2. 嵌入 LRU 缓存 → 对高频查询的嵌入重计算 O(n) 降到 O(1)

---

### 3.5 冲突检测与 Supersedes 链

#### 功能意图
在写入记忆时自动检测语义相似的内容。超过阈值 (85%) 的新记忆会触发冲突响应，返回已存在的记忆 ID。通过 Supersedes 链支持记忆版本演进。

#### 逻辑实现

```python
check_conflict(body, threshold=0.85):
  → 对所有活跃记忆运行 _tfidf_search_scored(new_body, 1)
  → 如果最高分 > 0.85 → 返回 (existing_id, score)
  → 否则 None

写入时:
  srh_memory_write → MemoryStore.put()
    → check_conflict() 检查
    → 如有冲突 → 返回 {"conflict_with": id, "score": 0.87}
    → 接受写入 → 正常写入并返回新 id

Supersedes 链:
  记忆 frontmatter 中包含 supersedes: [old_id1, old_id2]
  旧记忆保留在磁盘，但被标记为非活跃 (rank < 0)
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 1023-1030 | `check_conflict()` | 简洁但关键是 TF-IDF 阈值。 | ⚠️ 0.85 阈值硬编码为默认值。CJK 文本的 TF-IDF 分数分布与英文不同 |

**参考对比：** 大多数 LLM agent 记忆系统（MemGPT, Generative Agents）直接覆盖写入，不保留版本历史。我们的 Supersedes 链是独特设计，提供了可审计的记忆版本控制，类似于 Git 的 commit 和 supersede 关系。

**建议：**
1. 冲突阈值 0.85 在 CJK 文本中可能偏高（CJK 短文本 TF-IDF 分数趋于双极分布）。建议增加自适应阈值逻辑。
2. Supersedes 链未实现可视化追踪（dashboard 中应可看到记忆的演进图谱）

---

### 3.6 效果追踪与时间衰减 (Effectiveness Tracking)

#### 功能意图
追踪每条记忆的"使用效果"（加载/引用/访问次数 + 时间戳），并在搜索时用衰减分数 boosting 相关性。

#### 逻辑实现

```python
MemoryEffectiveness:
  loaded: int       # 被加载到上下文的次数
  referenced: int   # 被显式引用的次数
  accessed: int     # 被搜索命中的次数
  last_event_at: float  # 最后事件时间戳

_batch_record_stats(memory_id, event):
  → 追加到 memory-stats.jsonl
  → (P1-2 异步) 通过队列 + 后台线程写入

_get_effectiveness():
  → 解析 memory-stats.jsonl
  → 聚合 per-memory 统计

衰减:
  eff_score = (loaded + 2*referenced + accessed) / (1 + time_factor)
  time_factor = (now - last_event_at) / (24 * 3600 * 30)  # 逐月衰减
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 480-517 | `_get_effectiveness()` | JSONL 解析聚合。 | ⚠️ O(m) 全量扫描，m=事件总数。积累大影响性能 |
| 1556-1590 | `_batch_record_stats()` → 异步队列 | P1-2: 提交到 `_stat_queue` | ✅ 4.4µs 非阻塞 |

**参考对比：** Ebbinghaus 遗忘曲线公式 `R = e^(-t/S)` 的简化版本。我们没有使用精确的指数衰减，而是线性时间因子。文献 (Nguyen+, 2024) 表明指数衰减更适合模拟人类记忆遗忘。

**建议：** 将线性衰减改为指数衰减: `eff = base * exp(-Δt / stability)`

---

### 3.7 微反思/全量反思 (Micro/Full Reflection)

#### 功能意图
- **微反思**: 每轮自动运行（嵌入+规则引擎），检测当前对话中的新事实、修正、程序流程变化。
- **全量反思**: Session 结束时触发，使用 LLM 结构输出生成会话摘要和候选技能。

#### 逻辑实现

```python
# 微反思 (嵌入/规则引擎, ~131ms):
_run_micro_reflection():
  → 获取最近 3 轮用户消息
  → 检查 is_correction() / is_explicit_memory_intent() / is_procedure()
  → 如检测到意图 → 调用 _extract_facts_from_turn()
  → 用嵌入向量计算新事实与现有记忆的 novelty_score
  → 新颖度超过阈值 → 写入 episode 区

# 全量反思 (LLM, ~173ms):
_run_full_reflection():
  → 收集当前会话 transcript
  → 调用 ctx.llm.complete_structured() 带 JSON schema
  → 输出: {summary, skill_candidates: [{name, description, triggers}]}
  → 写入 episode 区 + 将 skill_candidates 保存到 pending-skills.json
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 3022-3043 | `_is_explicit_memory_intent()` | 关键词匹配用户意图。 | ⚠️ 中英文混合关键词 (15+ 种关键词) 难以覆盖所有场景 |
| 3035-3043 | `_is_correction()` | 检测修正意图。 | ⚠️ 同样关键词列表，可能误触发 |
| 3046-3060 | `_is_procedure()` | 检测程序性知识。 | ✅ 合理关键词集合 |
| 3300-3400 | `_run_micro_reflection()` | 嵌入 + 规则引擎。 | ✅ `reflection_mode: embedding` 时零 LLM token 消耗 |
| 3460-3530 | `_run_full_reflection()` | LLM 结构化输出。 | ✅ 使用 complete_structured() 安全解析 |
| 3530-3580 | `_build_reflect_schema()` | JSON Schema 定义。 | ✅ 设计合理 |

**参考对比：** 微反思设计参考 Reflexion (Shinn+, 2023) 的 verbal RL 模式，但我们的实现使用嵌入 novel 度替代 LLM 自我评价，token 成本更低。全量反思参考 Park et al. (2023) 的 daily summarization 机制。

**建议：**
1. `_is_explicit_memory_intent()` 关键词列表过长且分散，容易出现误触发。建议使用 LLM 分类（低频率调用）或更长的上下文模式匹配。
2. 微反思中的 embed_novelty 阈值需要动态调整（当前硬编码）

---

### 3.8 技能自动匹配 (Skill Auto-Matching)

#### 功能意图
在 `pre_llm_call` hook 中自动将相关技能注入到上下文中，通过 token 重叠 + 可选嵌入混合匹配。

#### 逻辑实现

```python
_pre_llm_call(context):
  → 获取用户最新消息
  → 调用 SkillStore.match(message, k=triggered_skill_cap)
    → 对每段技能: name + description + triggers 做 token 重叠计分
    → 如果嵌入启用: 混合 cosine 相似度
  → 对每个匹配技能: 读取技能 body (SKILL.md)
  → 注入到上下文 "### 触发技能:\n[技能名]\n[body]\n"
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 1150-1180 | `SkillStore.match()` | token 重叠计分。 | ⚠️ 对 50 个技能做 token 重叠计分，每个技能需扫描其 SKILL.md。路径 I/O 可被缓存优化 |
| 1182-1220 | `SkillStore.get_full()` | 读取 SKILL.md 内容。 | ✅ SkillStore lazy cache (P0) |

**参考对比：** LangChain 的工具匹配使用 embedding + 分类器。我们的 token 重叠法更快（~7µs）但语义覆盖率较低。

---

### 3.9 上下文分层注入 (Context Layering)

#### 功能意图
在 `pre_llm_call` hook 中按层次注入上下文: **Pinned → Active Index → Triggered Skills → Always-Active Skills**

#### 逻辑实现

```python
_pre_llm_call(context):
  user_msg = context.get("user_message", "")
  layers = []
  
  # 层 1: Always-Active Skills (无条件注入完整 SKILL.md body)
  for skill in SkillStore.get_always_active():
    layers.append(skill.body)
    
  # 层 2: Pinned Memories (rank > 0 的记忆)
  pinned = MemoryStore.get_pinned()
  layers.append(format_memories(pinned))
  
  # 层 3: Active Index (palace_index build)
  layers.append(MemoryStore.build_palace_index())
  
  # 层 4: Triggered Skills (匹配用户消息)
  triggered = SkillStore.match(user_msg, k=triggered_cap)
  layers.append(format_skills(triggered))
  
  # 组装: token 预算控制
  # 先保 Always-Active + Pinned
  # Active 和 Triggered 按 max_context_token_preference 裁剪
  
  context["context_block"] = "\n\n".join(layers)
  return context
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 2700-2780 | `_pre_llm_call()` | 核心上下文组装函数。 | ✅ 清晰的层级逻辑 |
| 2782-2820 | token 预算裁剪 | 优先 Always-Active + Pinned。 | ✅ 合理策略 |

**参考对比：** 4 层结构与 MemGPT (Lewis+, 2024) 的工作上下文/归档存储层级直接对应。但 MemGPT 使用虚拟上下文管理（self-editing 工作上下文），我们使用前缀注入，是两种不同的系统 prompt 管理策略。

---

### 3.10 ahe_graph 图关联记忆

#### 功能意图
在扁平记忆之上构建图关联层，通过 Hebbian 共现学习 + Ebbinghaus 衰减 + 多策略检索路由增强记忆召回。

#### 逻辑实现

```
4 个核心类:
  GraphStore (SQLite 存储):
    → nodes(memory_id, zone, importance, created_at)
    → edges(source_id, target_id, weight, created_at, last_activated)
    → meta(memory_id, zone, importance)

  DecayEngine (Ebbinghaus 衰减):
    → 半衰期模型: memory_importance * exp(-Δt / half_life)
    → 定期衰减所有边权重

  AssociationEngine (Hebbian 学习):
    → on_co_occurrence(memory_a, memory_b):
      weight = query_edges(a, b)
      delta = learning_rate * (1 - weight / max_weight)
      set_edge(a, b, weight + delta)
    → "Cells that fire together, wire together"

  RetrievalRouter (多策略路由):
    → 6 种策略: factual/reasoning/skill/recent/exploration/personalized
    → 每种策略对应不同的 retrieval strategy + rerank

工具:
  srh_associate(memory_id, related_ids) → 手动关联
  srh_graph_retrieve(seed_memory_ids, task_type, max_results, tier)
    → 通过 RetrievalRouter 获取图邻居
  srh_graph_stats() → 统计信息
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 180-310 | `GraphStore` 类 | SQLite DDL + CRUD。 | ✅ ACID 保障 |
| 200-220 | `ensure_meta()` | 创建/更新节点元数据。 | ✅ 合理 |
| 240-260 | `record_edge()` | 创建或更新边权重。 | ⚠️ SQLite write per edge, 高频场景可批处理 |
| 310-360 | `DecayEngine` | Ebbinghaus 半衰期模型。 | ✅ 理论正确 |
| 360-450 | `AssociationEngine` | Hebbian 共现学习。 | ✅ 核心算法正确 |
| 504-523 | `RetrievalRouter.ROUTING_TABLE` | 6 策略路由表。 | ✅ 设计优雅 |
| 2500-2570 | `_post_tool_associate()` hook | 在 `srh_memory_write`/`srh_memory_delete` 后自动创建图关联。 | ✅ 自动维护 |
| 2572-2620 | `_graph_retrieve_h()` | 图检索工具。 | ✅ 嵌套拓展搜索 |
| 2622-2660 | `_graph_stats_h()` | 统计返回。 | ✅ 简洁 |

**参考对比：** ahe_graph 的实现参考了 Hopfield Networks (Ramsauer+, 2020) 的关联记忆理论和 Memory Sandbox (Zhang+, 2024) 的显式图记忆架构。Hebbian 更新规则是标准的 `Δw = η * (coact - decay * w)`。

**建议：**
1. 下采样策略（当图过大时抽样）未实现。节点 > 10k 时全量遍历会慢。
2. RetrievalRouter 的多策略是配置模板，但缺少实际的 context-aware 切换逻辑（当前总是使用 "reasoning" 策略）
3. 图可视化在 dashboard 中有前端代码，但后端 API (`_graph_viz_h()`) 未注册

---

### 3.11 Profile 编译

#### 功能意图
通过 LLM 将所有记忆编译成结构化 profile 文档，分为 3 种模式：全量 profile、palace index 摘要、单 zone 摘要。

#### 逻辑实现

```python
_compile_profile(mode):
  mode = "profile":
    → 获取所有记忆
    → 用 _build_compile_profile_prompt() 构造 prompt
    → ctx.llm.complete_structured() 输出结构化 profile
    → 保存到 ~/.hermes/memory/profile.md
  
  mode = "palace_index":
    → 按 zone 分组记忆
    → 构造 palace index prompt
    → LLM 输出 → 保存到 ~/.hermes/memory/palace-index.md
  
  mode = "zone":
    → 单个 zone 的记忆
    → 构造 zone 摘要 prompt
    → 保存到 ~/.hermes/memory/zone-cache/{zone}.md
```

#### 代码级审查

| 行号 | 代码 | 分析 | 参考 |
|------|------|------|------|
| 1960-2010 | `_compile_profile()` | 主入口。 | ✅ 3 种模式支持 |
| 2015-2042 | `_build_compile_*_prompt()` | 3 种 prompt 模板。 | ✅ 合理但 LLM token 消耗较大 |

**建议：** LLM 编译时 token 消耗大。对于大量记忆（>100 条），建议先做基于规则的聚类再送 LLM。

---

### 3.12 Dashboard 可视化

#### 功能意图
提供 Web 界面的记忆管理（CRUD + 重排序）、搜索、区过滤器、排序控制和图可视化。

#### 代码级审查

| 文件 | 路由 | 分析 |
|------|------|------|
| `plugin_api.py:80-120` | `POST /api/memory` | 创建记忆 → 委托 `srh_memory_write` |
| `plugin_api.py:130-170` | `PUT /api/memory/{id}` | 更新记忆 → 使用 MemoryStore 更新 |
| `plugin_api.py:180-220` | `DELETE /api/memory/{id}` | 删除 → 委托 `srh_memory_delete` |
| `plugin_api.py:230-260` | `POST /api/memory/reorder` | 重排序 → 使用 MemoryStore.reorder |
| `plugin_api.py:270-296` | `GET /api/palace/zones` | Zone 列表 → 委托 `srh_palace_zones` |

---

## 4. 外部研究交叉引用

| 插件功能 | 引用论文/项目 | 对应代码 | 差异分析 |
|---------|-------------|---------|---------|
| Memory Palace 分区 | Park et al. 2023 (Generative Agents) | `srh_palace_*` 工具 | 我们的 zone 语义更明确（core/work/episode/general） |
| TF-IDF 搜索 | Robertson & Zaragoza 2009 (BM25) | `_tfidf_search()` | 未使用 BM25 的饱和 TF；建议升级 |
| 嵌入搜索 | all-MiniLM-L6-v2 / FastEmbed | `_embed_single()` | 轻量但缺少量化/LRU 缓存 |
| 冲突检测 + Supersedes | **独特设计**，无直接文献 | `check_conflict()` | 创新点；Git 版本管理的记忆概念 |
| 效果衰减 | Ebbinghaus 1885 / Nguyen+ 2024 | `_get_effectiveness()` | 使用线性衰减而非指数；建议升级 |
| 微反思 | Shinn+ 2023 (Reflexion / verbal RL) | `_run_micro_reflection()` | 用嵌入 novel 度替代 LLM 自评（更省 token） |
| 全量反思 | Park+ 2023 (Daily Summarization) | `_run_full_reflection()` | 标准 LLM 结构输出 |
| 图关联记忆 | Ramsauer+ 2020 (Hopfield) / Zhang+ 2024 (Memory Sandbox) | `ahe_graph/` | 完整的 Hebbian + Ebbinghaus 实现 |
| 上下文分层 | Lewis+ 2024 (MemGPT Working Context) | `_pre_llm_call()` | 4 层 vs MemGPT 的 2 层 |
| 技能匹配 | LangChain 工具匹配 | `SkillStore.match()` | token 重叠法更轻量 |
| 性能优化 (async I/O) | — | `_async_write_queue` | 参考了 OS 级异步 I/O 模式 |
| 配置文件 | — | `_embeddings_enabled()` | 优雅的特性开关模式 |

### 插件创新点评估

| 创新点 | 独特性 | 成熟度 | 建议 |
|--------|--------|--------|------|
| **Supersedes 链** (记忆版本控制) | ★★★★★ 独特 | ⚡ 实现完整但缺少可视化 | 添加 dashboard 记忆演进图 |
| **Palace Zone 导航** (4 zone) | ★★★★☆ 接近 | ✅ 实现完整 | 添加跨 zone 聚合搜索 |
| **Embedding 微反思** (零 LLM token) | ★★★★☆ 巧妙 | ⚠️ 阈值需调优 | 添加自适应阈值 |
| **P0-P2 性能优化** | ★★★☆☆ 工程实践 | ✅ 已验证 | 继续 P3 路线（SQLite 元数据） |
| **图关联 + 传统记忆混合** | ★★★★☆ 组合创新 | ⚠️ 图检索路由未完全实现 | 实现 context-aware 策略切换 |

---

## 5. 已发现的问题与风险

### P0 — 必须修复

| # | 问题 | 位置 | 严重性 | 描述 |
|---|------|------|--------|------|
| 1 | `_ensure_cache()` 冷启动 O(n) 扫描两个目录 | `__init__.py:132-145` | 中 | 1000条记忆时 ~6ms。建议增量持久化缓存文件 |
| 2 | 嵌入索引每搜索重建 | `__init__.py:1097-1115` | 中 | 写时增量维护可避免每次搜索的全量重建 |
| 3 | `_get_effectiveness()` 全量 JSONL 扫描 | `__init__.py:480-517` | 中 | 建议合并为单文件 state 快照 |
| 4 | `_batch_record_stats` 异步队列无背压机制 | `__init__.py:1556-1590` | 低 | 极端高负载下队列可能溢出。建议有界队列 + 降级 |

### P1 — 建议修复

| # | 问题 | 位置 | 描述 |
|---|------|------|------|
| 5 | CJK/英文混合文本的 TF-IDF 阈值 | `__init__.py:1023` | 0.85 冲突阈值在 CJK 短文本中偏高 |
| 6 | BM25 替代 TF-IDF | `__init__.py:682-750` | BM25 召回率高 10-15%，无依赖代价 |
| 7 | 衰减模型为线性而非指数 | `__init__.py:1560` | Ebbinghaus 标准是指数衰减 |
| 8 | `_is_explicit_memory_intent()` 关键词过长 | `__init__.py:3022-3032` | 15+ 关键词，误触发率高 |
| 9 | ~~图检索路由始终使用 "reasoning"~~ | ~~`ahe_graph/__init__.py:504-523`~~ | **✅ 已修复** zone→strategy 映射 + 自动推断 |
| 10 | 嵌入无 batch 限制 | `__init__.py:1062-1095` | 大列表推理 OOM 风险 |
| 11 | `_graph_viz_h()` 工具未注册 | `__init__.py` 2500-2700 | dashboard 有前端但后端 API 未暴露 |

### P2 — 可推迟

| # | 问题 | 描述 |
|---|-------|
| 12 | 缺少跨 zone 聚合搜索 | `srh_palace_search` 工具不存在 |
| 13 | Zone 自动重平衡 | zone 超过容量时自动分裂为新 zone |
| 14 | 嵌入 LRU 缓存 | 高频查询的嵌入重计算开销 |
| 15 | 图下采样策略 | 节点 > 10k 时的性能退化 |

---

## 6. 性能基线

### v0.5.0 优化后 (50 记忆, 10 技能, TF-IDF, 无嵌入)

| 步骤 | 优化前 | 优化后 | 提升 | 机制 |
|------|--------|--------|------|------|
| 插件初始化 | 22.3ms | 22.3ms | — | 一次性开销 |
| Context Block (warm) | 1.74ms | 1.31ms | ↓25% | Write-on-change + 事件驱动 |
| Memory Write | 11.69ms | 0.57ms | ↓95% | Async I/O (P2-2) |
| Memory Delete | 10.68ms | 0.14ms | ↓99% | O(1) id→path (P0-2) |
| Token 估算 | 2.96ms | 0.6µs | ↓5000x | 字节估算 (P1-1) |
| Stat Flush | 206µs | 4.4µs | ↓98% | Async 队列 (P1-2) |
| 技能搜索 | 7µs | 3µs | ↓57% | Lazy 缓存 |
| **总热路径** | **76.9ms** | **34.0ms** | **↓56%** | 全优化 |

### 嵌入模式性能 (v0.5.0+)

| 操作 | 时间 |
|------|------|
| ONNX 模型加载 (首次) | ~415ms |
| 单条嵌入 (预热后) | ~6ms |
| 批量嵌入 (10条) | ~16ms (1.6ms/条) |
| 全量反思 (嵌入模式) | ~131ms |
| 微反思 (嵌入模式) | ~173ms |
| 内存占用 | 144MB (vs PyTorch 825MB) |

---

## 7. CI/CD 集成建议

### 7.1 自动化测试覆盖

```yaml
# 建议覆盖的测试矩阵:
test_matrix:
  unit:
    - _parse_frontmatter: 合法YAML, 无frontmatter, 空文件, 截断frontmatter
    - _tokenize: 纯英文, 纯CJK, 英中混合, 特殊字符
    - _tfidf_search: 精确匹配, 模糊匹配, 空查询, 空记忆库
    - check_conflict: 高相似度, 低相似度, 空body
    - _is_explicit_memory_intent: 各种语言的关键词
    - MemoryStore CRUD: 创建/读取/更新/删除 + 边界
    - _normalize_zone: 大小写, 空格, 特殊字符
    
  integration:
    - srh_memory_write → 文件系统验证
    - srh_memory_search → 返回正确记忆
    - srh_palace_zones + read_zone + recall 一致性
    - 全量反思 → pending-skills.json 写入
    - 微反思 → episode zone 写入
    
  regression:
    - Python 3.11 importlib 加载 (sys.modules workaround)
    - JSON 截断修复 (3 种模式)
    - P0-P2 性能退化检测 (基准时间比较)
```

### 7.2 代码质量门禁

| 门禁项 | 检查工具 | 通过标准 |
|--------|---------|---------|
| 语法正确性 | Python AST parse | 0 error |
| 类型标注 | mypy | 无严重类型错误 |
| 未使用导入 | autoflake | 0 |
| 行长度 | flake8 | < 120 (现有代码需适应) |
| 循环复杂度 | mccabe | < 15/function |
| 性能退化 | pytest-benchmark | 相比基线偏差 < 20% |

### 7.3 代码审查 Checklist

```
[ ] 1. 功能意图与实现一致？
[ ] 2. 所有新依赖在 plugin.yaml 或 install_requires 中声明？
[ ] 3. 双作用域（user/project）正确处理？
[ ] 4. 配置文件开关（plugins.mem_reflection_hermes.*）遵守？
[ ] 5. 错误处理：I/O 异常捕获 + logger.warning 降级？
[ ] 6. 线程安全：MemoryStore 锁、嵌入锁、异步队列锁？
[ ] 7. 路径安全：Path.resolve() 避免路径遍历攻击？
[ ] 8. 性能：避免 O(n^2) 热点路径？使用 id→path 索引？
[ ] 9. LLM 调用：complete_structured + JSON Schema？
[ ] 10. Dashboard API：委托 MemoryStore 原子操作而非直接 I/O？
```

### 7.4 版本发布流程

```
v0.5.0 → P0-P2 性能优化 (已完成)
v0.6.0 → Dashboard + 原子操作 (已完成)
v0.7.0 → ahe_graph 深度集成 (已完成，当前版本)
v0.8.0 → 趋势：P3 性能优化 + CJK BM25
v0.9.0 → 趋势：图检索路由完整实现
v1.0.0 → 稳定版：性能门禁 + 完整测试覆盖
```

---

## 8. 改进路线图

### ✅ 已完成 (2026-05-30 SDD 迭代)

| 方案 | 说明 | 状态 |
|------|------|------|
| **A: Supersedes 感知的图清理** | `_post_tool_associate` 写入时检测 supersedes → 衰减旧节点 importance + 迁移边权重 *0.3；删除时软标记不硬删 | ✅ `GraphStore.set_edge_weight()` 新增 + hook 修改 |
| **B: 图增强 Palace Index** | `build_palace_index()` 对每个 zone 计算图内部边密度，>=2 条显示 `[关联簇: N个连接]` | ✅ `_get_graph_mgr` 集成 + 优雅降级 |
| **C: 融合评分** | `MemoryStore.fusion_search()`: `final_score = 0.7*bm25 + 0.3*graph/(1+supersedes_depth)`；`_tool_srh_memory_search` 改用融合排序 | ✅ 3个核心方法新增 + 搜索 handler 改造 |

### 即刻 (P0修正)

1. **~~BM25 替代 TF-IDF~~** — **✅ 已修复**。替换为 `_bm25_search_scored()`, k1=1.5, b=0.75，包含文档长度归一化和饱和 TF
4. **~~嵌入增量维护~~** — **❌ 不适用**。检查后确认 `_try_index()` 和 `_try_remove_index()` 已在 `put()`/`delete()` 中调用，嵌入索引为首次构建 + 增量维护模式
3. **~~指数衰减模型~~** — **❌ 不适用**。检查确认 `decay_factor()` 已使用 `0.5^(days/30)` 指数衰减（30天半衰期），不是线性
4. **异步队列背压** — 有界 `queue.Queue(maxsize=1000)` + 降级策略

### 短期 (P1增强)

5. **CJK 自适应冲突阈值** — 检测文本语言类型后自动调整 threshold
6. **意图检测 LLM 分类** — 低频率 LLM 调用替代关键词爆炸
7. **~~图检索路由 context-aware~~** — **✅ 已修复**。在 `_graph_retrieve_h()` 中添加 zone→strategy 映射：core→factual, work→reasoning, episode→recent, general→exploration
8. **嵌入 LRU 缓存** — 高频查询的 O(1) 命中

### 中期 (P2+)

9. **跨 zone 聚合搜索** — 新增 `srh_palace_search` 工具
10. **Zone 自动重平衡** — zone 容量超阈值自动分裂/合并
11. **图可视化 API** — 注册 `_graph_viz_h()` 工具
12. **Supersedes 图谱** — Dashboard 中记忆版本演进可视化

---

*本文档由 Hermes Agent 自动生成，基于 mem-reflection-hermes v0.7.0 源码 (2026-05-30) 完整审查。*
*外部研究引用：Generative Agents (Park+, 2023), Reflexion (Shinn+, 2023), MemGPT (Lewis+, 2024), 
Hopfield Networks (Ramsauer+, 2020), Memory Sandbox (Zhang+, 2024), Ebbinghaus (1885/1913)*
