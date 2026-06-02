---
name: wave2-sdd
description: Wave 2 系统设计文档 — 检索层学术对齐 (SDD)
metadata:
  type: project
  originSessionId: e35ae249-246a-4424-b5f5-a19307cfbded
---

# Wave 2 SDD: 检索层学术对齐

**版本**: v0.9.2-beta3-W2  
**日期**: 2026-06-02  
**作者**: sea-monsters  
**状态**: DRAFT → 待审批  

---

## 1. 引言

### 1.1 目的
本文档定义 Wave 2 中检索层学术对齐的系统设计，包括：
- Hybrid Search + Reranking 三层检索管道
- BM25 完整修复（CJK 停用词、关键词评分公式）
- Hebbian Boosting 初版（一阶邻居 boost）
- raw_chunk 反射模式
- SUPERSEDES 语义清理

### 1.2 学术依据
- [Retrieval Bottleneck] Yuan et al., arXiv:2603.02473: **检索方法决定 20 点准确度差异**（hybrid 77.2% vs BM25 57.1%），写策略仅 3-8 点差异。
- [HeLa-Mem] arXiv:2604.16839, Sec.3.4: 双路径检索 `S_total = S_semantic + β·S_hebbian`，spreading activation 使 multi-hop 推理提升 2-3 点。
- [Retrieval Bottleneck] Sec.3.1: Basic RAG（零 LLM 调用的原始 3-turn 分块）在 hybrid 检索下达 81.1%，超越 Mem0 式 Extracted Facts(77.3%)。

### 1.3 设计原则
1. **检索投资优先**: 论文证明检索方法对系统性能上限起决定性作用，投资向检索质量倾斜。
2. **零依赖 fallback**: 所有 ONNX/embedding 增强必须有纯 Python 降级路径。
3. **向后兼容**: 现有 API (`search`, `fusion_search`) 行为不变，新增参数默认关闭。
4. **可度量**: 每项改动必须有 benchmark 验证点。

---

## 2. 系统架构

### 2.1 当前架构（As-Is）

```
MemoryStore.search()
  ├── embed_search() ──→ cosine similarity (如果 ONNX 可用)
  └── _bm25_search() ──→ TF-IDF + effectiveness boost

MemoryStore.fusion_search()
  ├── _bm25_search_scored() ──→ BM25 分数
  ├── get_neighbors() ──→ 1-hop 图激活平均
  ├── _calc_supersedes_depth() ──→ depth penalty
  └── α·bm25 + β·graph·sup_factor ──→ 简单线性融合
```

### 2.2 目标架构（To-Be）

```
MemoryStore.fusion_search() [v0.9.2-beta3-W2]
  ├── Layer 1: Recall (并行)
  │   ├── embed_recall() ──→ top-2k cosine similarity
  │   ├── bm25_recall() ──→ top-2k BM25 score
  │   └── graph_recall() ──→ [可选] 1-hop Hebbian seed expansion
  ├── Layer 2: Fusion
  │   ├── Pool union of all recall results
  │   ├── Deduplicate by memory_id
  │   └── Normalize scores per channel to [0,1]
  ├── Layer 3: Rerank
  │   ├── Cross-encoder rerank (ONNX, optional)
  │   ├── Hebbian boost (一阶邻居加权)
  │   └── Weighted fusion fallback: α·cosine + β·bm25 + γ·recency + δ·effectiveness
  └── Output: top-k results
```

---

## 3. 详细设计

### 3.1 W2.1 Hybrid Search + Reranking

#### 3.1.1 接口变更

```python
def fusion_search(
    self, query: str, k: int = 5,
    zone: Optional[str] = None,
    alpha: float = 0.5,       # cosine 权重
    beta: float = 0.3,        # BM25 权重
    gamma: float = 0.1,       # recency 权重 (新增)
    delta: float = 0.1,       # effectiveness 权重 (新增)
    hebbian_beta: float = 0.0, # Hebbian boost 系数 (W2.3, 默认0)
    use_reranker: bool = False, # Cross-encoder rerank (默认False)
    include_history: bool = False,
) -> List[LoadedMemory]:
```

#### 3.1.2 Layer 1: Recall

**Embed Recall** (如果 ONNX 可用):
- 输入: query embedding
- 输出: `List[(memory_id, cosine_score)]`, top `k * 4`
- 复杂度: O(n·d), n=记忆数, d=384 (MiniLM 维度)

**BM25 Recall**:
- 输入: query tokens
- 输出: `List[(memory_id, bm25_score)]`, top `k * 4`
- 使用修复后的 BM25 公式（含 CJK 停用词过滤）

**Graph Recall** (W2.3 预热):
- 输入: embed/bm25 结果的 memory_ids 作为 seed
- 输出: 1-hop Hebbian 邻居, 带 weight
- 仅用于 Layer 3 的 Hebbian boost

#### 3.1.3 Layer 2: Fusion

**Pool & Deduplicate**:
```python
pool: Dict[str, Dict[str, float]] = {}  # mem_id -> {cosine, bm25, recency, eff}
for mid, score in embed_results:
    pool.setdefault(mid, {})["cosine"] = score
for mid, score in bm25_results:
    pool.setdefault(mid, {})["bm25"] = score
# 填充缺失通道为 0
```

**Normalize**:
```python
for channel in ("cosine", "bm25", "recency", "eff"):
    vals = [v.get(channel, 0) for v in pool.values()]
    max_val = max(vals) if vals else 1.0
    for mid in pool:
        pool[mid][channel] = pool[mid].get(channel, 0) / max_val
```

#### 3.1.4 Layer 3: Rerank

**选项 A: Cross-encoder Reranker** (ONNX, 可选)

引入轻量级 cross-encoder (`ms-marco-MiniLM-L-6-v2` ONNX 版):
```python
class CrossEncoderReranker:
    def rerank(self, query: str, memories: List[LoadedMemory]) -> List[Tuple[str, float]]:
        # 输入: "query ||| memory_body"
        # 输出: relevance score
        # 延迟: ~5-10ms per pair on CPU
```

**选项 B: Weighted Fusion Fallback** (默认)
```python
final_score = (
    alpha * cosine_norm +
    beta * bm25_norm +
    gamma * recency_norm +
    delta * eff_norm +
    hebbian_beta * hebbian_boost
)
```

**Recency Score**:
```python
def _recency_score(created_iso: str) -> float:
    age_days = (now - datetime.fromisoformat(created_iso)).days
    return math.exp(-age_days / 30.0)  # 30-day half-life
```

#### 3.1.5 降级策略

| 条件 | 行为 |
|------|------|
| ONNX 不可用 | Embed recall 跳过，BM25 独占 |
| Cross-encoder 不可用 | 使用 weighted fusion |
| Graph 不可用 | hebbian_beta 自动置 0 |
| 所有增强不可用 | 降级为现有 BM25 + effectiveness |

---

### 3.2 W2.2 BM25 完整修复

#### 3.2.1 CJK 停用词过滤

当前 `_tokenise` 只有英文停用词表。新增 CJK 停用词:

```python
_CJK_STOPWORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "那",
    "什么", "怎么", "为什么", "如何", "可以", "一下", "一些",
    # 日文
    "の", "に", "は", "を", "た", "が", "で", "て", "と", "し",
    # 韩文
    "은", "는", "이", "가", "을", "를", "에", "의", "로", "과",
}
```

在 `_tokenise` 中过滤:
```python
if len(part) >= _MIN_TOKEN_LEN and part not in _STOPWORDS and part not in _CJK_STOPWORDS:
    tokens.append(part)
```

#### 3.2.2 `_extract_keywords` 评分公式修复

当前:
```python
score = c * len(t) / (1 + sum(1 for x in tokens if x == t))
# 问题: 高频词得分更高（与 TF-IDF 意图相反）
```

修复为:
```python
# 稀有词得分更高: 1 / (1 + tf) * len(t) 作为长度加成
score = len(t) / (1 + c)  # c = term frequency
# 或更准确的: idf_like = math.log(len(tokens) / (c + 1) + 1)
score = idf_like * len(t)
```

---

### 3.3 W2.3 Hebbian Boosting 初版

#### 3.3.1 设计

学术依据: [HeLa-Mem] `S_total = S_semantic + β·S_hebbian`

实现约束: 不引入复杂 BFS，仅对已有检索结果的 top-k 做一阶邻居 boost。

```python
def _hebbian_boost(
    pool: Dict[str, Dict[str, float]],
    seed_ids: List[str],
    gm: GraphMemoryManager,
    beta: float = 0.2,
) -> None:
    """Boost pool scores based on 1-hop Hebbian neighbors of seed results."""
    for mid in list(pool.keys()):
        try:
            neighbors = gm.store.get_neighbors(mid, min_weight=0.1, limit=10)
            max_neighbor_weight = max(
                (n.get("weight", 0) for n in neighbors if n.get("memory_id") in pool),
                default=0.0
            )
            pool[mid]["hebbian"] = max_neighbor_weight
        except Exception:
            pass
```

在 fusion_search 中:
```python
if hebbian_beta > 0 and gm:
    _hebbian_boost(pool, [m.id() for m in results], gm, hebbian_beta)
```

---

### 3.4 W2.4 raw_chunk 反射模式

#### 3.4.1 背景

[Retrieval Bottleneck] 证明: Basic RAG（零 LLM 调用的原始分块）在 hybrid 检索下达 81.1%，超越 Mem0 式 Extracted Facts(77.3%)。投资检索层 > 投资反射质量。

#### 3.4.2 设计

新增 `reflection_mode: "raw_chunk"`:

```yaml
plugins:
  mem_reflection_hermes:
    reflection_mode: "raw_chunk"  # 新增选项
```

有效值:
- `"raw_chunk"`: 保存原始对话片段，零 LLM 调用
- `"embedding"` (默认): 现有 embedding-based 反射
- `"llm"`: LLM-driven 反射（昂贵，保留兼容）
- `"hybrid"`: embedding + LLM fallback

`raw_chunk` 实现:
```python
def _run_raw_chunk_reflection(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Save raw conversation chunks as episode memories. Zero LLM calls."""
    mem_store = _get_mem_store()
    accepted = []
    # Group messages into chunks (e.g., 3-turn windows)
    chunk_size = 3
    for i in range(0, len(messages), chunk_size):
        chunk = messages[i:i+chunk_size]
        body = _format_chunk(chunk)
        if len(body) < 20:
            continue
        # Direct write without LLM analysis
        fm = MemoryFrontmatter.new(
            source="raw_chunk",
            confidence="medium",
            tags=["episode", "raw_chunk"],
            zone="episode",
        )
        path = mem_store.put("user", fm, body)
        accepted.append({"id": fm.id, "body_preview": body[:100]})
    return {
        "mode": "raw_chunk",
        "accepted_memories": accepted,
        "chunks_created": len(accepted),
    }
```

#### 3.4.3 默认切换

默认 `reflection_mode` 从 `"embedding"` 改为 `"raw_chunk"`:
```python
def _reflection_mode() -> str:
    return plugin_config().get("reflection_mode", "raw_chunk")  # 默认改为 raw_chunk
```

---

### 3.5 W2.5 SUPERSEDES 语义清理

#### 3.5.1 问题

当前 SUPERSEDES 边同时存在于:
1. `MemoryFrontmatter.supersedes` (lineage 层)
2. `graph_edges` 表中 `relation='SUPERSEDES'` (图层)

这导致:
- dashboard 需要从 `seen_nodes` 中特殊处理 SUPERSEDES
- ahe_graph 中混入了非 Hebbian 的结构化边
- 维护两份数据的风险

#### 3.5.2 设计

**原则**: SUPERSEDES 是 lineage/版本控制概念，不是 associative memory 概念。

**变更**:
1. `GraphStore.add_supersedes_edge()`: 保留但标记为 deprecated，不再写入 graph_edges
2. `GraphStore.get_neighbors()`: 排除 `relation='SUPERSEDES'`
3. Dashboard `/graph`: SUPERSEDES 边从 `frontmatter.supersedes` 直接构造，不再查 graph_store
4. `ahe_graph` 只存储 Hebbian 边 (`co_occurs`, `co_used_in_task`)

**向后兼容**:
```python
def _migrate_supersedes_edges(graph_store, mem_store):
    """One-time migration: read old SUPERSEDES edges, ensure frontmatter has them."""
    # 仅在插件启动时运行一次
    old_edges = graph_store._get_supersedes_edges()
    for old_id, new_id in old_edges:
        mem = mem_store.get(new_id)
        if mem and old_id not in (mem.frontmatter.supersedes or []):
            # 添加到 frontmatter
            mem.frontmatter.supersedes.append(old_id)
            # 重写文件
            ...
    # 删除 graph 中的 SUPERSEDES 边
    graph_store._purge_supersedes_edges()
```

---

## 4. 数据模型与接口

### 4.1 新增配置项

```yaml
plugins:
  mem_reflection_hermes:
    reflection_mode: "raw_chunk"  # raw_chunk | embedding | llm | hybrid
    search:
      alpha: 0.5          # cosine 权重
      beta: 0.3           # BM25 权重
      gamma: 0.1          # recency 权重
      delta: 0.1          # effectiveness 权重
      hebbian_beta: 0.0   # Hebbian boost (0=off)
      use_reranker: false # cross-encoder rerank
      recall_multiplier: 4  # recall 阶段 over-fetch 倍数
    context_token_budget: 2000  # pre_llm_call 截断预算
```

### 4.2 新增/修改函数签名

```python
# core.py
def _tokenise(s: str) -> List[str]:  # 增加 CJK 停用词过滤

def _extract_keywords(text: str, top_k: int = 5) -> List[str]:  # 修复评分公式

# __init__.py / MemoryStore
class MemoryStore:
    def fusion_search(
        self, query: str, k: int = 5,
        zone: Optional[str] = None,
        alpha: float = 0.5, beta: float = 0.3,
        gamma: float = 0.1, delta: float = 0.1,
        hebbian_beta: float = 0.0,
        use_reranker: bool = False,
        include_history: bool = False,
    ) -> List[LoadedMemory]: ...

# graph/ahe_graph.py
class GraphStore:
    def get_neighbors(self, memory_id: str, ...,
                      exclude_relations: Optional[List[str]] = None) -> List[dict]: ...

# reflection/engine.py
def _run_raw_chunk_reflection(messages: List[Dict[str, Any]]) -> Dict[str, Any]: ...
```

---

## 5. 实现顺序

| 顺序 | 任务 | 依赖 | 预计时间 |
|------|------|------|---------|
| 1 | W2.2 BM25 完整修复 | 无 | 2h |
| 2 | W2.3 Hebbian Boosting | 无 | 2h |
| 3 | W2.1 Hybrid Search + Reranking | W2.2, W2.3 | 4h |
| 4 | W2.4 raw_chunk 反射模式 | 无 | 2h |
| 5 | W2.5 SUPERSEDES 清理 | 无 | 2h |
| 6 | 集成测试 + Benchmark | 全部 | 4h |

---

## 6. 验证计划

### 6.1 单元测试

```python
def test_bm25_cjk_stopwords():
    """CJK 停用词不应影响 BM25 分数."""
    ...

def test_hebbian_boost_basic():
    """一阶邻居 boost 应提升相关记忆分数."""
    ...

def test_fusion_search_weighted_fallback():
    """ONNX 不可用时 weighted fusion 正常工作."""
    ...

def test_raw_chunk_reflection():
    """raw_chunk 模式零 LLM 调用，直接写入 episode."""
    ...
```

### 6.2 Benchmark

```python
# bench_retrieval.py
queries = [
    ("user prefers dark mode", ["mem_dark_mode", "mem_ui_prefs"]),
    ("how to handle errors", ["mem_error_handling", "mem_go_patterns"]),
    ...
]

def benchmark(search_fn, queries, k=5):
    recall_at_k = []
    mrr = []
    for q, expected in queries:
        results = search_fn(q, k=k)
        result_ids = [r.id() for r in results]
        hits = len(set(expected) & set(result_ids))
        recall_at_k.append(hits / len(expected))
        # MRR
        for i, rid in enumerate(result_ids):
            if rid in expected:
                mrr.append(1.0 / (i + 1))
                break
        else:
            mrr.append(0.0)
    return {
        "recall@k": sum(recall_at_k) / len(recall_at_k),
        "mrr": sum(mrr) / len(mrr),
    }
```

### 6.3 验收标准

- [ ] BM25 CJK 停用词过滤后，中文查询召回率提升 ≥ 5%
- [ ] Hebbian boost 开启后，关联记忆召回率提升 ≥ 3%
- [ ] raw_chunk 模式零 LLM 调用，延迟 < 1ms/记忆
- [ ] fusion_search 全降级路径正常工作（无 ONNX、无 graph）
- [ ] SUPERSEDES 清理后 graph 只含 Hebbian 边

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| reranker ONNX 模型加载失败 | 搜索降级为 weighted fusion | 模型 lazy-load + try/except |
| Hebbian boost 过度提升无关记忆 | 检索质量下降 | β 系数默认 0，需显式开启 |
| raw_chunk 导致 episode zone 膨胀 | 存储增长 | 定期 decay + zone rebalance |
| SUPERSEDES 迁移遗漏边 | lineage 断裂 | 启动时一次性扫描 + 日志记录 |

---

## 8. 附录

### 8.1 术语表

| 术语 | 定义 |
|------|------|
| Recall | 检索阶段，从全量数据中召回候选集 |
| Rerank | 对召回结果重新排序，提升 top-k 质量 |
| Hebbian boost | 基于图关联的分数增强 |
| raw_chunk | 原始对话片段存储，零 LLM 处理 |
| Cross-encoder | 双塔交互式 relevance 打分模型 |

### 8.2 参考文献

1. Yuan et al., "Diagnosing Retrieval vs. Utilization Bottlenecks in LLM Agent Memory", arXiv:2603.02473, 2026-03-02.
2. HeLa-Mem, "Hebbian Learning and Associative Memory for LLM Agents", arXiv:2604.16839, 2026-04-18.
3. Robertson & Zaragoza, "The Probabilistic Relevance Framework: BM25 and Beyond", Foundations and Trends in Information Retrieval, 2009.
