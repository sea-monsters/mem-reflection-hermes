# v1.0-beta2 架构文档 — 实现状态

> **更新日期**: 2026-06-06
> **状态**: v1.1-beta 已发布 — runtime 模块稳定，227 测试通过，死代码清理完成，Hermes Agent v0.16.0 兼容
> **Wave A 性能优化**: bm25s + datasketch 引入完成
> **旧版本记录**: 见 [1.0-beta2-redesign.md](1.0-beta2-redesign.md) (原始设计) 和 [beta2-plan.md](beta2-plan.md) (开发计划)
>
> **v1.1 变更**: QueryTemplate/ResultCache 已从 search.py 移除；episode compaction 新增；`PluginLlmStructuredResult.error` → `.content_type`/`.parsed`

---

## 1. 模块依赖 DAG

```
store.py         ← 叶子模块，无项目内导入
  ├── python-frontmatter  (YAML 解析)
  ├── tiktoken            (token 计数)
  ├── datasketch          (MinHash LSH 近似去重)
  └── sqlite3             (memories.db)

search.py        ← 依赖 store.py
  ├── numpy               (embedding 矩阵)
  ├── bm25s               (BM25 检索，10-50x 加速)
  ├── cachetools.TTLCache (结果缓存)
  ├── functools.lru_cache (单条 embedding 缓存)
  └── ONNX / sentence-transformers (可选 embedding 引擎)

graph.py         ← 依赖 store.py (仅 cross_zone 需要)
  └── sqlite3             (graph.db，独立数据库)

reflect.py       ← 依赖 store.py + search.py + graph.py
  └── json                (reflect-log.jsonl)

context.py       ← 依赖 store.py + search.py
  └── 纯函数，无状态

__init__.py      ← 导入以上所有模块，注册 17 工具
  └── 旧模块仍在并行运行 (推至 beta3 清理)
```

无循环导入。`late_binding.py` 仅在旧代码路径中使用，beta2 模块不依赖它。

---

## 2. store.py — MemoryStore (1024 行)

### 2.1 职责

- **SQLite 主索引** (memories.db): memories, tags, supersedes, stats 四表
- **Markdown 冷存储**: YAML frontmatter + body，git 友好，人类可读
- **配置 & 路径**: hermes_home, load_config, plugin_config 等
- **Token 估算**: tiktoken cl100k_base，CJK-aware fallback
- **CJK 分词器**: bigram tokenizer + 中/日/韩 stopwords
- **Skill 模型**: SkillFrontmatter, LoadedSkill (读文件，无 SQLite 索引)

### 2.2 SQLite Schema (memories.db)

```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL, zone TEXT DEFAULT 'general',
    confidence TEXT DEFAULT 'medium', pinned INTEGER DEFAULT 0, rank INTEGER DEFAULT 0,
    created TEXT NOT NULL, source TEXT DEFAULT 'user',
    valid_from TEXT, valid_until TEXT, context_scope TEXT,
    version INTEGER DEFAULT 1, supersedes_reason TEXT,
    body_hash TEXT NOT NULL, path TEXT NOT NULL
);
CREATE TABLE tags (memory_id TEXT, tag TEXT, PRIMARY KEY (memory_id, tag));
CREATE TABLE supersedes (old_id TEXT, new_id TEXT, reason TEXT, PRIMARY KEY (old_id, new_id));
CREATE TABLE stats (memory_id TEXT, event TEXT, at TEXT);
```

### 2.3 公开 API

```python
class MemoryStore:
    # 生命周期
    def __init__(self, user_root: Path, project_root: Path | None)
    def _sync_from_disk(self)          # 启动时 rglob("*.md") 同步 SQLite
    def _init_db(self)                  # 建表

    # CRUD
    def put(scope, fm, body) -> Path           # 原子写 .md + INSERT SQLite
    def get(mem_id) -> LoadedMemory | None     # SQLite 查询 + 读 .md 文件
    def get_by_id(mem_id) -> ...               # 兼容别名 (CLUQI/dashboard)
    def delete(scope, mem_id) -> bool           # 删 .md + CASCADE SQLite
    def update(mem_id, body, zone, ...) -> LoadedMemory  # 原子更新

    # 查询
    def list(*, zone, active_only, sort, limit) -> list[LoadedMemory]
    def list_active() -> list[LoadedMemory]     # WHERE id NOT IN supersedes.old_id
    def list_pinned() -> list[LoadedMemory]     # pinned=1 AND active
    def zone_counts() -> dict[str, int]         # GROUP BY zone

    # 版本溯源 (supersedes 链)
    def is_superseded(mem_id) -> bool
    def latest_for(mem_id) -> LoadedMemory | None    # 沿链走到最新版本
    def lineage_chain(mem_id, max_depth=10) -> list[LoadedMemory]  # root→latest

    # 效能追踪
    def record_stat(memory_id, event)           # INSERT INTO stats
    def effectiveness(memory_id=None) -> dict   # SELECT + 聚合 MemoryEffectiveness

    # 健康 & 维护
    def health_metrics() -> dict                # 总数/活跃/重复聚类 (Jaccard)
    def reorder(memory_ids) -> list[str]        # 重设 rank
    def rebuild_index() -> dict                 # DROP + 重建 SQLite 索引 (调用 validate_index)
    def validate_index() -> dict                # 一致性检查 (孤儿行/孤儿文件/hash 不匹配)
    def prune_index() -> dict                   # 清理孤儿 SQLite 行
```

### 2.4 数据模型

```python
@dataclass
class MemoryFrontmatter:
    id: str; created: str; source: str; confidence: str
    pinned: bool = False; tags: list[str] = []
    supersedes: list[str] = []; supersedes_reason: str | None = None
    valid_from: str | None = None; valid_until: str | None = None
    context_scope: str | None = None
    zone: str = "general"; rank: int = 0; version: int = 1

@dataclass
class LoadedMemory:
    frontmatter: MemoryFrontmatter; body: str
    source_path: Path; scope: str

@dataclass
class MemoryEffectiveness:
    loaded: int = 0; referenced: int = 0; accessed: int = 0
    last_event_at: str | None = None
    def factor(self) -> float      # 0.5 + 0.5 * (referenced/loaded)
    def decay_factor(self) -> float # Ebbinghaus: 0.5^(days/30), min 0.3
```

### 2.5 关键设计决策

| 决策 | 实现 |
|------|------|
| 原子写 | temp file → `os.replace()`，崩溃安全 |
| 索引一致性 | `_sync_from_disk()` 启动扫描 + `rglob("*.md")` 递归 |
| 线程安全 | `threading.RLock` 保护所有写操作，每线程独立 SQLite 连接 |
| tiktoken fallback | 无 tiktoken 时 `len(text.encode("utf-8")) // 3` |
| 连接健康检查 | `SELECT 1` 验证，失败则重建连接 |
| ISO 时间兼容 | `fromisoformat(x.replace("Z", "+00:00"))` 处理 Z-suffix |

---

## 3. search.py — SearchIndex (~620 行)

### 3.1 职责

- **三层检索管道**: Recall → Fusion → Rerank
- **双模式融合**: RRF (默认, 无参数) 或 Weighted (可配置超参)
- **Dual-path 冲突检测**: embedding cosine + BM25 sigmoid
- **Embedding 引擎**: ONNX Runtime (优先) → sentence-transformers (fallback)
- **CJK BM25**: IDF 加权 + effectiveness 增强 + 自适应阈值

### 3.2 检索管道

```
Layer 1: Recall (recall_k = k × 4)
  ├── Embedding: numpy @ query_vector → top-2k
  └── BM25: IDF(q)·TF·(k1+1)/(TF+k1·(1-b+b·|D|/avgdl)) → top-2k
        k1=1.5, b=0.75 (CJK 优化)

Layer 2: Fusion (默认 RRF, 可选 weighted)
  ├── RRF (Reciprocal Rank Fusion): Σ 1/(60 + rank_i) — 无参数, 学术共识
  └── Weighted: per-channel min-max normalize → α·cosine + β·bm25

Layer 3: Rerank
  ├── RRF 模式: base_score × (1+γ·recency) × (1+δ·eff) × sup_factor  +  η·hebbian_scaled
  ├── Weighted 模式: (α·cosine + β·bm25 + γ·recency + δ·eff) × sup_factor  +  η·hebbian_scaled
  └── zone filter → top-k

hebbian_scaled = hebbian_beta × activation × max_rerank_score
(HeLa-Mem §3.4 additive fusion, scaled to base-score magnitude)
```

### 3.3 公开 API

```python
class SearchIndex:
    def __init__(self, store, graph=None, cache_ttl=60)
        # graph 参数启用 Hebbian boost

    def search(query, k=5, *, zone, fusion_mode="rrf",
               alpha=0.5, beta=0.3, gamma=0.1, delta=0.1,
               hebbian_beta=0.0, include_history=False)
        -> list[LoadedMemory]
        # fusion_mode="rrf": 无参数 Reciprocal Rank Fusion (默认)
        # fusion_mode="weighted": alpha/beta/gamma/delta 超参融合

    def check_conflict(body, threshold=None, exclude_ids=None)
        -> (memory_id, score) | None

    def invalidate_cache()               # 清空结果缓存 + 重建 embedding 索引

    def _rrf_fusion(embed, bm25, active_map, k=60) -> dict  # RRF 融合
    def _weighted_fusion(embed, bm25, active_map, alpha, beta) -> dict  # 加权融合
    def _ensure_embed_index() -> bool    # 懒惰构建 numpy 矩阵
    def _embed_search(query, k) -> dict  # {memory_id: cosine_score}
    def _calc_supersedes_depth(mem_id) -> int  # 版本深度
```

### 3.4 Embedding 引擎

```
优先级: ONNX Runtime (all-MiniLM-L6-v2) > sentence-transformers > None
缓存:   functools.lru_cache(maxsize=500) 单条
存储:   numpy.ndarray (N, D) float32, 懒惰构建
融合:   numpy @ query_vector (dot product, 已归一化)
搜索:   np.argpartition(-scores, k) 取 top-k
```

### 3.5 冲突检测 — Dual Path

```
Path 1: Embedding cosine
  └── 全局 numpy 矩阵 @ query → argmax, 排除 exclude_ids (mask=-1.0)
      阈值: adaptive_conflict_threshold (0.75 CJK / 0.80 混合 / 0.85 Latin)

Path 2: BM25 sigmoid
  └── _bm25_search_scored(active, body, k=1) → normalize_bm25(raw)
      阈值: 同上
```

### 3.6 Hebbian Boost (HeLa-Mem §3.4 加法融合)

```python
# HeLa-Mem formula (4): S(v_j) = S_base(v_j) + β · Σ_{i∈N(j)} S_base(v_i) · w_ij
# Approximated via graph.spread() and scaled to max_rerank_score magnitude.
if hebbian_beta > 0 and self._graph is not None:
    activation = self._graph.spread(pool_ids, decay=0.7, max_iter=30)
    scale = max_rerank_score if max_rerank_score > 0 else 1.0
    for i, (score, mem) in enumerate(reranked):
        act = activation.get(mem.id(), 0.0)
        if act > 0:
            hebbian_score = hebbian_beta * min(act, 1.0) * scale
            reranked[i] = (score + hebbian_score, mem)

# 融合公式:
score = base_rerank_score + hebbian_beta * activation * max_rerank_score
```

### 3.7 关键设计决策

| 决策 | 实现 |
|------|------|
| BM25 保留手写 | rank_bm25 不支持 CJK bigram tokenizer |
| numpy 优先，纯 Python fallback | `_HAS_NUMPY` 标志，降级到 list + 循环 |
| 结果缓存 | `cachetools.TTLCache(maxsize=200, ttl=60s)` |
| adaptive_conflict_threshold | 统一使用 store.py 版本 (0.75/0.80/0.85) |
| exclude_ids 在 embedding 路径 | mask 为 -1.0 (低于任何有效 cosine) |
| embedding 不可用时可见性 | `logger.info` (非 debug) 供运维感知 |

---

## 4. graph.py — GraphIndex (324 行)

### 4.1 职责

- **Hebbian 共激活图**: 独立 graph.db，与 memories.db 隔离
- **Spreading Activation**: 固定点迭代 (HeLa-Mem §3.4)
- **PageRank 中心性**: 反向邻接 O(n·d) 优化
- **Ebbinghaus 衰减**: 30 天半衰期
- **跨 zone 分析**: 桥接检测 + zone 矩阵

### 4.2 SQLite Schema (graph.db)

```sql
CREATE TABLE edges (
    source_id TEXT, target_id TEXT, relation TEXT DEFAULT 'co_occurs',
    weight REAL DEFAULT 0.5, co_occurrence INTEGER DEFAULT 1,
    last_activated TEXT,
    PRIMARY KEY (source_id, target_id, relation)
);
CREATE TABLE graph_meta (
    memory_id TEXT PRIMARY KEY, access_count INTEGER DEFAULT 0,
    last_access TEXT, importance REAL DEFAULT 0.5,
    strength REAL DEFAULT 1.0, status TEXT DEFAULT 'active'
);
```

### 4.3 公开 API

```python
class GraphIndex:
    def __init__(self, db_path: Path)

    # 边管理
    def associate(memory_ids, context="") -> int   # 创建/增强 Hebbian 边
    def neighbors(memory_id, min_weight=0.1, limit=20) -> list[dict]

    # 图算法
    def spread(seed_ids, decay=0.7, max_iter=50) -> dict[str, float]
        # 固定点扩散激活: act_new = propagation(act, decay, edge_weight)
        # 剪枝: act < 0.01, 收敛: delta < 1e-4
    def pagerank(damping=0.85, max_iter=50, tol=1e-6) -> dict[str, float]
        # O(n·d): 预建 rev_adj[node] = [(src, weight, total_out)]

    # 维护
    def decay()                                    # Ebbinghaus 衰减, 权重 < 0.05 删边
    def stats() -> dict                            # {nodes, edges, avg_weight}
    def cross_zone(store) -> dict                  # zone 矩阵 + 桥接检测
    def close()                                    # WAL checkpoint + 关闭连接
```

### 4.4 Spreading Activation 算法

```
输入: seed_ids, decay=0.7, max_iter=50
输出: {node_id: activation_score}

1. activation[seed_id] = 1.0
2. for _ in range(max_iter):
     new_act = {}
     for nid, act in activation:
       if act < 0.01: continue          # 剪枝
       for neighbor in graph[nid]:
         propagated = act × decay × edge_weight
         new_act[neighbor] = max(existing, propagated)
     activation.update(new_act)
     if sum(new_act.values()) < 1e-4:   # 收敛
       break
3. return activation
```

### 4.5 关键设计决策

| 决策 | 实现 |
|------|------|
| 独立 graph.db | 与 memories.db 隔离，可独立备份/重建 |
| thread-local 连接 | 每线程独立 `sqlite3.connect`，无锁读 |
| RLock 写保护 | `associate()` 和 `decay()` 串行化 |
| PageRank 反向邻接 | 预建 `rev_adj`，内层 O(d) 而非 O(n·d) |
| 旧 4 层抽象删除 | Protocol → Store → Engine → Router → Manager 全部坍缩 |

---

## 5. reflect.py — ReflectionEngine (403 行)

### 5.1 职责

- **raw_chunk 模式** (默认): 零 LLM 调用，保存对话片段
- **heuristic 模式**: 规则提取事实 (显式记忆意图/纠正/偏好)
- **full LLM 模式**: 结构化 JSON 输出 (可选，昂贵)
- **内容门控**: `_is_memorable_content` 防工具输出注入

### 5.2 公开 API

```python
class ReflectionEngine:
    def __init__(self, store, search, graph)   # 构造函数依赖注入
    def micro(ctx, user_msg, assistant_msg) -> dict | None
    def full(ctx, messages) -> dict
    def audit(candidate, decision, reason) -> dict
    def log(entry)                              # → reflect-log.jsonl
    def recent(n=10) -> list[dict]              # ← reflect-log.jsonl
```

### 5.3 反射模式

```
mode = os.environ.get("MEM_REFLECTION_MODE", "raw_chunk")

raw_chunk (默认):
  micro → 保存 user_msg + assistant_msg 作为 episode 记忆
  full  → 遍历所有对话轮次，保存每个非工具消息
  学术验证: hybrid 检索达 81.1% (Retrieval Bottleneck)

heuristic:
  micro → _extract_facts_from_turn (显式记忆意图/纠正/偏好)
  full  → raw_chunk fallback

full (LLM):
  micro → heuristic fallback
  full  → ctx.llm.complete_structured → 解析 JSON → 写入记忆
  失败 → raw_chunk fallback
```

### 5.4 内容门控 `_is_memorable_content`

```
拒绝规则:
  - 空文本 / 非字符串
  - 长度 < 15 字符
  - 包含工具输出标记 (```, Exit code, stdout, stderr, etc.)
  - 文件路径模式 (/path/to/file.ext, C:\path\to\file)
  - 代码模式 (def, class, import, function, const)
  - 重复文本 (前 50 字符中唯一字符 < 5)
```

### 5.5 关键设计决策

| 决策 | 实现 |
|------|------|
| 依赖注入 | `__init__(store, search, graph)` — 无 late_binding |
| raw_chunk 默认 | 零 LLM 调用, 依赖检索层质量 |
| JSON 解析 | `json.JSONDecoder.raw_decode` 替代贪婪正则 |
| 反射日志 | JSONL 追加 + `_read_reflect_log` 逆序读取 |
| 线程安全 | `threading.Lock` 保护日志写入 |

---

## 6. context.py — 上下文装配 (145 行)

### 6.1 职责

- 单一 Palace 模式，4 层优先级
- Skill 匹配 (token overlap)

### 6.2 公开 API

```python
def build_context(store, search, skills, query="", max_tokens=4000) -> str
```

### 6.3 上下文装配优先级

```
1. Pinned Memories  (store.list_pinned())
     ↓  token 预算剩余?
2. Relevant Memories (search.search(query, k=10) 或 store.list_active()[:10])
     ↓  token 预算剩余?
3. Triggered Skills  (_match_triggered_skills — token overlap 匹配)
     ↓  token 预算剩余?
4. Always-Active Skills (skills.list() → frontmatter.always_active)
```

### 6.4 Skill 匹配算法

```
_match_triggered_skills(skills, query, cap=3):
  q_tokens = set(_tokenise(query))
  for skill in skills.list():
    skill_tokens = _tokenise(name + description + triggers)
    overlap = len(q_tokens & skill_tokens)
    score = overlap / max(len(q_tokens), len(skill_tokens))
  return top-3 by score
```

### 6.5 Token 感知截断

`_format_memory` 使用 `estimate_tokens` 按 ~100 tokens 截断正文，替代硬编码 200 字符。
CJK 文本获得公平的上下文预算（每 token ~1.5 字符 vs Latin ~4 字符）。

---

## 7. __init__.py — 插件入口 + 旧模块兼容 (1959 行)

### 7.1 当前状态

beta2 模块作为**并行实现**运行，旧模块保持全量加载：

```
__init__.py
  ├── 旧模块导入 (core.py, search/embed.py, reflection/engine.py, ...)
  ├── 旧 MemoryStore + SkillStore + 工具注册
  ├── 旧 graph manager + slash 命令
  │
  └── runtime services:
        ├── _get_indexed_mem_store() → store.MemoryStore
        ├── _get_search_index()      → search.SearchIndex(graph=graph_index)
        ├── _get_graph_index()       → graph.GraphIndex
        ├── _get_reflection_engine() → reflect.ReflectionEngine
        └── _get_memory_context()    → context.build_context
```

### 7.2 runtime 单例创建链

```
_get_indexed_mem_store()
  └── store.MemoryStore(user_root, project_root)
      └── set_graph(graph_index)

_get_graph_index()
  └── graph.GraphIndex(plugin_data_dir() / "graph.db")

_get_search_index()
  └── search.SearchIndex(mem_store, graph=graph_index)  ← 修复 #4 接线

_get_reflection_engine()
  └── reflect.ReflectionEngine(mem_store, search_index, graph_index)
```

### 7.3 推至 beta3

旧模块 (`core.py`, `late_binding.py`, `search/embed.py`, `reflection/engine.py`, `graph/*`, `hooks/lifecycle.py`, `tools/handlers.py`, `query/cache.py`) 推至 beta3 一次性替换。

---

## 8. 数据流: 一次完整的记忆写入

```
Tool handler → MemoryStore.put(scope, fm, body)
  │
  ├── [store]   _root_for(scope) → 确定文件路径
  ├── [store]   write_memory_atomic(path, fm, body)
  │               ├── yaml.dump(fm.to_dict())
  │               ├── tmp.write_text(content)
  │               └── os.replace(tmp, path)          ← 原子替换
  │
  ├── [store]   _upsert_memory_row(conn, m)
  │               ├── INSERT OR REPLACE INTO memories
  │               ├── DELETE + INSERT tags
  │               └── DELETE + INSERT supersedes
  │
  ├── [store]   conn.commit()
  │
  ├── [search]  SearchIndex.invalidate_cache()
  │               ├── TTLCache.clear()
  │               └── _embed_array = None            ← 懒惰重建
  │
  └── [graph]   GraphIndex.associate(related_ids)
                  ├── INSERT OR IGNORE graph_meta
                  └── INSERT/UPDATE edges (双向对称)
```

全部同步执行，RLock 保护。无异步队列，无 late_binding。

---

## 9. 数据流: 一次完整的检索

```
context.build_context(store, search, skills, query)
  │
  └── search.search(query, k=5, hebbian_beta=0.15)
        │
        ├── [store] list_active()         → 所有非 superseded 记忆
        │
        ├── Recall ─────────────────────────────────────
        │   ├── _embed_search(query, 20)
        │   │     ├── _ensure_embed_index() → numpy 矩阵
        │   │     └── scores = embed_array @ qvec  → top-20
        │   └── _bm25_search_scored(active, query, 20)
        │         ├── _tokenise(query) → CJK bigrams
        │         └── IDF·TF 公式  → top-20 scored
        │
        ├── Fusion ─────────────────────────────────────
        │   ├── pool = union(embed_results, bm25_results)
        │   └── per-channel min-max normalize
        │
        ├── Rerank ─────────────────────────────────────
        │   ├── [store] effectiveness()     → eff factor
        │   ├── [store] latest_for()        → supersedes depth
        │   ├── [graph] spread(pool_ids)    → Hebbian activation
        │   └── score = base_rerank + η·hebbian_scaled
        │              (base_rerank already includes recency, eff, sup_factor)
        │
        └── zone filter → top-k
```

---

## 10. Supersedes 链 + 图记忆宫殿 的简化对照

| 维度 | 旧架构 (v1.0-beta1) | 新架构 (v1.0-beta2) |
|------|---------------------|---------------------|
| Supersedes 存储 | YAML frontmatter `supersedes` + graph 特殊边 (双份) | SQLite `supersedes` 表 (单一来源) |
| 版本链查询 | 文件系统遍历 + 手动去重 | `is_superseded()` / `latest_for()` / `lineage_chain()` — 3 个 SQL 方法 |
| 跨层统一查询 | CLUQI (~296 行) 手动 JOIN MemoryStore + GraphStore + supersedes | **不存在** — store 管数据+版本，graph 管共激活关系，search 在检索时组合 |
| Zone 管理 | Palace 独立缓存 + split/merge 阈值 + palace index 文件 | `memories.zone` 列 + `normalize_zone()` + `GROUP BY zone` |
| 图抽象层数 | 4 层 (Protocol → Store → Engine → Router → Manager) | 1 层 (`GraphIndex`) |
| 图是否拥有节点生命周期 | 是 (GraphMemoryManager 创建/删除节点) | 否 — 只存 edges + graph_meta，memory 生命周期由 MemoryStore 管理 |
| 检索如何结合两者 | `fusion_search()` 手动拼接 + Effectiveness | `SearchIndex.search()` 统一管道: sup_factor × (fusion + hebbian) |
| 代码行数 | ~7,200 (13 模块) | ~3,200 (6 模块) |

### 架构决策核心

```
store  = 数据 + 版本真相 (单一来源)
graph  = 共激活关系 (无记忆内容感知)
search = 检索时组合两者 (管道)
```

Supersedes 不再需要在 graph 中镜像一份，zone 不再需要独立索引，CLUQI 不再需要存在。

---

## 11. 代码审查修复清单 (2026-06-03)

| # | 级别 | 问题 | 文件 | 修复 |
|---|------|------|------|------|
| B1 | HIGH | adaptive_conflict_threshold 重复定义 (0.55 vs 0.75) | search.py | 删除本地定义, 统一 import store.py 版本 |
| B2 | HIGH | check_conflict embedding 路径忽略 exclude_ids | search.py | exclude_ids mask=-1.0 (numpy) / continue (纯 Python) |
| B3 | HIGH | functools import 顺序错误 | search.py | 移至文件顶部, 删除冗余 import |
| B4 | HIGH | Hebbian boost stub 未接入 | search.py + __init__.py | `graph.spread()` 接入融合公式, 构造函数注入 |
| B5 | MEDIUM | _sync_from_disk 非递归扫描 | store.py | `iterdir()` → `rglob("*.md")` |
| Q1 | MEDIUM | pagerank O(n²·d) | graph.py | 预建反向邻接表 → O(n·d) |
| Q2 | MEDIUM | decay_factor ISO Z-suffix 不兼容 | store.py | `replace("Z", "+00:00")` |
| Q3 | LOW | embedding 不可用静默 | search.py | `logger.debug` → `logger.info` |
| Q4 | — | 旧模块清理 | — | 推至 beta3 |
| E1 | MEDIUM | 缺失 SkillStore 类 (ImportError) | store.py | 添加完整 SkillStore + _read_skill_file + license 字段 |
| E2 | MEDIUM | context _format_memory 硬编码 200 字符截断 | context.py | token 感知截断 (~100 tokens, CJK 公平) |
| E3 | MEDIUM | MemoryStore 缺少可处置索引工具 | store.py | 添加 rebuild_index/validate_index/prune_index |
| E4 | LOW | graph.py stats() UNION 节点计数语义错误 | graph.py | UNION → 子查询 COUNT(DISTINCT ... UNION ...) |
| E5 | MEDIUM | 融合层 5 超参手调 vs RRF 无参学术共识 | search.py | RRF 为默认 (fusion_mode="rrf"), weighted 保留为选项 |
| A1 | HIGH | 手写 BM25 纯 Python O(n·m) 无索引 | search.py | 引入 `bm25s` (numpy+scipy sparse), 10-50x 加速 |
| A2 | HIGH | health_metrics Jaccard 近似 O(30n) bounded | store.py | 引入 `datasketch.MinHashLSH`, O(n) 精确近似 |

---

## 12. RRF 融合设计细节

### 12.1 公式

```
RRF(记忆 m) = Σ_{channel c} 1 / (k + rank_c(m))

k = 60 (Cormack et al., 2009 学术共识常数)
```

### 12.2 与 Weighted 融合对比

| 维度 | RRF (默认) | Weighted (可选) |
|------|-----------|----------------|
| 超参数数量 | 0 | 4 (α, β, γ, δ) |
| 归一化需求 | 无 (rank 自带归一化) | per-channel min-max |
| 学术验证 | IR 领域广泛验证 | 项目特定调参 |
| CJK 公平性 | rank 比较无语言偏差 | 需手动调 α/β |
| Hebbian 集成 | 乘法 bonus post-rerank | 加法项 post-rerank (HeLa-Mem §3.4) |

### 12.3 代码路径

```
search(query, fusion_mode="rrf")        ← 默认, 零配置
  ├── Recall (同 weighted)
  ├── _rrf_fusion(embed, bm25)          ← 仅用 rank 信息
  ├── Rerank: base_score × (1+γ·recency) × (1+δ·eff) × sup_factor + η·hebbian_scaled
  └── zone filter → top-k

search(query, fusion_mode="weighted")   ← 向后兼容
  ├── Recall (同上)
  ├── _weighted_fusion(embed, bm25)     ← min-max norm + α·cos + β·bm25
  ├── Rerank: (base + γ·recency + δ·eff) × sup_factor + η·hebbian_scaled
  └── zone filter → top-k
```

---
