---
name: beta3-plan-0-9-2
description: v0.9.2-beta3开发计划 — 整合代码审查与学术调研，分三波推进修复与优化
metadata: 
  node_type: memory
  type: project
  originSessionId: e35ae249-246a-4424-b5f5-a19307cfbded
---

# v0.9.2-beta3 开发计划

**制定日期**: 2026-06-02
**基础**: 第四轮代码审查 + deepxiv学术调研

**核心参考文献**:
- [HeLa-Mem] *HeLa-Mem: Hebbian Learning and Associative Memory for LLM Agents*. arXiv:2604.16839, 2026-04-18. 关键发现: spreading activation使multi-hop推理提升2-3点；Reflective Agent蒸馏hub节点；LoCoMo/LongMemEval-S上超越Mem0/A-MEM/MemoryOS.
- [Retrieval Bottleneck] *Diagnosing Retrieval vs. Utilization Bottlenecks in LLM Agent Memory*. Yuan et al. arXiv:2603.02473, 2026-03-02. 关键发现: 3×3对照实验（写策略×检索方法），检索方法主导20点准确度差异，写策略仅3-8点；Basic RAG（零LLM调用）在hybrid检索下达81.1%，匹配或超越Mem0式fact extraction.
- [MemForest] *MemForest: An Efficient Agent Memory System with Hierarchical Temporal Indexing*. arXiv:2605.23986, 2026-05-16. 关键发现: MemTree平衡k叉树实现O(log N)写入，保留时间局部证据；对比MemPalace原始追加O(1)但无结构化时间维护.
**目标**: 修复全部19 CRITICAL + 52 HIGH，检索层学术对齐，架构债务清理

---

## 执行摘要

第四轮审查发现 **19 CRITICAL + 52 HIGH + 31 MEDIUM + 18 LOW**，覆盖14个核心模块。deepxiv学术调研揭示更深层问题：**检索层质量是系统性能上限的决定性因素**（论文证据：20点准确度差异来自检索方法），而我们的检索层存在 BM25 log缺失、无reranking、无Hebbian融合三重缺陷，在学术基准上可能排名末位。

beta3采取**三波推进策略**：
- **Wave 1**: 消灭所有CRITICAL + HIGH级别bug，恢复系统可靠性
- **Wave 2**: 检索层学术对齐（中低难度优化），引入reranking、修复BM25、图边参与搜索
- **Wave 3**: 高难架构抉择 — spreading activation完整实现、线程安全重构、hub detection

---

## Wave 1 — Bug 修复（预计2-3天）

### W1.1 存储层修复（core.py + __init__.py）

| # | 问题 | 文件 | 修复方案 | 验证 |
|---|------|------|---------|------|
| 1 | BM25 IDF缺少`log()` | `core.py:_bm25_search_scored` | 公式改为`log((N-df+0.5)/(df+0.5)+1.0)` | 单元测试：固定语料下排名与标准BM25一致 |
| 2 | `_safe_write`非原子 | `core.py` | 写入临时文件→`os.replace()`原子替换 | 模拟磁盘满场景测试 |
| 3 | `_stat_flush_worker`死代码 | `core.py` | 删除队列和线程，或统一走队列 | 代码覆盖确认无引用 |
| 4 | `_write_path_locks`内存泄漏 | `core.py` | 写完后`del`或使用`WeakValueDictionary` | 长时间运行监控 |
| 5 | `_tokenise` CJK索引bug | `core.py` | `for`循环改为`while i < len(...)` | CJK文本分词单元测试 |
| 6 | `_update_cache_for_put` O(n²) | `core.py` | 用`rank/created`作key而非UUID字符串 | 大规模插入性能测试 |
| 7 | `put()`竞态条件 | `__init__.py` | 查重时同时检查磁盘`id_to_path`索引 | 并发写入测试 |
| 8 | `update()`两文件皆失风险 | `__init__.py` | 原子写：temp→replace→删旧 | 崩溃恢复测试 |

### W1.2 反射引擎修复（reflection/engine.py）

| # | 问题 | 修复方案 | 验证 |
|---|------|---------|------|
| 9 | `_parse_reflect_output`贪婪正则 | 改用`json.JSONDecoder.raw_decode`迭代提取多个JSON对象 | 多candidate/嵌套JSON测试用例 |
| 10 | `_repair_truncated_json`扁平对象bug | 修复`last_safe`对无嵌套对象的处理 | 截断JSON修复单元测试 |
| 11 | `_approve_skill`路径遍历 | `sanitize_filename`：仅保留`a-z0-9_-.` | 安全测试：尝试`../etc/passwd` |
| 12 | prompt缺少`supersedes_reason`字段 | 在`_FULL_REFLECT_SYSTEM`的JSON schema中显式加入 | LLM输出检查 |
| 13 | `_run_embedding_reflection`硬编码zone | 根据`scope`推断zone，或暴露参数 | zone分配单元测试 |

### W1.3 嵌入引擎修复（search/embed.py）

| # | 问题 | 修复方案 | 验证 |
|---|------|---------|------|
| 14 | MD5违规 | `hashlib.sha256()`替换`md5()` | 哈希输出长度检查 |
| 15 | 手动padding错误 | 使用`tokenizer.pad(..., padding=True)` |  attention mask一致性检查 |
| 16 | `_classify_intent`重复import | `import numpy as np`移到模块顶部 | 性能benchmark对比 |

### W1.4 图记忆修复（graph/）

| # | 问题 | 修复方案 | 验证 |
|---|------|---------|------|
| 17 | SQLite线程安全 | 使用连接池（每个线程独立连接）或`queue.Queue`序列化访问 | 并发压力测试 |
| 18 | PageRank O(n²·d) | 构建反向邻接表，内层循环从O(n)降到O(d) | 1000节点benchmark |
| 19 | `_ModuleProxy` raise KeyError | 改为`AttributeError` | `getattr(obj, 'x', default)`测试 |

### W1.5 仪表盘修复（dashboard/plugin_api.py）

| # | 问题 | 修复方案 | 验证 |
|---|------|---------|------|
| 20 | `delete_memory`无事务 | 图清理包进`BEGIN...COMMIT` | 部分删除后一致性检查 |
| 21 | `create_memory` race-prone定位 | 返回写入时确定的ID而非body匹配 | API并发测试 |

### W1.6 工具处理器修复（tools/handlers.py）

| # | 问题 | 修复方案 | 验证 |
|---|------|---------|------|
| 22 | `_auto_rebalance_zones`绕过原子写 | 使用`MemoryStore.update()`替代直接`write_text()` | 并发rebalance测试 |
| 23 | `_tool_srh_memory_write` cycle check不完整 | 遍历`supersedes`所有目标进行环检测 | 多目标supersedes环测试 |
| 24 | `_tool_srh_memory_history` fallback错误 | `latest_id`应使用查询的memory ID而非`root_id` | lineage测试 |

### W1.7 生命周期钩子修复（hooks/lifecycle.py）

| # | 问题 | 修复方案 | 验证 |
|---|------|---------|------|
| 25 | `_pre_llm_call` NameError | 添加late-binding import `_reflection_mode = _lb("_reflection_mode")` | 运行时hook测试 |
| 26 | token预算未使用 | 使用`_estimate_tokens`执行上下文截断 | 长上下文截断测试 |
| 27 | `post_tool_call`未实现 | 实现tool result关联的graph enrichment | graph边生成测试 |

---

## Wave 2 — 功能策略优化（中低难度，预计3-5天）

### W2.1 检索层学术对齐（核心，优先级最高）

基于 [Retrieval Bottleneck] Yuan et al., arXiv:2603.02473: **检索方法决定20点准确度差异**（hybrid 77.2% vs BM25 57.1%），写策略仅3-8点差异。投资应向检索质量倾斜。

#### W2.1.1 Hybrid Search + Reranking 实现

**现状**: `fusion_search`只是简单合并cosine和BM25结果，无reranking层。

**目标**: 实现三层检索管道

```
Layer 1: Recall
  ├── Embedding retrieval: top-2k by cosine similarity
  └── BM25 retrieval: top-2k by keyword score

Layer 2: Fusion
  └── Pool union, deduplicate

Layer 3: Rerank (NEW)
  └── Cross-encoder or LLM-as-judge对pool后的结果重排，取top-k
```

**实现策略**: 
- 引入轻量级cross-encoder（如`ms-marco-MiniLM-L-6-v2`ONNX版）作为reranker
- 若cross-encoder不可用，降级为加权融合：`score = α·cosine + β·bm25 + γ·recency + δ·effectiveness`
- 保持零依赖fallback：纯加权融合在无ONNX时可用

**验证**: 在内部benchmark上对比fusion vs hybrid-rerank的召回精度

#### W2.1.2 BM25 完整修复

- 修复IDF `log()`（Wave 1已完成）
- 引入CJK停用词过滤（中文、日文、韩文常见停用词表）
- 修复`_extract_keywords`评分公式：`score = len(t) / (1 + c)` 改为 `df / (1 + cf)`（稀有词得分更高）

#### W2.1.3 Hebbian Boosting 初版（轻量融合）

**学术依据**: [HeLa-Mem] arXiv:2604.16839, Sec.3.4: 双路径检索 `S_total = S_semantic + S_hebbian`，spreading activation从语义种子出发沿Hebbian边传播，multi-hop查询中可将低语义相似度(0.35)记忆通过Hebbian boost(0.36)提升至可召回范围(0.71).

**实现**: 在`fusion_search`中增加可选的Hebbian boost
```python
if graph_boost and memory_id in graph:
    neighbor_score = max(graph.get_edge_weight(memory_id, query_related_id), 0)
    final_score = semantic_score + beta * neighbor_score
```

**约束**: 不引入复杂BFS，仅对已有检索结果的top-k做一阶邻居boost。这是向full spreading activation的过渡。

### W2.2 代码质量与工程债务

| 项 | 方案 | 难度 |
|----|------|------|
| 引入`tests/`目录 | 核心路径单元测试（BM25、frontmatter解析、lineage、原子写） | 低 |
| `_write_path_locks`清理 | `WeakValueDictionary`替换普通dict | 低 |
| `__init__.py`拆分准备 | 将图工具、slash命令处理拆到独立模块（保持向后兼容） | 中 |
| 异常信息结构化 | 工具handler统一返回结构化错误码 | 低 |
| `valid_from`/`valid_until`过滤 | `list_active()`自动过滤过期记忆 | 低 |

### W2.3 反射管道优化

**学术依据**: [Retrieval Bottleneck] arXiv:2603.02473, Sec.3.1, Table 1: Basic RAG（零LLM调用的原始3-turn分块）在hybrid检索下达81.1%，超越Mem0式Extracted Facts(77.3%)和MemGPT式Summarized Episodes(73.3%). Token F1对比: Basic RAG 0.240 vs Extracted Facts 0.220. 论文结论: "raw chunked storage, which requires zero LLM calls, matches or outperforms expensive lossy alternatives."

**策略**: 不完全移除reflection，而是提供raw-chunk模式选项

```yaml
plugins:
  mem_reflection_hermes:
    reflection_mode: "raw_chunk"  # 新增: raw_chunk | fact_extraction | summary
```

- `raw_chunk`: 保存原始对话片段，零LLM调用，依赖检索层质量
- `fact_extraction`: 现有模式，保留supersedes governance和zone分配
- `summary`: MemGPT式摘要

**默认改为`raw_chunk`**，因为论文证明在好的检索下这是最优性价比。

### W2.4 SUPERSEDES 语义清理

**方案**: 将SUPERSEDES边从ahe_graph中移除，改为纯lineage层管理

- 保留`MemoryFrontmatter.supersedes`字段
- lineage helpers (`is_superseded`, `latest_for`, `lineage_chain`) 独立运作
- ahe_graph只存储Hebbian co-activation边（`co_occurs`, `related`）
- dashboard graph endpoint不再需要从`seen_nodes`中过滤SUPERSEDES

**向后兼容**: 读取时自动迁移旧数据中的SUPERSEDES边到lineage层

---

## Wave 3 — 高难策略抉择（需深入设计与决策）

### W3.1 Spreading Activation 完整实现

**学术标杆**: [HeLa-Mem] arXiv:2604.16839, Sec.3.4 "Dual-Path Retrieval" 和 Sec.4.5 "Trace Analysis of Associative Recall". 消融实验: w/o Spreading Activation导致性能从34.74%降至32.19%(multi-hop 36.04%→33.88%)，证明"without spreading activation, the system degrades to a single semantic path, failing to retrieve memories that are semantically distant from the query but strongly associated through Hebbian connections."

**设计抉择点**:

1. **激活传播算法**
   - 选项A：简单BFS，一阶邻居boost（已在Wave 2.1.3实现）
   - 选项B：完整迭代扩散，类似PageRank的单步传播
   - 选项C：带阈值的截断传播，只传播超过阈值θ的激活

2. **语义+Hebbian融合公式**
   - HeLa-Mem: `S_total = S_semantic + β·S_hebbian`
   - 变体: `S_total = (1-λ)·S_semantic + λ·S_hebbian`
   - 是否需要非线性（如sigmoid归一化）？

3. **冷启动问题**
   - HeLa-Mem论文承认：早期会话Hebbian边不足，需要bootstrap
   - 方案：用初始embedding相似度作为Hebbian边的先验权重

**建议**: 先实现选项A + λ线性融合 + embedding先验bootstrap。根据benchmark结果决定是否升级。

### W3.2 线程安全重构

**当前问题**: `check_same_thread=False` + `RLock` 不保护SQLite连接句柄

**选项对比**:

| 方案 | 复杂度 | 性能 | 可靠性 |
|------|--------|------|--------|
| A. 每个线程独立连接 | 低 | 好 | 高 |
| B. 单线程串行队列 | 低 | 一般 | 最高 |
| C. SQLAlchemy连接池 | 中 | 好 | 高 |
| D. 进程隔离（每个worker进程一个graph） | 高 | 最好 | 高 |

**建议**: 方案A。`threading.local()`存储每个线程的连接，`get_conn()`时检查thread-local是否存在，不存在则创建。

### W3.3 Hub Detection + Hebbian Distillation

**学术概念**: [HeLa-Mem] arXiv:2604.16839, Sec.3.3 "Reflective Memory Agent" 和 Sec.4.4 "Reflective Agent: Memory Lifecycle Management". Hub节点定义: degree ≥ 10的节点（图4中示例最大degree=17）。Reflective Agent识别hub后应用"Hebbian Distillation"将episodic cluster合并为semantic entry。孤立节点(degree < 4且无近期访问)由Adaptive Forgetting机制标记删除。

**设计问题**:

1. **Hub定义**: 度数阈值 vs PageRank阈值 vs 度增长率？
2. **蒸馏触发时机**: 每次反射时检查？周期性后台任务？图大小阈值触发？
3. **蒸馏产物**: 新semantic记忆写入哪个zone？如何与原始episodic关联？
4. **episodic保留策略**: 蒸馏后原始记忆删除、归档、还是保留链接？
5. **蒸馏质量**: LLM蒸馏可能产生幻觉，如何验证？

**建议决策**: 暂不实现全自动蒸馏。改为dashboard中的**手动蒸馏按钮**：用户点击hub节点→触发LLM蒸馏→生成新记忆→手动审核。积累使用数据后再评估自动化策略。

### W3.4 MemTree 时间索引评估

**学术概念**: [MemForest] arXiv:2605.23986, Sec.3 "MemTree Design" 和 Appendix B.8. MemTree为平衡k叉树，每次插入触及leaf-to-root路径O(log N)。对比MemPalace(原始追加O(1))和Mem0(mutable state O(K)候选比较+状态依赖更新)。MemForest写路径: σ=Route(r), T'=Insert(T_σ,r), A_σ=RefreshDirty(T'_σ)，dirty nodes跨scope和同级可并行刷新。

**评估问题**:

1. 我们的场景（50-500条记忆/scope）是否真的需要O(log N)？当前O(n)在50条时仅0.8ms
2. MemTree的实现复杂度（平衡树维护、范围查询、作用域路由）是否值得？
3. 与我们现有的zone系统如何整合？zone是用户语义分区，MemTree是时间索引，二者正交

**建议决策**: 当前规模下MemTree**过度设计**。先实现简单的**时间范围索引**（按valid_from/created排序的跳表或有序列表），满足`valid_from`/`valid_until`过滤即可。当平均scope记忆数>1000时重新评估MemTree。

### W3.5 评估基础设施

**缺失**: 当前无任何benchmark

**需要建立**:

1. **检索benchmark**: 固定50条记忆的测试集，人工标注query→relevant memories映射，测量Recall@k和MRR
2. **LoCoMo适配器**: 若LoCoMo开源可用，集成其评估协议
3. **回归测试**: 每次修改后运行标准query set，防止检索质量退化
4. **性能benchmark**: `bench_latency.py`扩展为持续监控工具

---

## 时间线与里程碑

| 阶段 | 时间 | 里程碑 | 可交付成果 |
|------|------|--------|-----------|
| Wave 1 | Day 1-3 | 所有CRITICAL/HIGH修复 | 绿色CI、无已知崩溃路径 |
| Wave 2 | Day 4-8 | 检索层学术对齐 | hybrid rerank、Hebbian boost初版、测试目录 |
| Wave 3 | Day 9-14 | 高难设计决策完成 | spreading activation设计文档、线程安全PR、评估协议 |
| beta3发布 | Day 15 | 合并到main | 完整CHANGELOG、性能对比报告 |

## 风险与回退策略

| 风险 | 影响 | 缓解 |
|------|------|------|
| reranker引入增加延迟 | 用户感知搜索变慢 | ONNX cross-encoder < 10ms；超时降级为加权融合 |
| spreading activation效果不佳 | 投入无回报 | Wave 2的轻量boost已可用，Wave 3是可选增强 |
| SQLite连接池重构引入regression | 图数据损坏 | 完整备份+迁移测试；回退到单线程队列 |
| LLM蒸馏幻觉 | 污染semantic记忆 | 手动审核gate；confidence阈值过滤 |

## 关键指标（beta3验收标准）

- [ ] 零CRITICAL/HIGH已知问题
- [ ] 检索benchmark Recall@5 >= 80%（对比beta2的基线）
- [ ] 单元测试覆盖率 >= 60%（新增模块）
- [ ] LoCoMo子集准确率（若有benchmark适配）
- [ ] 搜索延迟P99 < 50ms（50记忆scope）
