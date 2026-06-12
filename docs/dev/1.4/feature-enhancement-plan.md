# mem-reflection-hermes v1.4 功能增强与优化规划报告

> 日期: 2026-06-09  
> 当前基线: mem-reflection-hermes v1.4-beta 代码形态，`plugin.yaml` 已对齐 1.4-beta  
> 当前实现状态: 本文定义的 v1.4 范围已在当前分支完成实现，本文保留为规划基线与验收对照  
> 参考实现: `D:\Codex_lib\code_reference\mem0`、`D:\Codex_lib\code_reference\hy-memory`  
> 输入报告: `docs/mem0-comparison-report-v2.md`

## 1. 结论摘要

v1.4 不应把 mem0 或 hy-memory 整体搬进当前插件。当前插件的核心优势是文件可审计的 Markdown 记忆、SQLite 索引、Hebbian 图、supersedes 版本链和 session-end 自治反射。v1.4 的合理方向是把另外两个系统的成熟工程能力补到现有主线里：

1. 从 hy-memory 借鉴上下文注入工程：stable/dynamic 分离、recall 超时保护、上下文预算分级压缩、checkpoint 恢复。
2. 从 mem0 借鉴检索基础设施：实体抽取/实体 boost、查询 over-fetch 策略、多信号诊断输出、配置模型化。
3. 保留 SRH 自身优势：Markdown 是真源、SQLite 是索引层、Hebbian 图必须继续参与检索，而不是变成旁路数据。

建议 v1.4 目标命名为：

**v1.4 Context Reliability & Entity Recall**

交付重点不是增加更多工具数量，而是提高三件事的确定性：召回更准、注入更省、生命周期更可恢复。

## 2. 当前插件基线核查

### 2.1 已具备能力

当前插件已经不是单纯文件检索系统：

- `core/store.py` 以 Markdown 文件为持久真源，同时用 SQLite 建索引，支持 `memories`、`tags`、`supersedes`、`stats` 等结构。
- `core/search.py` 已有 BM25 + embedding recall、RRF fusion、recency/effectiveness/supersedes rerank、Hebbian boost、二阶段 reranker 和 MMR。
- `memory/context.py` 已有 4 层注入：pinned memories、relevant memories、triggered skills、always-active skills，并追加 episode summaries。
- `reflection/runtime.py` 已有 full reflection、micro reflection、raw chunk reflection、episode compaction、reflect-log rotation。
- `memory/curator.py` 已有 TTL/staleness、supersedes chain compaction、similarity scan/merge、cold storage、orphan graph edge cleanup。
- `runtime/hooks.py` 已接入 session/start/end、pre_llm_call、post_tool_call、api_error/subagent telemetry，并在 session end 串联 graph decay、reflection、compaction、curator。

### 2.2 主要短板

这些短板决定 v1.4 的优先级：

- `memory/context.py` 只有一个最终 `context` 字符串，没有 stable/dynamic 分离，容易破坏 prompt cache。
- `runtime/hooks.py` 的 pre-LLM 注入只有硬截断，没有分级压缩和降级策略。
- 当前 CJK tokenization 是 bigram heuristic，不具备 hy-memory 的 jieba search-mode 分词质量。
- Search 已有 Hebbian boost，但缺少 mem0 式实体层，遇到专名、项目名、文件名、工具名时依赖 BM25/embedding 运气。
- session 状态在内存中，反射/管道中断后缺少 checkpoint 恢复。
- 配置仍是 YAML dict 直接读取，缺少 schema/default/validation/diagnostic report。

## 3. 参考实现证据

### 3.1 mem0 可借鉴点

源码核查重点：

- `mem0/memory/main.py` 在 add 阶段批量 `extract_entities_batch`，把实体写入独立 entity store，并以 `linked_memory_ids` 关联记忆。
- `mem0/memory/main.py` 搜索阶段使用 `internal_limit = max(limit * 4, 60)` over-fetch，分别做 semantic search、keyword search、entity boosts，再由 `score_and_rank` 合成。
- `mem0/utils/scoring.py` 明确把 semantic、BM25、entity boost 做可解释合成，并保留每个信号的分量。
- `mem0/configs/base.py` 使用 Pydantic 配置模型，配置入口比当前插件的裸 dict 更稳定。

适合当前插件吸收的不是 mem0 的 vector-first 存储，而是：

- 实体抽取与实体-记忆反向索引。
- 搜索 over-fetch 和候选池诊断。
- 配置模型和错误提示。

不建议吸收：

- 用向量数据库取代 Markdown 真源。
- 每次 add 都强制 LLM 提取。
- 抛弃 supersedes 语义版本链。

### 3.2 hy-memory 可借鉴点

源码核查重点：

- `src/core/hooks/auto-recall.ts` 将 `prependContext` 作为 dynamic user-prefix，把 persona、scene navigation、tools guide 放进 `appendSystemContext` stable 区域。
- `src/core/hooks/auto-recall.ts` 对 recall 设置默认 timeout，超时跳过注入，避免阻塞用户请求。
- `src/core/store/sqlite.ts` 的 `buildFtsQuery()` 使用 jieba `cutForSearch`，对中文 FTS 查询显著优于字符 bigram。
- `src/core/hooks/auto-recall.ts` hybrid path 并行做 keyword + embedding，再用 RRF k=60 融合；TCVDB 可走 native hybrid short-circuit。
- `src/utils/pipeline-manager.ts` 把 L0->L1->L2->L3 管道拆成 warm-up threshold、idle timeout、downward-only L2 timer、SerialQueue、checkpoint recovery。
- `src/offload/*` 实现上下文卸载、MMD 注入、mild/aggressive/emergency 压缩策略。

适合当前插件吸收的是：

- stable/dynamic 注入协议。
- recall timeout 和失败降级。
- CJK 分词策略。
- checkpoint + 恢复语义。
- 分级上下文压缩。

不建议吸收：

- 全量 L0/L1/L2/L3 重写当前反射系统。
- 强依赖 Node.js/TCVDB/Mermaid offload 作为 v1.4 必需项。
- 用 SQLite 内部格式替代 Markdown 人工审计层。

## 4. v1.4 目标范围

### 4.1 产品目标

v1.4 的用户可感知结果：

- 中文、项目名、工具名、文件名相关记忆召回更稳。
- 长会话中注入上下文更少破坏 prompt cache。
- 记忆注入不会因搜索或 embedding 慢而拖住主请求。
- session 中断后，下次启动能知道哪些反射/策展工作尚未完成。
- 开发者能看到一条搜索结果为何出现：BM25、embedding、entity、Hebbian、recency/effectiveness 各贡献多少。

### 4.2 非目标

v1.4 不做：

- 多向量数据库后端。
- 完整 MemForest 层级存储。
- 完整 hy-memory L0/L1/L2/L3 管道替换。
- dashboard 大改版。
- 默认启用高成本 LLM 压缩。

## 5. 功能规划

### P0. Stable/Dynamic 上下文注入

目标：让 `memory/context.py` 输出结构化注入，而不是单一字符串。

设计：

- 新增 `ContextBundle`：
  - `prepend_context`: per-turn relevant memories，放用户消息前。
  - `append_system_context`: pinned/profile/skills/tool guide，稳定内容放 system 末尾。
  - `debug`: token count、裁剪原因、各层数量。
- 保留 `build_context()` 返回字符串的兼容包装。
- 新增 `build_context_bundle(store, search, skills, query, max_tokens)`。
- `runtime/hooks.py` 检测 host 是否支持双通道返回；不支持时合并为现有 `{"context": ...}`。

参考：

- hy-memory `auto-recall.ts` 的 `prependContext` / `appendSystemContext`。

验收：

- 旧测试 `tests/test_context.py` 不破。
- 新增测试覆盖 bundle 层级、预算裁剪、兼容字符串输出。
- `pre_llm_call` 在不支持双通道 host 上行为不变。

### P0. Recall Timeout 与 Fail-Open 注入

目标：搜索、embedding、图扩散、reranker 任一慢路径不能阻塞主请求。

设计：

- 在 `runtime/hooks.py` 的 context build 外包一层 deadline。
- 配置项：`memory.recall_timeout_ms`，默认 1500。
- 超时策略：返回 stable fallback，只注入 pinned/always-active skills；记录 warning 和 reflect-log diagnostic。
- Search 内部可选传入 `deadline_at`，embedding/reranker/Hebbian 分别检查 deadline。

参考：

- hy-memory `auto-recall.ts` 的 timeout guard。

验收：

- 构造慢 reranker，验证 pre-LLM 在 timeout 内返回。
- 超时不写入损坏缓存。
- 日志能区分 timeout、empty recall、normal recall。

### P0. CJK Search-Mode 分词适配

目标：把当前 CJK bigram 从唯一方案降级为 fallback，引入可选 jieba/search-mode tokenization。

设计：

- 新增 `core/tokenizer.py` 或保守地在 `core/store.py` 内封装 `_tokenise_cjk_search_mode`。
- 配置项：`search.cjk_tokenizer: auto | bigram | jieba`，默认 `auto`。
- Python 环境有 `jieba` 时使用 `jieba.cut_for_search`，无依赖时继续 bigram。
- BM25 index 与 query tokenization 必须同源。
- 记录 tokenizer diagnostic，便于排查中英文混合召回。

参考：

- hy-memory `sqlite.ts buildFtsQuery()` 的 jieba `cutForSearch`。

验收：

- 中文查询包含 “开发规划/上下文压缩/实体抽取” 等词时，token 不再只依赖非重叠 bigram。
- 无 jieba 环境测试仍通过。
- BM25 fallback 和 bm25s path 结果稳定。

### P1. Entity Recall Layer

目标：补齐 mem0 风格实体索引，但落在当前 SQLite/Markdown 架构里。

设计：

- SQLite 新增实体索引表：
  - `entities(id, text, normalized, type, created_at, updated_at)`
  - `entity_links(entity_id, memory_id, weight, source)`
- 写入路径：
  - `MemoryStore.put()` 后抽取实体，fail-open 写 entity_links。
  - 初版使用 regex + optional spaCy，不强制大模型。
  - 专名类型覆盖：quoted/code/file_path/package/tool/proper/compound。
- 删除/归档路径：
  - 删除 memory 时清理 entity_links。
  - curator orphan cleanup 扩展到 entity orphan。
- 搜索路径：
  - query 抽取实体。
  - 命中 entity_links 后生成 `entity_boosts`。
  - 与现有 RRF/Hebbian 合并时作为 rerank bonus，而不是替代 RRF。
- 诊断输出展示 `entity_hits`。

参考：

- mem0 的 `extract_entities_batch`、`linked_memory_ids`、`_compute_entity_boosts`。

验收：

- 专名查询能召回 embedding 不明显、BM25 分词不稳定的记忆。
- 删除记忆后 entity_links 无孤儿。
- 不安装 spaCy 时可用 regex fallback。

### P1. Search Explain 与信号分量

目标：让搜索结果可审计，支撑调参。

设计：

- 新增 `SearchResult` 内部结构或旁路 `explain=True`。
- 记录：
  - `bm25_rank/score`
  - `embedding_rank/score`
  - `rrf_score`
  - `entity_boost`
  - `hebbian_boost`
  - `recency_factor`
  - `effectiveness_factor`
  - `supersedes_factor`
  - `final_score`
- 默认 API 仍返回 `LoadedMemory` 以免破坏调用方。
- `srh_memory_search` 可选 `explain=true` 返回扩展 JSON。

参考：

- mem0 `score_and_rank` 保留多信号分量。
- 当前插件已有 fusion/rerank/Hebbian，只缺结构化暴露。

验收：

- 单元测试验证 explain 字段存在且最终排序可复现。
- Dashboard/API 可渐进接入，不要求 v1.4 同步改 UI。

### P1. Checkpoint 与 Pending Work 恢复

目标：反射、compaction、curator 在 session end 中断时可恢复，不只依赖内存状态。

设计：

- 新增 `runtime/checkpoint.py`：
  - 文件路径：`plugin_data_dir()/runtime-checkpoint.json`
  - 原子写入：临时文件 + replace。
  - 字段拆分：`session_states`、`pending_reflections`、`pending_curator_runs`、`last_completed`。
- `runtime/hooks.py`：
  - session end 开始前登记 pending reflection。
  - reflection 成功后标记 completed。
  - compaction/curator 同理。
- 启动或 session start 时：
  - 加载 checkpoint。
  - 对 pending 只做低风险恢复：curator 可直接重跑；reflection 需要有 transcript 快照才重跑，否则写 diagnostic。

参考：

- hy-memory `checkpoint.ts` 将 runner state 与 pipeline state 分开，避免互相覆盖。
- hy-memory `pipeline-manager.ts recoverPendingSessions()` 的 best-effort 恢复思想。

验收：

- 模拟 reflection 失败后 checkpoint 有 pending。
- 下一次 session start 能清理或报告 pending。
- checkpoint JSON 损坏时 fail-open 并备份损坏文件。

### P1. 分级上下文压缩

目标：替代当前硬截断，减少丢失高优先级上下文。

设计：

- 预算策略：
  - pinned 永不压缩，只按配置硬上限保护。
  - relevant memories 先 per-memory 摘短，再减少数量。
  - episode summaries 可压缩为 bullet digest。
  - skills 只保留 description/triggers，必要时省略 body。
- 压缩等级：
  - mild：格式瘦身，保留全部层。
  - aggressive：每条 memory 降到 1 行，skills 只保留名称。
  - emergency：只保留 pinned + top-K relevant ids/body preview。
- 不默认调用 LLM；LLM 压缩作为 future flag。

参考：

- hy-memory context offload 的 mild/aggressive/emergency 思路。

验收：

- 构造超预算上下文，不出现简单从尾部切断造成标签/Markdown 坏块。
- debug 记录 compression level。

### P2. 配置模型化与诊断

目标：把裸 dict 配置收束成可验证默认值。

设计：

- 新增 `core/config.py`，优先使用 Pydantic；无 Pydantic 时 dataclass fallback。
- 聚合配置：
  - `search`
  - `context`
  - `reflection`
  - `curator`
  - `entity`
  - `checkpoint`
- 提供 `get_config_diagnostics()`，展示默认值、未知 key、类型错误 fallback。
- 保留 `plugin_config()` 兼容。

参考：

- mem0 `MemoryConfig` 的模型化入口。

验收：

- 错误类型配置不会 crash。
- 未知配置 key 有 warning。
- 旧 YAML 配置无需迁移即可运行。

### P2. Native Hybrid/Backend 抽象预研

目标：只做接口，不做多后端落地。

设计：

- 定义 `SearchBackendCapabilities`：
  - `native_hybrid_search`
  - `entity_search`
  - `keyword_search`
  - `vector_search`
- 当前 SQLite/Markdown backend 返回 false/partial。
- 为未来 TCVDB/Qdrant/pgvector 留接口。

参考：

- hy-memory native hybrid short-circuit。
- mem0 VectorStoreFactory。

验收：

- 不改变默认搜索行为。
- 接口能被假 backend 单元测试覆盖。

## 6. 建议开发顺序

### Milestone 1: Context Reliability

范围：

- ContextBundle。
- pre_llm_call timeout。
- 分级压缩。
- 兼容旧 context 返回。

原因：

- 用户体验收益最大。
- 不触碰存储 schema，风险低。
- 为后续 search explain 和 entity recall 提供 debug 通道。

验证：

- `pytest tests/test_context.py tests/test_host_contract_smoke.py`
- 新增 timeout/compression tests。

### Milestone 2: CJK + Explainable Search

范围：

- CJK tokenizer auto/jieba/bigram。
- Search explain。
- recall over-fetch 参数化。

原因：

- 当前比较报告明确指出 CJK 是 hy-memory 的优势。
- Explain 先落地，后续 entity recall 调参才不会盲飞。

验证：

- `pytest tests/test_bm25.py tests/test_search.py tests/test_fusion_rerank.py tests/test_wave3_retrieval.py`
- 新增 CJK tokenizer tests。

### Milestone 3: Entity Recall

范围：

- entities/entity_links schema。
- 写入/删除/重建索引维护。
- query entity boost。
- orphan cleanup。

原因：

- 有 schema 变更和搜索排序变更，应在 explain 完成后做。

验证：

- `pytest tests/test_store.py tests/test_search.py tests/test_memory_curator.py tests/test_tool_handlers.py`
- 新增 entity index rebuild/delete tests。

### Milestone 4: Checkpoint Recovery

范围：

- runtime checkpoint。
- session-end pending reflection/curator 状态。
- startup/session-start recovery。

原因：

- 触碰生命周期，适合最后整合。

验证：

- 模拟异常中断。
- checkpoint corrupt recovery。
- `pytest tests/test_reflection.py tests/test_compaction.py tests/test_memory_curator.py`

## 7. 风险与控制

| 风险 | 影响 | 控制 |
|------|------|------|
| ContextBundle 与 host 返回协议不一致 | 注入失效 | 保留 `{"context": string}` fallback |
| jieba/spaCy 增加依赖失败 | 安装复杂 | optional import + fallback |
| entity boost 干扰 Hebbian 排序 | 召回漂移 | 先 explain，再小权重默认关闭或低权重 |
| checkpoint 重跑 reflection 造成重复记忆 | 记忆污染 | 只有 transcript snapshot 完整才重跑，并走 conflict/supersedes 检查 |
| 分级压缩隐藏关键记忆 | 行为退化 | pinned 永不压缩，debug 输出裁剪原因 |
| 配置模型破坏旧配置 | 启动失败 | dataclass/Pydantic 双路径，未知 key warning 不 fatal |

## 8. 配置草案

```yaml
plugins:
  mem_reflection_hermes:
    search:
      fusion_mode: rrf
      recall_overfetch_min: 60
      recall_overfetch_factor: 4
      cjk_tokenizer: auto
      explain_default: false
      entity_boost:
        enabled: true
        weight: 0.08
        extractor: auto

    context:
      token_budget: 2000
      recall_timeout_ms: 1500
      split_stable_dynamic: true
      compression:
        enabled: true
        mild_ratio: 0.85
        aggressive_ratio: 1.0
        emergency_ratio: 1.25

    checkpoint:
      enabled: true
      recover_on_session_start: true
      max_pending_sessions: 20
```

## 9. 文档与归档要求

v1.4 相关开发材料按版本号归档在：

- `docs/dev/1.4/feature-enhancement-plan.md`

后续建议新增：

- `docs/dev/1.4/implementation-checklist.md`
- `docs/dev/1.4/search-explain-design.md`
- `docs/dev/1.4/checkpoint-recovery-design.md`
- `docs/dev/1.4/acceptance-report.md`

根目录 `docs/mem0-comparison-report-v2.md` 作为横向比较报告保留，不再继续堆叠 v1.4 开发细节。

## 10. 最小验收矩阵

v1.4 完成标准：

- ContextBundle 双通道输出可用，旧 host fallback 可用。
- Recall timeout 生效，慢搜索不会阻塞主请求。
- CJK tokenizer 在有/无 jieba 两种环境均可运行。
- Search explain 能解释 BM25、embedding、Hebbian、entity 至少四类信号。
- Entity index 支持写入、删除、重建和 orphan cleanup。
- Checkpoint 能记录 pending session-end work，并能在下次启动报告或恢复。
- 全量现有测试通过，新增测试覆盖上述路径。

建议验证命令：

```powershell
pytest tests/test_context.py tests/test_search.py tests/test_bm25.py tests/test_fusion_rerank.py tests/test_store.py tests/test_memory_curator.py tests/test_host_contract_smoke.py
```

## 11. 下一步建议

先实现 Milestone 1。它风险最低，又能立即改善长会话体验；同时它会建立 v1.4 的结构化 context/debug 通道，使后续 CJK、entity、checkpoint 的行为更容易验证。
