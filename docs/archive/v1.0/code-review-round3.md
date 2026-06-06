# v1.0-beta2 代码审查报告 — Round 3

**日期**: 2026-06-04
**审查范围**: 跨模块代码简化、重复造轮子分析、开源替代评估

## 总体评价

beta2 Round 2 完成后，代码库功能完整（215 测试通过），但遗留了大量过渡期的重复代码和死代码。Round 3 专注于消除重复、替换可用的开源库、简化架构，目标是**在保持行为不变的前提下，减少 ~1,000 行代码**（约占 runtime 总代码的 17%）。

---

## 审查发现汇总

### 一、跨模块函数拷贝（最严重的重复）

同一函数在多个模块中独立定义，语义相同但实现可能有细微差异：

| 函数 | 定义次数 | 活跃位置 | 删除/合并目标 |
|------|---------|---------|-------------|
| `_is_explicit_memory_intent` | 4 次（embed.py, search.py, reflect.py, engine.py） | search.py 版本为死代码 | 删除 search.py 版本 |
| `_is_correction` | 4 次（同上） | search.py 版本为死代码 | 删除 search.py 版本 |
| `_memory_tokens` | 3 次（store.py, search.py, core.py） | search.py 版本重复 | 删除 search.py 版本，改用 import |
| `_calc_supersedes_depth` | 3 次（store.py, search.py, __init__.py），3 种不同算法 | 算法不一致 | 统一到 store.py |
| `_cosine_sim` / `_cosine_similarity` | 3 次（search.py, core.py, embed.py） | 实现细节不同 | 替换为 scipy 统一实现 |

### 二、无可用替代的手写组件（合理保持）

| 组件 | 原因 |
|------|------|
| CJK bigram tokenizer（`_tokenise`） | bm25s/nltk/jieba 均不支持 CJK bigram + BM25 的组合 |
| RRF（Reciprocal Rank Fusion） | 领域特定，无标准库 |
| MMR 多样性重排序 | 领域特定，无标准库 |
| Hebbian Spreading Activation | HeLa-Mem 领域算法 |
| PageRank（手写） | 当前实现 O(n·d) 已优化，networkx 额外构建 Graph 对象更浪费 |

### 三、可用开源替代的手写组件

| 组件 | 当前行数 | 开源替代 | 收益 |
|------|---------|---------|------|
| 英文停用词（103 个） | ~25 行 | nltk.corpus.stopwords（198 个） | 覆盖更全，社区维护 |
| 余弦相似度（纯 Python） | ~12 行 × 3 处 | scipy.spatial.distance.cosine | 数值稳定性更好 |
| BM25 手写（CJK fallback） | ~60 行 | bm25s（主路径） | 已使用，保留 fallback |

### 四、两套并存的 MemoryStore

`__init__.py:294` 的旧版 `MemoryStore`（~800 行）与 `store.py:623` 的新版 `MemoryStore`（~700 行）并存。旧版仍通过 `_get_mem_store()` 服务 17 个工具 handler，但 `_get_mem_store` 已在文件末尾（第 1973 行）被重定向到 `_get_indexed_mem_store()`。旧类定义本身不再被使用。

同样情况：`__init__.py:1116` 的旧 `SkillStore` 也已被 `store.py:477` 取代。

---

## 优化执行记录

<!-- 以下部分在执行过程中逐条记录 -->

### [已执行] P1: 删除 search.py 死代码

**日期**: 2026-06-04

**操作**:
- 删除 `search.py` 中的 `_is_explicit_memory_intent` 定义（第 249-257 行）
- 删除 `search.py` 中的 `_is_correction` 定义（第 260-267 行）
- 删除 `search.py` 中的 `_memory_tokens` 函数定义（第 145-148 行）
- 删除 `search.py` 中的 `_bm25_search` 函数定义（第 219-228 行）
- 将 `search.py` 引用改为从 `store.py` import `_memory_tokens`

**验证**: `pytest tests/ -q` → 215 passed

**收益**: 减少 ~30 行死代码，消除误导性 API 声明

---

### [已执行] P0: 统一 _calc_supersedes_depth 到 store.py

**日期**: 2026-06-04

**操作**:
- 确认 `store.py:1054` 的 SQL-based 实现最准确（直接查询 supersedes 表）
- 将 `search.py:714` 的 `_calc_supersedes_depth` 改为调用 `self.store._calc_supersedes_depth(mem_id)` 代理
- 将 `__init__.py:794` 的 `_calc_supersedes_depth` 改为调用 `self._get_indexed_mem_store()._calc_supersedes_depth(mem_id)` 代理

**验证**: `pytest tests/ -q` → 215 passed

**收益**: 消除 3 种不同 supersedes depth 算法的行为差异，未来语义变更只需修改一处

---

### [已执行] P2: 用 scipy 替换手写余弦相似度

**日期**: 2026-06-04

**操作**:
- 将 `search.py:129` 的 `_cosine_sim()` 纯 Python 实现替换为 `scipy.spatial.distance.cosine`

**验证**: `pytest tests/ -q` → 215 passed

**收益**: 减少 ~12 行，利用 scipy 的数值稳定性（零向量保护、float 精度）

---

### [已执行] P2: 用 nltk 替换手写英文停用词

**日期**: 2026-06-04

**操作**:
- 将 `store.py:255-269` 的 `_STOPWORDS` 从手写 103 个英文词改为 `set(nltk.corpus.stopwords.words('english'))`
- 保留 CJK 停用词 `_CJK_STOPWORDS` 不变（nltk 不支持）

**验证**: `pytest tests/ -q` → 215 passed

**收益**: 减少 ~15 行维护代码，停用词覆盖从 103 扩展到 198，由 nltk 社区维护

---

### [已执行] P1: SQLite body cache 优化 list() 性能

**日期**: 2026-06-04

**操作**:
- `store.py` `_SCHEMA` 中 `memories` 表增加 `body TEXT NOT NULL DEFAULT ''` 字段
- `_upsert_memory_row()` 写入 body 到 SQLite
- `_row_to_loaded()` 从 SQLite body 字段构建 LoadedMemory，不再读文件
- 新增 `_row_to_loaded_from_disk()` 方法，仅用于 `validate_index()` 和 `rebuild_index()` 的磁盘验证
- 增加 `_schemas_add_body_column()` 兼容迁移（`ALTER TABLE` 对旧数据库添加 body 字段）

**验证**: `pytest tests/ -q` → 215 passed

**收益**: `list()` 从 O(n) 文件 I/O 降为 O(1) SQL 查询。1,000 条记忆时预期从 ~1,000ms 降到 ~5ms

---

### [已执行] P0: 删除 __init__.py 旧 MemoryStore / SkillStore + 测试迁移

**日期**: 2026-06-04

**操作**:
- `conftest.py`：移除 `_ensure_package()` 注册逻辑，改为直接 `from store import MemoryStore`
- `_helpers.py`：从 `from store import MemoryFrontmatter`（需要 `to_dict()` 方法）
- `test_fusion_rerank.py`：4 处 `_inject_memories` 从内存注入改为 `store.put()`，Hebbian 测试从 `patch` 旧 `_get_graph_mgr` 改为直接 `si._graph = temp_graph`
- `test_wave3_retrieval.py`：3 处内存注入改为 `store.put()`，`MemoryFrontmatter` import 改为 store 版本
- `smoke_host_contract.py`：`MemoryStore` import 改为 `from store import MemoryStore`，适配新 store API
- `__init__.py`：删除旧 `MemoryStore` 类（825 行）+ 旧 `SkillStore` 类（64 行）
- `store.py`：`_row_to_loaded()` 补充 supersedes 查询（之前 body cache 重构漏掉了）

**验证**: `pytest tests/ -q` → 215 passed | `python scripts/smoke_host_contract.py` → 34 passed, 0 failed

**收益**: `__init__.py` 从 1,974 行降至 1,085 行（-890 行，45%），消除双轨实现风险

---

### 后续建议

1. **`_calc_supersedes_depth` 再次统一**：旧 `MemoryStore` 已删除，`__init__.py` 中不再有重复实现。但 `store.py:1054` 和 `search.py:714`（已 proxy）的方法签名不同（store 递归 vs search 代理），可考虑统一为纯 SQL 查询
2. **测试模块内聚性**：`test_fusion_rerank.py` 依赖于 `store.py`/`search.py`/`graph.py` 三个模块的交互验证，不易单独 mock。可拆分为三层：单元测试（纯函数/单类）、集成测试（模块间交互）、宿主合约测试（17 tools）。当前测试偏向"集成 + 宿主合约"混合，缺少纯单元测试层
3. **`hub_bonus` 参数是否保留**：`SearchIndex.search()` 以 `**kwargs` 接收 `hub_bonus` 但不生效。旧 store 的 hub_bonus 通过 `__init__` 的 `_get_graph_mgr` 和 `pagerank` 模块实现。如果新 pipeline 需要，应作为正式 feature 接入
2. `_mmr_rerank` token set 预计算：优化量不大，后续有余力再处理
3. 搜索参数收敛到 `SearchOptions` dataclass：SearchIndex 接口从 10+ 参数优化为 options 对象
