# v1.0-beta2 开发计划 — 架构重构 + 成熟库替代 + 学术对齐

> **版本**: v1.0-beta2
> **制定日期**: 2026-06-03
> **目标**: 13 模块 → 6 模块，~8,000 LOC → ~3,200 LOC，保留全部 17 工具 + 14 dashboard 端点

---

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| SQLite 存储 | 双库（memories.db + graph.db） | 隔离性更好，graph 可独立备份/重建 |
| BM25 实现 | 保留手写 CJK tokenizer | rank_bm25 不支持 CJK bigram 分词 |
| 新依赖 | python-frontmatter, tiktoken, cachetools | 均为轻量库，替换手写实现 |
| 反射模式 | raw_chunk 为默认 | 学术验证：hybrid 检索下 81.1%，零 LLM 调用 |
| 异步写队列 | 删除，改同步 + RLock | 峰值 <1 write/sec，异步是过度工程 |

---

## Phase 1: 基础层 — store.py（Day 1-2）

### 步骤

- [ ] 1.1 安装新依赖 `pip install python-frontmatter tiktoken cachetools`
- [ ] 1.2 创建 `store.py` 骨架，定义 SQLite schema（memories, tags, supersedes, stats）
- [ ] 1.3 迁移 `core.py` 中的数据模型（MemoryFrontmatter, LoadedMemory, SkillFrontmatter, LoadedSkill）
- [ ] 1.4 迁移 `core.py` 中的配置/路径函数（hermes_home, load_config, plugin_config 等）
- [ ] 1.5 用 `python-frontmatter` 替换手写 frontmatter 解析
- [ ] 1.6 用 `tiktoken` 替换 bytes-based token 估算
- [ ] 1.7 实现 `MemoryStore` CRUD（put/get/delete/update/list）→ SQLite
- [ ] 1.8 实现 `_write_md()` 原子写（temp → os.replace）
- [ ] 1.9 实现 `_sync_from_disk()` 启动扫描
- [ ] 1.10 实现 stats 持久化到 SQLite stats 表
- [ ] 1.11 实现 lineage helpers（latest_for, lineage_chain, is_superseded）
- [ ] 1.12 实现 SkillStore 迁移到 SQLite
- [ ] 1.13 编写 `test_store_sqlite.py`
- [ ] 1.14 验证 `test_core_data.py` 全部通过

### 关键替换

| 当前手写 | 替换为 |
|----------|--------|
| core.py frontmatter 解析 (~100行) | `python-frontmatter` |
| bytes/3-4 token 估算 | `tiktoken` cl100k_base |
| memory-stats.jsonl | SQLite stats 表 |
| async write queue (~164行) | 同步 write + RLock |
| _id_to_path/_id_to_mem 缓存 | SQLite 查询 |

---

## Phase 2: 检索层 — search.py（Day 3-4）

### 步骤

- [ ] 2.1 创建 `search.py` 骨架，定义 SearchIndex 类
- [ ] 2.2 迁移 `_tokenise()` CJK bigram 分词器（保留手写）
- [ ] 2.3 迁移 `_bm25_search` / `_bm25_search_scored`（修复 IDF log 公式）
- [ ] 2.4 抽取 `normalize_bm25(raw) → float` 统一归一化
- [ ] 2.5 迁移 embedding 引擎（ONNX + sentence-transformers fallback）
- [ ] 2.6 用 `numpy.ndarray` 替换 Python dict embedding 存储
- [ ] 2.7 用 `functools.lru_cache` 替换手写 LRU cache
- [ ] 2.8 用 `cachetools.TTLCache` 替换 query/cache.py
- [ ] 2.9 实现三层检索管道（Recall → Fusion → Rerank）
- [ ] 2.10 实现 dual-path 冲突检测（embedding cosine + BM25 sigmoid）
- [ ] 2.11 迁移 intent classification
- [ ] 2.12 编写 `test_search_sqlite.py`
- [ ] 2.13 验证 `test_bm25.py` + `test_fusion_rerank.py` 通过

### 检索管道

```
Layer 1: Recall ── Embedding top-2k + BM25 top-2k
Layer 2: Fusion ── pool union + min-max 归一化
Layer 3: Rerank ── α·cosine + β·bm25 + γ·recency + δ·eff + Hebbian boost
```

---

## Phase 3: 图记忆层 — graph.py（Day 5-6）

### 步骤

- [ ] 3.1 创建 `graph.py` 骨架，定义 GraphIndex 类 + 独立 graph.db schema
- [ ] 3.2 迁移 Hebbian 边管理（upsert_edge, get_neighbors, decay）
- [ ] 3.3 合并 AssociationEngine 逻辑到 GraphIndex.associate()
- [ ] 3.4 迁移 PageRank（修复 O(n²) → O(n·d) 反向邻接表）
- [ ] 3.5 迁移 CLUQI 跨层查询
- [ ] 3.6 迁移 cross-zone 分析
- [ ] 3.7 实现 Spreading Activation（fixed-point iteration）
- [ ] 3.8 实现 thread-local 连接（替代 check_same_thread=False）
- [ ] 3.9 SUPERSEDES 边从图中移除 → 纯 lineage 层
- [ ] 3.10 编写 `test_graph_index.py`
- [ ] 3.11 验证 `test_graph_operations.py` 通过

### 删除的抽象

| 当前 | 操作 |
|------|------|
| GraphStoreProtocol | 删除（只有 1 个实现） |
| AssociationEngine | 合并到 GraphIndex.associate() |
| RetrievalRouter | 删除（dict lookup 无价值） |
| GraphMemoryManager | 合并到 GraphIndex |

---

## Phase 4: 反射层 — reflect.py（Day 7-8）

### 步骤

- [ ] 4.1 创建 `reflect.py` 骨架，定义 ReflectionEngine(store, search, graph)
- [ ] 4.2 实现 raw_chunk 模式（零 LLM 调用，直接存储对话片段）
- [ ] 4.3 迁移 full reflection 管道
- [ ] 4.4 修复 JSON 解析（json.JSONDecoder.raw_decode 替换贪婪正则）
- [ ] 4.5 修复 _repair_truncated_json
- [ ] 4.6 迁移 micro reflection 管道
- [ ] 4.7 迁移 profile compilation
- [ ] 4.8 迁移 skill candidate 管理
- [ ] 4.9 构造函数依赖注入 — 无 late_binding
- [ ] 4.10 编写 raw_chunk 模式测试
- [ ] 4.11 验证 `test_reflection.py` 通过

---

## Phase 5: 上下文 + 工具 + 入口（Day 9-10）

### 步骤

- [ ] 5.1 创建 `context.py`，迁移 Palace 模式上下文注入（~250 行）
- [ ] 5.2 删除 Profile/Legacy 模式分支
- [ ] 5.3 重写 `__init__.py` 为纯注册入口（~150 行）
- [ ] 5.4 命名空间检测 + sys.modules 别名
- [ ] 5.5 迁移 12 个 SRH 工具 handler 到新模块 API
- [ ] 5.6 迁移 5 个 graph/health 工具
- [ ] 5.7 重写 `dashboard/plugin_api.py`，直接导入新模块（~350 行）
- [ ] 5.8 删除所有 importlib.import_module try/except
- [ ] 5.9 全量测试运行（117+ 测试）
- [ ] 5.10 Dashboard 端点 smoke test

---

## Phase 6: 迁移 + 清理（Day 11-12）

### 步骤

- [ ] 6.1 编写 `scripts/migrate_to_beta2.py` 迁移脚本
- [ ] 6.2 迁移 .md 文件 → memories.db
- [ ] 6.3 迁移 memory-stats.jsonl → stats 表
- [ ] 6.4 迁移旧 graph.db → 新 schema
- [ ] 6.5 迁移脚本空目录 + 有数据目录验证
- [ ] 6.6 删除旧模块（core.py, late_binding.py, query/, reflection/, graph/, search/, hooks/）
- [ ] 6.7 更新 CLAUDE.md 模块布局
- [ ] 6.8 更新 conftest.py 测试 fixture
- [ ] 6.9 全量测试最终验证
- [ ] 6.10 更新 plugin.yaml 版本号

---

## 验收标准

| 指标 | 目标 |
|------|------|
| Python 模块数 | 6（+ dashboard） |
| Python LOC | ≤3,500 |
| 测试通过率 | 100%（117+ 测试） |
| 工具 API 兼容 | 17/17 签名不变 |
| Dashboard 端点 | 14/14 不变 |
| Memory 文件格式 | 完全兼容 |
| HeLa-Mem dual-path | 融合检索包含 Hebbian boost |
| Reflection 默认 | raw_chunk（零 LLM 调用） |
| 无 late_binding | 完全消除 |

---

## 学术参考文献

- [HeLa-Mem] arXiv:2604.16839 — Hebbian Learning and Associative Memory for LLM Agents
- [Retrieval Bottleneck] arXiv:2603.02473 — Diagnosing Retrieval vs. Utilization Bottlenecks
- [MemForest] arXiv:2605.23986 — Hierarchical Temporal Indexing
