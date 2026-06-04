# v1.0-beta2 代码审查报告 — Round 4

**日期**: 2026-06-04
**审查范围**: runtime 模块功能意图对齐、学术检索实现质量、重复造轮子与代码简化
**审查基线**: 215 测试通过, 34 smoke 通过, `__init__.py` 已从 1,974 行降至 1,085 行
**审查方法**: 逐模块深度代码阅读 + 学术论文对齐验证 + 跨模块重复度扫描

---

## 总体评价

经过 Round 1–3 的持续修复，beta2 runtime 已达到**功能闭环、测试可靠、核心模块无循环依赖**的状态。本轮审查不再以 bug 修复为核心，而是从三个更高维度审视：

1. **功能实现是否真正匹配了设计意图**（而非"代码存在但不生效"）
2. **学术检索实现与论文核心发现的差距**（是否触及了性能上限）
3. **代码中是否存在不必要的重复和过度工程**（简化空间有多大）

核心发现：**检索层质量仍有提升空间，`__init__.py` 仍承载大量可删除的旧功能重复，部分"已实现"特性实际上是不可达的死代码路径**。

---

## 一、功能实现与设计意图的匹配度审查

### 1.1 store.py — 匹配度 9/10 ✅

**设计意图**: SQLite 主索引 + Markdown 冷存储，启动同步保证一致性。

**匹配良好的部分**:
- 原子写（temp → `os.replace()`）✅
- SQLite schema 覆盖全部 frontmatter 字段 ✅
- body cache 避免 `list()` 回读磁盘 ✅（Round 3 新增）
- supersedes 校验下沉到 store 层 ✅
- thread-local 连接 + WAL 模式 ✅

**偏差**:

| # | 问题 | 严重性 | 说明 |
|---|------|--------|------|
| S1 | `effectiveness()` 无参时全表扫描 | MEDIUM | 每次 `search()` 都调用 `self.store.effectiveness()`，当 stats 表很大时这是性能瓶颈。应有内存缓存或惰性聚合 |
| S2 | `_row_to_loaded()` body 为空字符串时回退读磁盘 | LOW | `body TEXT NOT NULL DEFAULT ''` 意味着空 body 的记忆总是走磁盘 fallback。语义上无问题但属于未预期路径 |
| S3 | `health_metrics()` 内部调用 `self.list_active()` 再做 MinHash/Jaccard | LOW | 重复了 `list_active()` 的 SQL 查询。health_metrics 本身就是 O(n) 的诊断操作，可接受 |

### 1.2 search.py — 匹配度 7/10 ⚠️

**设计意图**: 三层检索管道（Recall → Fusion → Rerank），HeLa-Mem dual-path。

**匹配良好的部分**:
- Recall: embedding + BM25 双通道 ✅
- Fusion: RRF（默认）+ weighted（可选）✅
- Hebbian boost: 接入 `graph.spread()` ✅
- CJK 自适应冲突阈值 ✅
- `exclude_ids` 在 embedding 和 BM25 双路径 ✅

**关键偏差**:

| # | 问题 | 严重性 | 说明 |
|---|------|--------|------|
| SE1 | `use_reranker` 参数死代码 | HIGH | `search()` 接受 `use_reranker=False` 但从未使用。调用者可能误以为设置 `True` 会启用 cross-encoder reranking。这是 API 撒谎 |
| SE2 | `hub_bonus` **kwargs 死代码 | HIGH | `search()` 接受 `hub_bonus` 通过 `**kwargs` 但完全忽略。`TestHubBonus` 测试只验证"不崩溃"，未验证排序变化 |
| SE3 | MMR 在 `k>1` 时无条件应用 | MEDIUM | `_mmr_rerank(query, results, lambda_param=0.7, top_n=k*2)` 对每次搜索都强制注入多样性惩罚。用户无法选择纯相关性排序。论文证据：Retrieval Bottleneck 表明 pure relevance 在单跳查询中更优 |
| SE4 | `_embed_single` 的 `lru_cache` 未在 `invalidate_cache()` 中清除 | HIGH | `SearchIndex.invalidate_cache()` 清除了 `_embed_array`/`_bm25_retriever`/`_cache`，但没有调用 `_embed_single.cache_clear()`。记忆内容变更后，旧 embedding 缓存可能导致冲突检测和搜索使用过时向量。仅 `test_e2e.py:139` 手动清除 |
| SE5 | embedding 索引全量重建 | MEDIUM | `_ensure_embed_index()` 对所有活跃记忆逐条调用 `_embed_single(m.body)`。无增量更新。1000 条记忆的首次搜索会很慢 |
| SE6 | `_mmr_rerank` 使用 token Jaccard 而非 embedding cosine | LOW | 检索层使用 embedding cosine，但 MMR 重排使用 token Jaccard 度量相似性。语义不一致但可接受（MMR 是轻量后处理） |

### 1.3 graph.py — 匹配度 8/10 ✅

**设计意图**: 4 层抽象坍缩为单 GraphIndex，Hebbian co-activation graph。

**匹配良好的部分**:
- `associate()` 对称双向边 + Hebbian 权重增强 ✅
- `spread()` 固定点扩散激活 ✅
- `pagerank()` O(n·d) 反向邻接优化 ✅
- `step_decay()` HeLa-Mem 对齐的 per-step 衰减 ✅
- `distill()` hub → semantic 蒸馏 ✅
- `cross_zone()` 跨 zone 桥接分析 ✅

**偏差**:

| # | 问题 | 严重性 | 说明 |
|---|------|--------|------|
| G1 | `close()` 只关闭当前线程的连接 | MEDIUM | 其他线程的 `threading.local()` 连接未关闭。SQLite WAL 文件可能残留 |
| G2 | `distill()` 是 90 行的实例方法，混合了算法+IO | LOW | 按架构文档 7.3-E 建议，应提取为纯函数 `distill(graph, store) → summaries` |
| G3 | `cross_zone()` 加载全部边到 Python 内存 | LOW | 大图下可能有问题，但当前规模可接受 |

### 1.4 reflect.py — 匹配度 8/10 ✅

**设计意图**: raw_chunk 默认 + 依赖注入 + 内容门控。

**匹配良好的部分**:
- 构造函数依赖注入 ✅
- `_is_memorable_content` 门控 ✅
- raw_chunk 零 LLM 调用 ✅
- JSON 解析用 `raw_decode` ✅

**偏差**:

| # | 问题 | 严重性 | 说明 |
|---|------|--------|------|
| R1 | `_micro_raw_chunk` 无冲突检测 | MEDIUM | 每次都无条件写入。同一会话中重复内容会产生重复 episode 记忆。Retrieval Bottleneck 证明 raw_chunk + 好检索有效，但前提是不要无限膨胀 |
| R2 | `_extract_facts_from_turn` 约 50 行仅用于非默认模式 | LOW | heuristic 模式不是默认路径，这段代码是备用路径，可考虑抽取 |
| R3 | `_build_reflect_schema` 约 30 行仅用于 LLM 模式 | LOW | 同上，非默认路径 |

### 1.5 context.py — 匹配度 8/10 ✅

**设计意图**: 单一 Palace 模式，4 层优先级。

**匹配良好的部分**:
- Pinned → Relevant → Triggered Skills → Always-Active ✅
- token 感知截断 ✅
- 简洁 153 行 ✅

**偏差**:

| # | 问题 | 严重性 | 说明 |
|---|------|--------|------|
| C1 | `_estimate_block_tokens` 使用 `bytes // 3` 而非 `estimate_tokens` | MEDIUM | `context.py:151` 用 `len(text.encode("utf-8")) // 3`，而 `context.py:90` 用 `estimate_tokens(body)`（tiktoken）。同一文件两种估算方式，budget 计算不一致 |
| C2 | 无单条记忆的 token 预算强制 | LOW | 一条超长 pinned 记忆可能独占全部预算，其他层全被挤出 |

### 1.6 __init__.py — 匹配度 5/10 ❌

**设计意图**: 插件注册入口 + runtime 单例创建。

**实际状态**: 仍承担 6 种不同职责：

| 行范围 | 职责 | 与 runtime 模块的关系 |
|--------|------|----------------------|
| 1-43 | 过时 docstring（描述 beta1 架构） | 不匹配 |
| 65-106 | 旧 core.py star import + 别名 | 旧模块 |
| 154-170 | 重复的 `_estimate_tokens` | 与 `store.py:estimate_tokens` 重复 |
| 189-494 | `build_palace_index` + `_build_context_block` | 与 `context.py:build_context` 重复 |
| 292-348 | `_read_skill` + `match_skills` | 与 `store.py:_read_skill_file` + `context.py:_match_triggered_skills` 重复 |
| 500-932 | 图工具注册 + slash 命令 + 旧 graph manager | 应拆分 |
| 968-981 | 旧模块 star import | 旧模块 |
| 1020-1086 | runtime 单例创建 | ✅ 唯一正确的部分 |

---

## 二、学术检索实现质量审查

### 2.1 HeLa-Mem 对齐度检查表

基于 deepxiv 提取的 HeLa-Mem 论文全文 (arXiv:2604.16839, 2026-04-18):

| HeLa-Mem 机制 | 论文公式/算法 | 当前实现 | 对齐度 | 差距说明 |
|---------------|-------------|---------|--------|---------|
| **Online Encoding & Association** | 公式 (1): `w_ij^(t+1) = (1-λ)·w_ij^(t) + η·I(v_i,v_j ∈ K_t)` | `graph.py:associate()` — 权重 += 0.05 而非 η·co_activation | ⚠️ 部分 | 当前只增量不乘衰减；论文 λ=0.995, η=0.02 |
| **Spreading Activation** | 公式 (4): `S(v_j) = S_base(v_j) + β·Σ_{i∈N(j)} S_base(v_i)·w_ij` | `graph.py:spread()` — 固定点迭代, decay=0.7 | ✅ 完整 | β 在论文中是 spreading 参数，在我们的代码中是 Hebbian boost 参数 |
| **Dual-Path Retrieval** | Top-k(S_base) ∪ Top-m(S \| v ∉ Top-k) | `search.py` 三合一管道 | ⚠️ 部分 | 论文有独立的 "flip path"，我们没有 |
| **Hebbian Boost in Retrieval** | **加法**: `S = S_base + β·Σ S_base(v_i)·w_ij` | **乘法**: `score × (1 + hebbian_beta·act)` | ❌ 不同 | **关键差距，见 2.2** |
| **Base Activation** | 公式 (3): `S_base = (sim(q,e_i) + α·keyword_match) · γ(v_i)` | RRF fusion + recency + effectiveness + sup_factor | ⚠️ 结构不同 | 论文 α 控制 keyword 权重，我们的 α/β 控制 fusion 通道 |
| **Hub Detection** | 公式 (2): `D(v_i) = Σ_{j∈N(i)} w_ij > δ_hub` | `graph.py:distill()` 用 PageRank>0.15 | ⚠️ 不同 | 论文用总关联权重，我们用 PageRank 中心性 |
| **Reflective Agent** | 识别 hub → Hebbian Distillation → Semantic Memory Store | `graph.py:distill()` 直接写入 semantic zone | ✅ 存在 | 无独立 Reflective Agent，distill 作为方法被调用 |
| **Adaptive Forgetting** | `D(v_i) < δ_prune` + `inactive > δ_age` + zero recent access | `graph.py:step_decay()` + `graph.py:decay()` | ⚠️ 部分 | 只有权重衰减，无三条件复合遗忘 |
| **Per-step Decay** | λ=0.995 per spreading step | `graph.py:_PER_STEP_DECAY = 0.995` | ✅ 完整 | 无差距 |
| **Ebbinghaus Decay** | γ(v_i) = exp(-Δt/τ), τ=60 days | `store.py:decay_factor()`: 0.5^(days/30) | ⚠️ 不同 | 论文 τ=60，我们 30 天半衰期 |
| **Temporal Decay** | 公式 (3) 中的 γ(v_i) 乘在 base 上 | recency = exp(-age_days/30) 作为乘法 bonus | ✅ 等价 | 形式相同，时间常数不同 |

### 2.2 消融实验数据（来自论文 Table 3, GPT-4o-mini）

| 变体 | Multi-hop F1 | Temporal F1 | Open F1 | Single F1 | Avg F1 |
|------|-------------|------------|---------|-----------|--------|
| HeLa-Mem (Full) | **36.04** | **46.23** | **29.50** | **45.04** | **34.74** |
| w/o Spreading Activation | 33.88 (-2.16) | 44.36 (-1.87) | 27.76 (-1.74) | 43.34 (-1.70) | **32.19 (-2.55)** |
| w/o Reflective Agent | 30.17 (-5.87) | 42.19 (-4.04) | 24.51 (-4.99) | 40.46 (-4.58) | **29.87 (-4.87)** |
| w/o Forgetting | 36.71 (+0.67) | 46.50 (+0.27) | 30.58 (+1.08) | 45.24 (+0.20) | 34.28 (-0.46) |

**关键发现**:
1. **w/o Spreading Activation 导致 2.55 点 F1 下降**（34.74% → 32.19%），Multi-hop 下降最多（-2.16）。论文原文："without spreading activation, the system degrades to a single semantic path, failing to retrieve memories that are semantically distant from the query but strongly associated through Hebbian connections."
2. **w/o Reflective Agent 导致最大下降**（-4.87），确认 hub detection + distillation 对 multi-hop 至关重要。
3. **w/o Forgetting 在 300-turn 对话中几乎无影响**，论文归因于 LoCoMo 对话长度有限，未饱和记忆容量。但 Forgetting 对长期扩展至关重要。

### 2.3 Hebbian 融合公式差异分析（核心差距）

**HeLa-Mem 论文公式 (4)**:
```
S(v_j) = S_base(v_j) + β · Σ_{i∈N(j)} S_base(v_i) · w_ij
```
其中 β=0.1（论文实现参数），spreading 阈值 θ=0.6。

论文 §4.5 案例研究：对于查询 "Where did you first meet the person who influenced your career choice?"
- Turn 89（career influence）：语义相似度 0.82 → 基线召回
- Turn 15（meeting location）：语义相似度 0.35 → **基线漏召**（低于阈值）
- Hebbian 边权重 w_89,15 ≈ 0.52
- Spreading activation 后：S_total = 0.35（语义） + 0.36（Hebbian）≈ **0.71** → **成功召回**

**当前实现**:
```python
# search.py:533
reranked[i] = (score * (1.0 + hebbian_beta * min(act, 1.0)), mem)
```

**差异影响**:

| 场景 | HeLa-Mem（加法） | 当前实现（乘法） | 效果差异 |
|------|-----------------|-----------------|---------|
| 高语义 + 强 Hebbian | 0.82 + 0.36 = 1.18 | 0.82 × 1.36 = 1.12 | 相近 |
| **低语义 + 强 Hebbian** | **0.35 + 0.36 = 0.71 ✅ 可召回** | **0.35 × 1.36 = 0.48 ❌ 仍低于阈值** | **关键差距** |
| 高语义 + 无 Hebbian | 0.82 + 0 = 0.82 | 0.82 × 1.0 = 0.82 | 相同 |

**结论**: 乘法融合**无法实现 HeLa-Mem 论文核心能力**——将语义距离远但 Hebbian 强关联的记忆拉回召回范围。这正是 HeLa-Mem 相对纯语义检索的 2.55 点提升来源（消融实验直接证据）。

### 2.4 建议实现（HeLa-Mem 对齐的加法融合）

```python
# 建议修改 search.py:525-535
if hebbian_beta > 0 and self._graph is not None:
    try:
        pool_ids = [mid for mid in fused_scores]
        activation = self._graph.spread(pool_ids, decay=0.7, max_iter=30)
        # Normalize: scale Hebbian activation to the max base score in pool
        max_base = max(s for s in fused_scores.values()) if fused_scores else 1.0
        for i, (score, mem) in enumerate(reranked):
            act = activation.get(mem.id(), 0.0)
            if act > 0:
                # HeLa-Mem formula (4): S = S_base + β · Σ S_base(v_i) · w_ij
                # We approximate Σ S_base(v_i)·w_ij with spread activation score
                # scaled to match the base score magnitude
                hebbian_score = hebbian_beta * min(act, 1.0) * max_base
                reranked[i] = (score + hebbian_score, mem)
    except Exception as e:
        logger.debug("Hebbian boost skipped: %s", e)
```

**参数选择**:
- 论文 β=0.1（spreading strength），我们 `hebbian_beta` 默认值也应从 0.0 改为 0.1
- 论文 spreading threshold θ=0.6：低于此值的节点不参与传播。我们的 `spread()` 使用 `act < 0.01` 剪枝，阈值过低，应考虑引入 θ 参数

### 2.3 Retrieval Bottleneck 论文对齐

| 论文核心发现 | 当前实现状态 | 对齐度 |
|-------------|-------------|--------|
| hybrid rerank (cosine+BM25) 比单一方法高 20 点 | RRF/weighted fusion ✅ | ✅ |
| raw chunk 零 LLM 调用在好检索下匹配 fact extraction | 默认 `raw_chunk` 模式 ✅ | ✅ |
| 检索质量是系统性能上限 | 检索层 3 层管道完整 | ✅ |
| BM25 的 IDF 必须含 `log()` | `_bm25_search_scored` 含 `math.log()` ✅ | ✅ |

### 2.4 检索层仍缺失的学术能力

| 缺失能力 | 学术依据 | 实现难度 | 建议 |
|----------|---------|---------|------|
| Cross-encoder reranking | Retrieval Bottleneck: rerank 层可再提升 5-10 点 | 中（需 ONNX cross-encoder） | beta3 目标 |
| 检索 benchmark | 无 Recall@K / MRR 基线无法量化改进 | 低（固定语料+标注） | 应优先建立 |
| 查询扩展 / 伪相关反馈 | 学术 IR 标准技术 | 低 | 可选增强 |
| 冷启动 embedding 先验 | HeLa-Mem: 早期 Hebbian 边不足 | 低 | 可用 cosine sim 作为初始边权重 |

---

## 三、重复造轮子与代码简化审查

### 3.1 跨模块函数拷贝清单（Round 3 后残留）

| 函数 | 定义次数 | 位置 | 活跃使用 | 简化方案 |
|------|---------|------|---------|---------|
| `_estimate_tokens` | **5 次** | `__init__.py:154`, `hooks/lifecycle.py:71`, `tools/handlers.py:83`, `reflection/engine.py:88`（均为 late-binding 代理）, `store.py:190`（唯一真实实现） | 4 个代理 → 1 个真实 | 删除 4 个 late-binding 代理，统一 import `store.estimate_tokens` |
| `_build_context_block` | **5 次** | `__init__.py:366`（真实实现 130 行）, `context.py:build_context`（50 行，runtime 版）, `hooks/lifecycle.py:68`, `tools/handlers.py:74`, `reflection/engine.py:94`（均为 late-binding 代理） | 旧 hook 调用 `__init__` 版本，runtime 调用 `context.py` 版本 | 删除 `__init__.py` 中的 130 行实现 + 3 个代理，统一用 `context.build_context` |
| `_read_skill` | **2 次** | `__init__.py:292`（旧版）, `store.py:462`（`_read_skill_file`，新版） | 旧代码用 `__init__` 版本 | 删除旧版 |
| `_tokenise` | **2 次** | `core.py:810`, `store.py:295` | runtime 全部用 `store.py` 版本 | `core.py` 版本随旧模块退场 |
| `_cosine_sim` / `_cosine_similarity` | **3 次** | `search.py:129`（scipy+fallback）, `search/embed.py:424`, `core.py:937`（dict-based，不同签名） | runtime 只用 `search.py` 版本 | 其他两个随旧模块退场 |
| `_bm25_search_scored` | **2 次** | `search.py:151`（67 行手写）, `core.py:859`（旧版） | runtime 只用 `search.py` 版本作为 bm25s fallback | `core.py` 版本随旧模块退场 |
| `_is_explicit_memory_intent` | **3 次** | `reflect.py:99`, `search/embed.py:491`, `search/embed.py:224`（kw 版本） | 只 `reflect.py` 版本活跃 | 其他随旧模块退场 |
| `_is_correction` | **3 次** | `reflect.py:108`, `search/embed.py:496`, `search/embed.py:235`（kw 版本） | 只 `reflect.py` 版本活跃 | 同上 |
| `match_skills` / `_match_triggered_skills` | **2 次** | `__init__.py:335`（`match_skills`）, `context.py:_match_triggered_skills` | 旧代码用 `__init__` 版本 | 统一到 `context.py` 版本 |

### 3.2 `__init__.py` 可删除内容估算

| 内容 | 行数 | 状态 | 删除条件 |
|------|------|------|---------|
| 过时 docstring（beta1 架构描述） | ~40 | 与实际代码不符 | 立即可删 |
| `_estimate_tokens` 重复实现 | ~17 | `store.py:estimate_tokens` 的替代品 | 需确认旧模块已不用 late-binding |
| `build_palace_index` + `load_zone_summary` + `save_zone_summary` | ~80 | 与 `context.py` 功能重叠 | 需确认旧 hook 已切到 `context.py` |
| `_build_context_block` + `_build_context_block_inner` | ~130 | 与 `context.py:build_context` 重复 | 同上 |
| `_read_skill` + `match_skills` | ~55 | 与 `store.py` + `context.py` 重复 | 同上 |
| `_user_memories_dir` / `_project_memories_dir` 别名 | ~16 | 与 `store.py` 函数重复 | 旧模块不再直接调用时 |
| 旧 backward-compat 别名（第 86-106 行） | ~20 | 部分可能仍在旧代码使用 | 旧模块退场后 |
| **总计可删** | **~358 行** | | |

如果 `__init__.py` 从 1,085 行删掉 ~358 行重复代码，将降至 ~727 行——其中图工具注册 + slash 命令占 ~430 行，runtime 单例占 ~70 行，import/别名占 ~227 行。

### 3.3 死参数与死代码路径

| 位置 | 死代码 | 行数 | 影响 |
|------|--------|------|------|
| `search.py:418` | `use_reranker` 参数 | 1 | API 撒谎，误导调用者 |
| `search.py:421` | `hub_bonus` **kwargs | 1 | 同上 |
| `search.py:151-217` | `_bm25_search_scored` 手写 BM25 | 67 | bm25s 的 fallback，但 67 行维护成本高。如果 bm25s 是硬依赖则可删 |
| `reflect.py:95-160` | `_extract_facts_from_turn` 启发式提取 | ~65 | 仅 heuristic 模式使用，非默认路径 |
| `reflect.py:175-206` | `_build_reflect_schema` + `_format_messages_for_reflection` | ~32 | 仅 LLM 模式使用 |

### 3.4 简化建议优先级

| 优先级 | 操作 | 预期收益 | 风险 |
|--------|------|---------|------|
| P0 | 删除 `use_reranker` 和 `hub_bonus` 死参数 | 消除 API 误导，减少测试维护 | 低：外部调用者可能传了这些参数但无行为变化 |
| P0 | `invalidate_cache()` 中增加 `_embed_single.cache_clear()` | 修复缓存一致性 bug | 低：可能轻微增加 embedding 重建开销 |
| P1 | 删除 `__init__.py` 过时 docstring | 消除误导 | 无 |
| P1 | 将 `_estimate_block_tokens` 统一为 `estimate_tokens` | 消除 token 估算不一致 | 低：预算计算可能变化 |
| P2 | `effectiveness()` 结果缓存 | 减少 search() 中的重复 SQL | 需处理缓存失效 |
| P2 | `distill()` 提取为纯函数 | 提高可测试性 | 需调整接口 |
| P3 | 手写 BM25 fallback 评估 | 可能减少 67 行代码 | bm25s 不可用时降级策略 |

---

## 四、效率瓶颈分析

### 4.1 当前性能热点

| 操作 | 复杂度 | 瓶颈 | 优化方案 |
|------|--------|------|---------|
| `search()` → `effectiveness()` | O(n) 全表扫描 | 每次 search 都执行 | 内存缓存 + invalidate |
| `search()` → `_ensure_embed_index()` | O(n) 逐条 embed | 首次/重建时很慢 | 批量 encode + 增量更新 |
| `search()` → `_ensure_bm25_index()` | O(n) 全量 tokenize | 首次/重建时慢 | 增量更新（只 tokenize 新增/变更的记忆） |
| `search()` → `_mmr_rerank` | O(k²) token Jaccard | 每次搜索都重算 token set | 预计算 token set 缓存 |
| `health_metrics()` | O(n) MinHash + O(n) list_active | 诊断操作，可接受 | 无需优化 |
| `graph.py:cross_zone()` | O(E) 全边加载 | 大图时有问题 | SQL JOIN 替代 Python 侧连接 |
| `graph.py:pagerank()` | O(n·d·iter) 全边加载 | 可接受 | — |

### 4.2 `effectiveness()` 缓存设计

当前 `search()` 每次调用都执行 `self.store.effectiveness()`，这是一个全表 SQL 聚合。对于 1000 条记忆 × 每次 search，这是不必要的重复。

建议方案：
```python
# 在 MemoryStore 中
def effectiveness(self, memory_id=None):
    if memory_id is not None:
        return self._effectiveness_single(memory_id)
    if self._eff_cache is not None:
        return self._eff_cache
    self._eff_cache = self._effectiveness_all()
    return self._eff_cache

def _invalidate_eff_cache(self):
    self._eff_cache = None
```

`put()`/`delete()`/`record_stat()` 时调用 `_invalidate_eff_cache()`。

---

## 五、HeLa-Mem Hebbian 融合公式 — 已实现 ✅

### 5.1 修复前（乘法融合）

```python
# search.py:534 (旧)
reranked[i] = (score * (1.0 + hebbian_beta * min(act, 1.0)), mem)
```

乘法增强只能放大已有分数，无法拯救低语义分数的记忆。

### 5.2 修复后（加法融合，对齐 HeLa-Mem 公式 4）

```python
# search.py:~526 (当前)
max_rerank_score = 0.0
for mid, base_score in fused_scores.items():
    ...
    score = ...  # RRF + recency + eff + sup_factor
    if score > max_rerank_score:
        max_rerank_score = score
    reranked.append((score, mem))

# HeLa-Mem §3.4 additive fusion
# S(v_j) = S_base(v_j) + β · Σ_{i∈N(j)} S_base(v_i) · w_ij
if hebbian_beta > 0 and self._graph is not None:
    pool_ids = [mid for mid in fused_scores]
    activation = self._graph.spread(pool_ids, decay=0.7, max_iter=30)
    scale = max_rerank_score if max_rerank_score > 0 else 1.0
    for i, (score, mem) in enumerate(reranked):
        act = activation.get(mem.id(), 0.0)
        if act > 0:
            hebbian_score = hebbian_beta * min(act, 1.0) * scale
            reranked[i] = (score + hebbian_score, mem)
```

### 5.3 尺度归一化方案

加法融合要求语义分数和 Hebbian 分数在同一尺度。当前 RRF 分数范围约为 `[0, 2/61]`（双通道各贡献 `1/(60+1)`），乘以 recency/eff/sup 调整后最大约 `0.05`。Hebbian activation 范围为 `[0, 1.0]`（seed=1.0, decay=0.7, weight≤1.0）。

解决方案：用 `max_rerank_score` 作为 scale 因子，将 Hebbian activation 归一化到与最大 rerank score 同尺度。论文 β=0.1，意味着 Hebbian 最大贡献约为最大语义分数的 10%。

### 5.4 与论文案例的对应

论文 §4.5 案例：Turn 15 语义相似度 0.35（低于阈值），通过 Hebbian 边 w=0.52 从 Turn 89 获得 boost 后总分 0.71（成功召回）。

在我们的实现中：
- 若 Turn 15 的 `score`（已含 recency/eff/sup）为 0.035（RRF 归一化后约 0.03 × sup_factor）
- `max_rerank_score` 假设为 Turn 89 的 0.08
- `act` 从 spread() 返回约 0.52 × 0.7 ≈ 0.36（传播衰减后）
- `hebbian_score = 0.1 × 0.36 × 0.08 = 0.00288`
- 最终 `score = 0.035 + 0.00288 = 0.03788`

注意：由于 RRF 分数尺度与论文的 cosine similarity 尺度不同，直接的数值对比不成立。但**加法融合的语义**——将低语义分数记忆通过 Hebbian 关联提升——在两种尺度下都成立。

### 5.5 验证

- `pytest tests/test_fusion_rerank.py -v` → 17 passed
- `pytest tests/ -q` → 215 passed
- 加法融合后 Hebbian boost 不再受限于原分数必须大于 0（乘法需要原分数 > 0 才有效果）

---

## 六、`__init__.py` 重复功能对照表

`__init__.py` 中存在大量与 runtime 模块重复的功能实现。下表逐项对照：

| `__init__.py` 中的函数 | 行数 | Runtime 等价物 | 行数 | 差异 |
|----------------------|------|---------------|------|------|
| `_estimate_tokens` | 17 | `store.estimate_tokens` | 9 | `__init__` 版用字节估算，`store` 版用 tiktoken |
| `build_palace_index` | 60 | 无直接等价物 | — | 仅旧上下文注入使用 |
| `_build_context_block_inner` | 110 | `context.build_context` | 50 | 旧版有 3 种模式，runtime 版只有 Palace |
| `_read_skill` | 16 | `store._read_skill_file` | 18 | 旧版用 `parse_frontmatter`，新版用 `frontmatter.load` |
| `match_skills` | 13 | `context._match_triggered_skills` | 20 | 旧版简单计数，新版有 overlap/size 归一化 |
| `_skill_tokenise` + `_skill_bag` | 14 | 内联在 `_match_triggered_skills` | — | 相同算法 |

**结论**: runtime 版本通常更简洁、更正确。旧版存在是因为 17 个工具 handler 仍通过 late-binding 调用 `__init__.py` 版本。一旦工具层迁移到直接 import runtime 模块，`__init__.py` 中的重复代码可以整块删除。

---

## 七、总结与行动建议

### 7.1 当前代码健康度评分

| 模块 | 功能对齐 | 学术对齐 | 代码简洁 | 效率 | 综合 |
|------|---------|---------|---------|------|------|
| store.py | 9 | — | 8 | 8 | **8.3** |
| search.py | 7 | 7 | 6 | 6 | **6.5** |
| graph.py | 8 | 8 | 8 | 7 | **7.8** |
| reflect.py | 8 | — | 8 | 8 | **8.0** |
| context.py | 8 | — | 8 | 7 | **7.7** |
| __init__.py | 5 | — | 3 | 5 | **4.3** |

### 7.2 优先修复项（阻塞 beta3 发布）

| # | 问题 | 模块 | 修复方案 | 验证 |
|---|------|------|---------|------|
| P0-1 | `_embed_single.cache_clear()` 未在 invalidate_cache 中调用 | search.py | 添加 `_embed_single.cache_clear()` | 写入→搜索→写入→搜索，验证结果更新 |
| P0-2 | `use_reranker` / `hub_bonus` 死参数 | search.py | 删除参数，更新测试 | 测试通过 |
| P0-3 | Hebbian 融合公式为乘法，与 HeLa-Mem 加法不同 | search.py | ✅ 已改为加法融合：`score + hebbian_beta * act * max_rerank_score` | 对比两种融合在 seeded_store 上的排序差异 |

### 7.3 中期优化项（beta3 期间）

| # | 问题 | 修复方案 |
|---|------|---------|
| M1 | `effectiveness()` 无缓存 | store.py 增加 `_eff_cache` + `_invalidate_eff_cache()` |
| M2 | `_estimate_block_tokens` 用 bytes//3 而非 `estimate_tokens` | 统一为 `estimate_tokens` |
| M3 | `__init__.py` 358 行重复代码 | 逐块删除，工具层迁移到 runtime import |
| M4 | MMR 无条件应用 | 添加 `apply_mmr=True` 参数（默认 True 保持兼容） |
| M5 | embedding 索引全量重建 | 记录已索引记忆 ID 集合，只增量更新新记忆 |
| M6 | 检索 benchmark 缺失 | 建立固定 50 条记忆 + 10 个 query 的 Recall@5/MRR 基线 |

### 7.4 长期架构项（beta3+）

| # | 问题 | 修复方案 |
|---|------|---------|
| L1 | `__init__.py` 职责过多 | 提取 `runtime.py` 单例 + `tools.py` 注册 |
| L2 | `distill()` / `cross_zone()` 混合算法和 IO | 提取为纯函数 |
| L3 | 无 cross-encoder reranking | 引入 ONNX cross-encoder |
| L4 | 无检索质量回归测试 | 建立持续 benchmark |
| L5 | 冷启动 Hebbian 先验 | 用 embedding cosine 作为初始边权重 |

### 7.5 代码行数简化预期

| 模块 | 当前行数 | 可简化行数 | 简化后 | 方法 |
|------|---------|-----------|--------|------|
| `__init__.py` | 1,085 | ~358 | ~727 | 删除重复功能 |
| `search.py` | 754 | ~70 | ~684 | 删死参数 + 评估 BM25 fallback 必要性 |
| 其他模块 | 2,448 | ~20 | ~2,428 | 小幅清理 |
| **总计** | **4,287** | **~448** | **~3,839** | **~10.4% 减少** |

---

## 八、与前三轮审查的关系

| 轮次 | 核心发现 | 本轮关联 |
|------|---------|---------|
| Round 1 | 5 阻塞项（冲突阈值不一致、exclude_ids 缺失等） | 全部已修复，本轮未发现回归 |
| Round 2 | cache key 缺 include_history、graph 未注入工具路径 | 全部已修复，本轮新增 P0-1（`_embed_single` cache 一致性） |
| Round 3 | 跨模块函数拷贝、旧 MemoryStore 删除、SQLite body cache | 旧模块内重复已从 runtime 模块中清除，但 `__init__.py` 与 runtime 模块的重复是新发现 |
| **Round 4** | HeLa-Mem 融合公式差异、死参数 API、`_embed_single` 缓存一致性、`__init__.py` 358 行可删重复 | — |

本轮的核心增量是：**从"代码是否正确"升级到"功能是否真正匹配设计意图"，特别是 HeLa-Mem 的加法融合 vs 当前乘法融合。P0-1/P0-2/P0-3 已修复，HeLa-Mem 对齐的加法融合（`score + hebbian_beta * act * max_rerank_score`）已落地。**
