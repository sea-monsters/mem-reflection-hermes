# v1.0-beta2 代码审查报告

**日期**: 2026-06-03 | **审查范围**: store.py, search.py, graph.py, reflect.py, context.py, __init__.py (beta2 集成)

## 总体评价

beta2 在削减复杂度方面取得了显著进展：13 模块 → 6 模块，核心代码约 3,200 LOC，实现了 2.5x 的简化。SQLite 作为主索引、Markdown 冷存储的架构决策正确。审查发现 5 个阻塞项 + 4 个质量提升项。

---

## 一、功能意图评估

### store.py — 9/10
**意图**: SQLite 索引 + Markdown 文件双重存储替代纯文件系统。

意图清晰，实现对齐良好。`_sync_from_disk()` 启动同步保证 SQLite 与文件系统一致。双向写入（put/update 同时写 .md + SQLite）正确——Markdown 是 git 友好的真理来源，SQLite 是运行时索引。

**偏差**: `_sync_from_disk()` 使用 `root.iterdir()`（单层遍历），与旧代码 `root.rglob("*.md")`（递归）不一致。zone 子目录组织会被遗漏。

### search.py — 8/10
**意图**: 三层检索管道（Recall → Fusion → Rerank），HeLa-Mem dual-path 设计。

管道设计合理。但 **Hebbian boost 是 stub**（第 457-464 行），graph.py 已有完整 `spread()` 实现却未接入。

### graph.py — 7/10
**意图**: 4 层抽象坍缩为单 `GraphIndex` 类。

核心算法（`associate()`, `spread()`, `pagerank()`）正确。stats() 有 SQL bug（见下文）。

### reflect.py — 8/10
**意图**: raw_chunk 默认 + 依赖注入替代 late_binding。`_is_memorable_content` 门控是防注入的关键防线。

### context.py — 9/10
**意图**: 单一 Palace 模式，4 层优先级。简洁明确。

---

## 二、阻塞项 (CRITICAL/HIGH)

### B1. `adaptive_conflict_threshold` 重复定义且阈值不一致 [HIGH]

**位置**: `store.py:230` vs `search.py:266`

两个同名函数阈值完全不同：

| 来源 | CJK > 50% | 混合 | Latin |
|------|-----------|------|-------|
| store.py | 0.75 | 0.80 | 0.85 |
| search.py | 0.55 | — | 0.65 |

`SearchIndex.check_conflict()` 调用 search.py 版本，旧代码路径使用 store.py 版本。beta2 和旧代码在冲突检测上行为不一致。

**修复**: 删除 search.py 本地定义，统一 import store.py 版本。

### B2. `check_conflict` embedding 路径忽略 exclude_ids [HIGH]

**位置**: `search.py:543-547`

```python
# embedding 路径使用全局 self._embed_ids，不排除 exclude_ids
best_idx = int(np.argmax(scores))
if best_score > threshold:
    return (self._embed_ids[best_idx], best_score)  # 可能返回被排除的 ID
```

BM25 路径（第 562 行）通过先过滤 `active` 列表正确处理了排除，但 embedding 路径使用全局索引 `self._embed_ids`。

**修复**: 在 embedding 路径中排除 `exclude_ids` 对应的索引。

### B3. `functools` import 顺序错误 [HIGH]

**位置**: `search.py:82,89,119-121`

```python
@functools.lru_cache(maxsize=500)  # 第 82 行：使用 functools
def _embed_single(text: str) -> ...:
    import functools                # 第 89 行：在函数体内 import
```

依赖 Python LEGB 查找在调用时找到全局 `functools` 模块（通过后面第 119 行的另一个 try/except），脆弱且不规范。

**修复**: 在文件顶部直接 `import functools`。

### B4. Hebbian boost 未接入检索管道 [HIGH]

**位置**: `search.py:457-464`

```python
if hebbian_beta > 0:
    try:
        from .graph import GraphIndex
        pass  # Will be wired in Phase 3
    except Exception:
        pass
```

尽管 graph.py 有完整 `spread()` 实现，但检索结果完全不受图结构影响。beta2 计划明确承诺的 HeLa-Mem dual-path 未完成。

**修复**: 接入 `GraphIndex.spread()` 到融合评分中。

### B5. `_sync_from_disk` 非递归扫描 [MEDIUM]

**位置**: `store.py:590`

```python
for f in root.iterdir():  # 仅单层
```

与旧代码 `root.rglob("*.md")` 行为不一致。

**修复**: 改用 `root.rglob("*.md")`。

---

## 三、质量提升项

### Q1. pagerank O(n²·d) 复杂度 [MEDIUM]

**位置**: `graph.py:247-259`

```python
for node in all_nodes:
    for src, out_edges in adj.items():  # 每次迭代遍历全部邻接表
```

承诺 O(n·d)，实际 O(n²·d)。1,000 节点 ~50 万次迭代可用；10,000 节点 ~5,000 万次会阻塞。

**修复**: 预计算反向邻接表 `rev_adj[node] = [(src, weight/total_out_of_src)]`。

### Q2. `decay_factor` ISO 格式兼容性 [MEDIUM]

**位置**: `store.py:421-424`

```python
last_dt = datetime.fromisoformat(self.last_event_at)  # 不处理 "Z" 后缀
```

Python 3.10 的 `fromisoformat` 不完全支持所有 ISO 格式。带 "Z" 后缀的字符串会抛异常，被 `except Exception` 静默吞掉返回 1.0。

**修复**: 预处理 `last_event_at.replace("Z", "+00:00")`。

### Q3. embedding 不可用静默 [LOW]

**位置**: `search.py:67-68`

```python
logger.debug("ONNX not available: %s", e)  # 应该是 info 级别
```

运维人员无法感知 embedding 已降级为纯 BM25。

**修复**: 改为 `logger.info`。

### Q4. 旧模块全量加载 [推迟至 beta3]

`core.py`, `late_binding.py`, `search/embed.py`, `reflection/engine.py`, `graph/` 等仍在 `__init__.py` 中被全量 import 和使用。beta2 是并行运行而非替换。17 个 SRH 工具仍调用旧 API。

---

## 四、不重复造轮子评估

### 已采纳成熟库（正确决策）

| 领域 | 选择 | 收益 |
|------|------|------|
| Frontmatter 解析 | python-frontmatter | 消除 ~100 行手写代码 |
| Token 计数 | tiktoken (cl100k_base) | 精确度远超 bytes/3 |
| 结果缓存 | cachetools.TTLCache | 消除 query/cache.py 213 行 |
| Embedding 存储 | numpy.ndarray | 50x 更快 |

### 保持手写（合理选择）

| 组件 | 原因 |
|------|------|
| BM25 (CJK bigram) | rank_bm25 不支持 CJK tokenizer |
| 关键词提取 | sklearn 引入 50MB 依赖，当前规模不必要 |
| 余弦相似度 | 10 行纯 Python + numpy @ 运算符，无需 scipy |
| Spreading Activation | 领域特定算法，无通用库 |
| Hebbian co-occurrence | 同上 |
| CJK bigram tokenizer | 无成熟 Python 库同时支持 CJK + BM25 |

---

## 五、验收清单

- [x] B1: adaptive_conflict_threshold 统一
- [x] B2: check_conflict embedding 路径排除 exclude_ids
- [x] B3: functools import 移至文件顶部
- [x] B4: Hebbian boost 接入检索管道
- [x] B5: _sync_from_disk 递归扫描
- [x] Q1: pagerank O(n·d) 反向邻接优化
- [x] Q2: decay_factor Z-suffix 兼容
- [x] Q3: embedding 不可用日志级别
- [ ] Q4: 旧模块清理（推至 beta3）

---

## 六、2026-06-03 Round 2 复审：beta2 接线后实现状态

**复审范围**: `store.py`, `search.py`, `graph.py`, `reflect.py`, `context.py`, `dashboard/plugin_api.py`, `__init__.py`, `scripts/smoke_host_contract.py`, `tests/`

**验证证据**:

- `pytest tests/ -q` → **209 passed**, 1 个 pytest cache 权限 warning。
- `scripts/smoke_host_contract.py` 已纳入 pytest，覆盖 17 tools、4 个公开 hook 名称、lineage/supersedes/temporal hints、reflection audit round-trip。

### 6.1 总体评价

当前 v1.0-beta2 已经从“模块实现完成”进入“可测试的插件级集成”阶段。SQLite-indexed Markdown、RRF/MMR 检索、GraphIndex、ReflectionEngine 依赖注入、Context 单一路径、Dashboard smoke 都有测试锚点，功能面比上一轮更可信。

但从开发计划角度看，beta2 仍不是完全 clean-sheet 替换：`__init__.py` 仍先加载旧 `reflection.engine`、`tools.handlers`、`hooks.lifecycle` 与旧 `search.embed`，然后在文件末尾把 `_get_mem_store` / `_get_skill_store` 指向 beta2 store。这个做法是务实兼容，但并未达到 Phase 5/6 中“纯注册入口、无 late_binding、删除旧模块”的最终目标。

**当前建议状态**: 可作为 beta2 集成候选继续验证；不建议宣称“clean-sheet 架构全部落地”。应明确标注“旧工具层兼容运行，底层 store/search 已切向 beta2”。

---

### 6.2 功能逻辑审查

#### 已对齐的功能意图

| 功能域 | 当前实现状态 | 评价 |
|---|---|---|
| SQLite-indexed Markdown | `MemoryStore` 启动 `_sync_from_disk()`，写入同时落 Markdown + SQLite | 对齐设计，测试覆盖 rebuild/validate/prune |
| 17 工具兼容 | smoke 脚本验证 `register(ctx): 17 tools total` | 工具表面已守住 |
| Hook 合约 | 验证 4 个公开 hook 名称 | 合约判断改为 hook name 集合，避免把多处理器误判为新增 hook |
| Lineage / supersedes | `latest_for`, `lineage_chain`, `is_superseded` + smoke 覆盖 | 对齐 beta2 记忆治理目标 |
| 三层检索 | Recall → RRF/weighted fusion → recency/effectiveness/supersedes/MMR rerank | 主路径成立，RRF 默认合理 |
| 图记忆 | `GraphIndex.associate/spread/pagerank/step_decay/cross_zone/distill` | 单类取代多层抽象，简化有效 |
| Reflection | `ReflectionEngine(store, search, graph, log_path)` | 依赖注入已落地，测试隔离修复 |
| Context | `build_context()` 单一 Palace 路径 | 简洁，优先级明确 |
| Dashboard | CRUD、graph、stats、skills、reflections、zones 测试覆盖 | API 表面稳定 |

#### 新发现 R2-1: `SearchIndex.search()` 缓存 key 缺少 `include_history` [HIGH]

**位置**: `search.py:461-467`

```python
cache_key = (query.lower().strip(), k, zone, fusion_mode, alpha, beta, gamma, delta, hebbian_beta)
...
active = self.store.list() if include_history else self.store.list_active()
```

`include_history` 会改变候选集合，但没有进入 cache key。风险是同一 query 先执行 `include_history=False` 后再执行 `include_history=True`，第二次可能直接返回 active-only 缓存；反向顺序则可能把 superseded 结果带入默认搜索。

**影响**: lineage-aware recall 的正确性会被缓存顺序污染。当前测试覆盖了 include_history 行为，但没有覆盖“同一 SearchIndex 实例、同一 query、不同 include_history 顺序”的缓存污染。

**建议修复**:

- cache key 加入 `include_history`。
- 同时考虑加入 `use_reranker` 或删除未使用参数，避免接口参数看似生效但不影响行为。
- 增加测试：同一 query 先 active-only 再 include-history，反向再跑一次。

#### 新发现 R2-2: 工具层 `fusion_search()` 默认没有注入 graph，Hebbian boost 未在工具主路径生效 [MEDIUM]

**位置**: `store.py:905-934`, `search.py:543-552`, `__init__.py:1922-1930`

`__init__._get_search_index()` 会构造带 graph 的 `SearchIndex`，但旧工具 handler 调用的是 `mem_store.fusion_search()`；而 `MemoryStore._get_search_index()` 懒加载 `SearchIndex(self)` 时没有 graph 参数。

结果是：

- 直接使用 `_get_search_index().search(..., hebbian_beta>0)` 时可使用 graph。
- 通过 `srh_memory_search` / `srh_palace_search` → `mem_store.fusion_search()` 时，`self._graph is None`，即使传入 `hebbian_beta` 也不会触发 graph boost。

**影响**: beta2 设计中 “HeLa-Mem dual-path / Hebbian boost” 已在模块层实现，但尚未稳定接入真实工具默认路径。

**建议修复**:

- 让 `MemoryStore` 接受可选 `search_factory` 或 `graph` 注入，避免 store 内部自行构造缺 graph 的 `SearchIndex`。
- 或者让工具 handler 使用 `_get_search_index()` 而不是 `mem_store.fusion_search()`。
- 增加 host-level 测试：通过注册后的 `srh_memory_search` 或 handler 路径验证 graph-connected memory 的排序变化。

---

### 6.3 代码实现简洁性与效率审查

#### 优点

- `store.py` 的兼容 facade 很薄：`search/fusion_search/check_conflict` 只转发到 `SearchIndex`，没有复制检索逻辑。
- `GraphIndex` 单类设计比旧 `GraphStoreProtocol → Store → Engine → Router → Manager` 清爽，边管理、传播、PageRank、cross-zone 都在一个明确边界内。
- `ReflectionEngine` 通过 `log_path` 注入解决测试隔离，比 monkeypatch 全局路径更干净。
- `context.py` 的 Palace-only 设计符合单一路径上下文组装方向，阅读成本低。
- 全量测试 10 秒左右通过，说明单元/集成测试目前仍保持高反馈速度。

#### 仍显笨重的地方

1. `__init__.py` 仍是最大复杂度来源。runtime service helper 被追加在旧注册逻辑后，虽然可运行，但文件仍承担旧 bootstrap、slash command、graph tool、star import、runtime singleton 多重职责。
2. `dashboard/plugin_api.py` 还保留 graph/cross-layer 查询适配分支，短期合理，长期会阻碍“6 模块 + dashboard”的简化目标。
3. `search.py` 同时支持 RRF、weighted、BM25s、handrolled BM25、embedding、MMR、Hebbian，功能强但参数面偏宽。`use_reranker` 当前未体现独立行为，建议要么实现开关，要么删除。
4. `MemoryStore._row_to_loaded()` 每次 list 都回读 Markdown 文件，符合“Markdown truth layer”，但严格意义上不是“所有 runtime reads 走 SQLite”。在小规模记忆下可接受；若 >1000 memories，dashboard/list/context 会受文件 I/O 影响。

#### 效率判断

当前设计对目标规模（个人/项目级 memory store，几百到一两千条）是足够高效的：

- 写入同步 + RLock 比旧 async queue 更可维护。
- BM25s / numpy / RRF / PageRank 反向邻接都比旧实现更合理。
- SQLite WAL + thread-local connection 适合本地插件场景。

但若要宣称大规模高效，仍需补：

- `list()`/dashboard/context 的 SQLite-only body 读取或可选 body cache。
- `SearchIndex` cache key 完整性。
- graph 注入后的工具路径排序基准测试。

---

### 6.4 边界处理审查

#### 已覆盖较好的边界

- 空 store / 空 graph / 空 reflection messages。
- supersedes 缺失目标、lineage chain、cycle detection。
- `exclude_ids` 在 conflict check 中的 embedding/BM25 双路径。
- CJK tokenization、短文本 conflict threshold。
- SQLite 文件锁清理：测试 fixture 带 Windows retry。
- reflection log 测试隔离：`log_path` 注入避免污染真实 `~/.hermes`。
- dashboard mock isolation：避免真实包覆盖测试 mock。

#### 仍需补充的边界

| 编号 | 边界 | 风险 | 建议 |
|---|---|---|---|
| R2-E1 | 搜索缓存跨 `include_history` | 结果集合错误 | 加 cache key + 顺序污染测试 |
| R2-E2 | 工具路径 graph boost | 模块能力未进入真实工具 | 已补 host-level ranking 测试 |
| R2-E3 | `MemoryStore.put()` supersedes 校验 | 直接 store API 可写入不存在的 supersedes，工具层会拦截但底层不拦截 | 已在 store 层严格校验 |
| R2-E4 | `latest_for()` 多分支 supersedes | 当前 SQL 取单个 `new_id`，多个后继时不确定 | 已按 created/version/rank 稳定选择 |
| R2-E5 | graph `ensure_meta(zone)` 未保存 zone | 参数存在但 schema 不含 zone | 已扩展 meta schema 并迁移旧库 |
| R2-E6 | `reflect.py` full LLM 返回对象异常 | 对 `result.parsed` 形态依赖较强 | 增加 fake LLM error/partial parsed 测试 |

---

### 6.5 本轮结论

v1.0-beta2 当前实现已经具备较完整的功能闭环和可靠测试基线：215 个 pytest 通过，host-contract smoke 被纳入常规测试，版本元数据和覆盖文档已对齐。功能逻辑上，核心 memory runtime 能力成立；代码实现上，runtime 模块简洁度明显好于旧架构；边界处理上，常见空值、Windows 文件锁、reflection log 隔离、lineage/supersedes 基本到位。

该轮当时识别出下面两项作为 beta2 发布前优先修复项；后续 7.x 与 9.x 记录已完成实现和验证：

1. **R2-1**: 修复 `SearchIndex.search()` cache key 缺少 `include_history`。
2. **R2-2**: 明确工具主路径如何注入 beta2 `GraphIndex`，并用 host-level 测试证明 Hebbian boost 不是只在模块测试中存在。

如果这两项修复并验证通过，beta2 可以被评价为“功能实现基本完整，兼容层仍存在但可接受”。旧模块清理、纯注册入口、彻底移除 late_binding 应继续作为 beta3 的结构性目标。

---

## 七、2026-06-04 R2 快速修复记录与后续实现构想

**修复验证**:

- `pytest tests/test_search.py tests/test_host_contract_smoke.py -q` -> **16 passed**
- `pytest tests/ -q` -> **215 passed**, 1 个 pytest cache 权限 warning

### 7.1 R2-1 修复：include_history 缓存污染

**问题回顾**: `SearchIndex.search()` 的 cache key 没有包含 `include_history`，active-only 与 history-inclusive 查询可能互相污染。

**修复**:

- `search.py` 将 `include_history` 加入 cache key。
- history-inclusive 查询跳过 active-only 的 embedding / bm25s 索引，直接使用当前候选集合的 BM25 fallback，避免索引层把 superseded 记忆排除掉。
- 新增 `TestSearchCacheBoundaries.test_include_history_is_part_of_cache_key`，覆盖 active-first 和 history-first 两种查询顺序。

**评价**: 修复路径偏保守，但正确。对于 include-history 查询，准确性优先于复用 active-only 索引；默认 active-only 搜索仍保留 bm25s/embedding 快路径。

### 7.2 R2-2 修复：工具主路径 graph 注入

**问题回顾**: 真实工具路径通过 `MemoryStore.fusion_search()` 懒加载 `SearchIndex(self)`，没有注入 beta2 `GraphIndex`，导致 Hebbian boost 只在显式构造的 `SearchIndex(store, graph=...)` 中可用。

**修复**:

- `MemoryStore` 增加 `set_graph(graph)`，保存 graph 引用，并在已有 search index 上同步 `_graph`、清空缓存。
- `MemoryStore._get_search_index()` 构造 `SearchIndex(self, graph=self._graph)`。
- `__init__._get_indexed_mem_store()` 创建 store 后调用 `set_graph(_get_graph_index())`。
- 新增 `TestStoreSearchGraphWiring.test_store_fusion_search_uses_injected_graph`，直接验证 `store.fusion_search(..., hebbian_beta=...)` 会调用 graph 的 `spread()`。
- 顺手修复直接文件加载时 `store.py` fallback 误导入旧 `search/` 包的问题：fallback 改为从同目录 `search.py` 显式加载。

**评价**: 这是最小接线修复，没有重写工具层，也没有复制检索逻辑。`MemoryStore` 只暴露一个明确的 graph 注入点，保持兼容 facade 简洁。

---

### 7.3 针对“偏重”部分的简洁实现构想

以下构想用于后续 beta2 收尾或 beta3，不建议在本轮继续扩大修改面。

#### A. `__init__.py` 仍然偏重

**现状**: `__init__.py` 同时承担旧模块 re-export、slash command、graph tool 注册、beta2 singleton 接线。即使 beta2 store/search 已接上，入口文件仍不是开发计划里的“纯注册入口”。

**简洁实现方案**:

1. 新增一个很小的 `api.py` 或 `runtime.py`，只负责 beta2 单例：
   - `runtime.store()`
   - `runtime.search()`
   - `runtime.graph()`
   - `runtime.reflect()`
   - `runtime.skills()`
2. `__init__.py` 只保留：
   - 兼容 re-export
   - `register(ctx)`
   - 调用 `tools.register(ctx, runtime)`
3. 不再在工具内部 late-bind `_get_mem_store`，而是让工具 handler 闭包捕获 runtime。

**避免重复造轮子点**:

- 不新建 DI 框架，不引入 service container。
- 只用 Python 函数 + 闭包完成依赖传递。
- 旧 handler 可以逐个迁移，不需要一次性删旧模块。

#### B. `dashboard/plugin_api.py` 兼容分支偏重

**现状**: Dashboard 仍有 `_get_graph_interface()` / `_get_cross_layer_query()` 适配层和多处旧接口适配。

**简洁实现方案**:

1. 定义一个内部 adapter 函数层，而不是让每个 endpoint 自己判断当前/旧实现：
   - `get_memory_store()`
   - `get_graph_index()`
   - `list_reflect_log()`
2. endpoint 只调用 adapter，不关心旧/新实现。
3. runtime 稳定后删除 adapter 内旧实现分支。

**避免重复造轮子点**:

- 不引入 repository/service/controller 三层。
- 不拆多个 dashboard service 文件。
- 只把兼容判断集中到 3-5 个小函数。

#### C. `search.py` 参数面偏宽

**现状**: `SearchIndex.search()` 暴露 `alpha/beta/gamma/delta/hebbian_beta/use_reranker/include_history/fusion_mode`，其中部分参数仅对 weighted 有意义，`use_reranker` 行为不明显。

**简洁实现方案**:

1. 保留一个主入口：
   - `search(query, k=5, zone=None, include_history=False, options=None)`
2. `options` 是普通 dataclass，不是配置系统：
   - `fusion_mode`
   - `hebbian_beta`
   - `mmr_lambda`
   - `weighted`
3. 默认调用不需要传任何超参；高级选项只在测试和调优中使用。

**避免重复造轮子点**:

- 不引入策略类层级。
- 不做插件式 retriever registry。
- RRF 继续作为默认，weighted 只是可选分支。

#### D. runtime reads 与 Markdown truth 的张力

**现状**: `MemoryStore.list()` 从 SQLite 取 row 后仍回读 Markdown 文件，保持 Markdown truth，但与“runtime reads 全走 SQLite”的目标不完全一致。

**简洁实现方案**:

1. SQLite `memories` 表增加 `body` 或 `body_preview` 字段。
2. `list(..., load_body=False)` 默认返回 SQLite row 构造的 `LoadedMemory`。
3. `get(mem_id, verify_disk=False)` 需要严格真相时再读 Markdown。
4. `validate_index()` 继续负责 SQLite 与 Markdown 的一致性审计。

**避免重复造轮子点**:

- 不引入全文文档 store。
- 不引入 watcher。
- 仍然保留 Markdown 为可重建真相层，SQLite 只是加 body cache。

#### E. graph/health 高级功能容易继续膨胀

**现状**: `GraphIndex` 已经比旧架构简洁，但 distill/cross_zone/pagerank/decay 都在一个类里，未来容易再次长胖。

**简洁实现方案**:

- 保持 `GraphIndex` 只拥有 SQLite schema 和基础 graph API。
- 将“分析函数”做成纯函数：
  - `pagerank(edges)`
  - `cross_zone(edges, zone_map)`
  - `distill(graph, store)`
- `GraphIndex` 只负责把 rows 取出来并调用纯函数。

**避免重复造轮子点**:

- 不恢复 `GraphStoreProtocol/AssociationEngine/RetrievalRouter`。
- 不引入 graph 框架。
- 只用纯函数提高可测性和可读性。

### 7.4 后续优先级

1. **短期**: 保持当前 215 测试绿；host-level graph ranking 已覆盖排序行为，不只覆盖内部调用。
2. **中期**: 提取 `runtime.py`，让 `__init__.py` 降到注册入口和兼容 re-export。
3. **中期**: 搜索参数收敛到 `SearchOptions` dataclass。
4. **长期/beta3**: dashboard adapter 收口后删除 legacy branches；旧模块逐步退场。

---

## 八、2026-06-04 命名清洁记录

**目标**: 移除活跃代码、脚本、测试中的版本批次命名和叙述性命名，让名称表达实际功能。

**验证证据**:

- `pytest tests/ -q` -> **215 passed**, 1 个 pytest cache 权限 warning。

### 8.1 运行时代码命名

| 旧名称 | 新名称 | 理由 |
|---|---|---|
| `_get_beta2_mem_store()` | `_get_indexed_mem_store()` | 表达 SQLite-indexed store 能力 |
| `_get_beta2_search_index()` | `_get_search_index()` | 表达检索索引职责 |
| `_get_beta2_graph_index()` | `_get_graph_index()` | 表达关联图职责 |
| `_get_beta2_reflect_engine()` | `_get_reflection_engine()` | 表达反射引擎职责 |
| `_get_beta2_context()` | `_get_memory_context()` | 表达上下文组装职责 |
| `_get_beta2_skill_store()` | `_get_indexed_skill_store()` | 表达技能存储职责 |
| `_store_mod`, `_search_mod`, `_graph_mod`, `_reflect_mod`, `_context_mod` | `_storage_module`, `_search_module`, `_graph_module`, `_reflection_module`, `_context_module` | 模块别名体现职责 |
| `MemoryStore._search()` | `MemoryStore._get_search_index()` | 避免把内部索引对象误读成搜索操作 |
| `_beta2_store_search` 动态模块名 | `_memory_search_module` | 移除版本批次叙述 |
| `_get_graph_manager()` | `_get_graph_interface()` | Dashboard 只依赖图接口，不依赖旧 manager 概念 |
| `_get_cluqi()` | `_get_cross_layer_query()` | 名称表达查询能力，而非历史实现名 |

### 8.2 脚本和测试命名

| 旧名称 | 新名称 | 理由 |
|---|---|---|
| `scripts/test_beta2.py` | `scripts/smoke_host_contract.py` | 脚本实际验证宿主工具/hook 合约 |
| `tests/test_beta2_smoke.py` | `tests/test_host_contract_smoke.py` | pytest 名称与合约 smoke 对齐 |
| `scripts/migrate_to_beta2.py` | `scripts/migrate_memory_index.py` | 脚本实际迁移 memory SQLite index |

### 8.3 文档同步

- `CLAUDE.md` 更新迁移和 smoke 命令。
- `README.md` 的当前版本说明改为 runtime services 描述。
- `docs/testing/test-coverage.md` 更新 smoke 测试文件名、测试总数和 helper 名称。
- `docs/research/beta2-architecture.md` 的入口接线图改为 runtime service 命名。
- 本报告同步移除 Round 2 之后新增内容里的旧函数名和旧脚本名。

### 8.4 保留项

历史设计、历史审查、版本号和迁移语境中的 `beta2` 字样保留，因为它们是版本记录，不是当前代码命名。功能参数 `hebbian_beta` 保留，因为它是数学/检索权重语义，不是版本批次名。

---

## 九、2026-06-04 边界补强实现记录

**目标**: 按前述代码审查报告中的边界问题和后续优化构思，优先实现无需大拆架构、但能提升真实正确性的修复。

**验证证据**:

- `pytest tests/test_store.py tests/test_graph.py tests/test_search.py -q` -> **42 passed**, 1 个 pytest cache 权限 warning。
- `pytest tests/ -q` -> **215 passed**, 1 个 pytest cache 权限 warning。

### 9.1 R2-E2: host-level graph ranking 测试

`TestStoreSearchGraphWiring.test_store_fusion_search_uses_injected_graph` 只能证明 graph 被调用，不能证明排序行为真的进入工具主路径。本轮新增 `test_store_fusion_search_graph_boost_changes_ranking`，通过 `MemoryStore.fusion_search(..., hebbian_beta>0)` 验证 graph activation 可以改变排序首位。

**实现评价**: 测试只约束外部排序行为，不绑定 `SearchIndex` 内部 scoring 细节。后续若 graph 实现替换，只要工具路径排序能力成立，测试仍然有效。

### 9.2 R2-E3: supersedes 目标校验下沉到 store 层

`MemoryStore.put()` 现在会在写文件前校验 `fm.supersedes` 指向的旧 memory 是否已存在。缺失目标直接抛出 `ValueError`，避免 Markdown 文件已经落盘但 SQLite lineage 写入失败或产生悬空语义。

**实现评价**: 这是 store 层最小严格校验，不改变 `_sync_from_disk()` 的历史导入容错路径；历史文件仍可通过索引维护工具审计，直接 API 写入则保持严格。

### 9.3 R2-E4: 多后继 latest_for 稳定选择

`latest_for()` 和 `lineage_chain()` 的前向查询从裸 `SELECT new_id` 改成 JOIN `memories` 后按 `created DESC, version DESC, rank DESC, new_id DESC` 选择。即使旧数据中存在一个旧 memory 被多个后继 supersede 的分支情况，也会稳定返回最新后继。

**实现评价**: 没有引入复杂 DAG lineage 解析，也没有迁移历史数据；在保持单链 API 的前提下消除了 SQLite 无序返回的不确定性。

### 9.4 R2-E5: graph meta zone 语义落地

`graph_meta` 增加 `zone TEXT NOT NULL DEFAULT 'general'`。`GraphIndex._init_db()` 会用 `PRAGMA table_info` 检查旧库并执行 `ALTER TABLE` 兼容迁移；`ensure_meta(memory_id, zone=...)` 现在会保存并刷新 normalized zone。

**实现评价**: 该修复让参数含义与持久化 schema 一致，同时保留旧 `associate()` 自动创建 meta 的默认 zone 行为。没有把 graph 重新改成拥有 memory 生命周期的旧式 manager。

### 9.5 当前残余边界

R2-E6 `reflect.py` full LLM 返回对象 partial parsed/error shape 仍未补测试。它涉及 fake LLM 返回协议，需要单独构造更细的 reflection harness；本轮没有扩大到该路径。
