# mem-reflection-hermes — 代码审查修复状态跟踪

**更新日期**: 2026-06-01
**版本**: v0.9.2-beta — Hermes Agent 宿主对照二次审查问题已快速修复 ✅

---

## P0 (严重) — 全部修复 ✅

| # | 问题 | 模块 | 修复 |
|---|------|------|------|
| - | register 未导出 | tools.py | __all__ 添加 "register" |
| - | _bm25_search_scored 签名不一致 | core.py | 返回类型统一为 List[Tuple[LoadedMemory, float]] |

---

## P0 (严重) — 2026-06-01 宿主对照二次审查新增 🔴

本节基于 `D:\Codex_lib\code_reference\hermes-agent` 的当前插件契约复核。Hermes Agent 目录插件只调用包顶层 `register(ctx)`，hook 参数由 `agent/conversation_loop.py` 和 `model_tools.py` 提供。

| # | 问题 | 模块 | 当前状态 | 影响 |
|---|------|------|----------|------|
| H-1 | 包顶层 `register(ctx)` 实际来自 `tools.py`，未调用 `__init__.py` 中的 graph/slash 注册块 | `__init__.py`, `tools.py` | ✅ FIXED | 新增包顶层聚合 `register(ctx)`，宿主模拟验证 16 个工具、4 个 hook、8 个 slash command 均可注册 |
| H-2 | `pre_llm_call` 读取 `messages/ctx`，但 Hermes 当前传 `user_message/conversation_history/...` | `hooks.py` | ✅ FIXED | hook 同时支持 Hermes 当前 kwargs 与旧 `messages/ctx`，并验证能返回可注入 context |
| H-3 | `on_session_end` 期待 `messages/ctx`，但 Hermes 当前只传 session/model/completed/interrupted 等元数据 | `hooks.py`, `reflection.py` | ✅ FIXED | `register(ctx)` 保存宿主 ctx，`pre_llm_call` 缓存 session 消息，session-end 可使用缓存消息与 ctx 运行 |
| H-4 | 记忆写入/反射/CLUQI/Dashboard 调用不存在或层级不匹配的 API | `core.py`, `__init__.py`, `tools.py`, `cluqi.py`, `ahe_graph/__init__.py`, `dashboard/plugin_api.py` | ✅ FIXED | 补齐 `MemoryFrontmatter.new()`、`MemoryStore.get_by_id()`、manager activation/association aliases、Dashboard 包内导入和 CLUQI list/dict 兼容 |

**验证摘要**:

- Fake Hermes `PluginContext` 调用包顶层 `register(ctx)` 后得到 16 个工具、4 个 hook、8 个 command。
- 最小导入检查显示 `MemoryFrontmatter.new`、`MemoryStore.get_by_id`、`GraphMemoryManager.propagate_activation`、`AssociationEngine.on_memory_coactivation` 均存在。
- `srh_memory_write`、`pre_llm_call` context 注入、`post_tool_call` graph auto-associate、Dashboard 包导入和 CLUQI graph query 均已 smoke 验证。

---

## P0 (严重) — 跨插件问题发现 ✅

| # | 问题 | 插件 | 模块 | 修复 |
|---|------|------|------|------|
| - | compress_context 无限递归 | hermes-cache-optimizer | prefix_guard.py | 模块级预捕获 _ORIGINAL_COMPRESS_CONTEXT，替换函数体内运行时 import |

**根因**: `compress_context_guarded` 函数体内通过 `from agent.conversation_compression import compress_context as _original` 动态获取原始函数。但此时 `__init__.py` 已完成 monkey-patch（`cc_mod.compress_context = compress_context_guarded`），导致 `_original` 实际上指向了被 patch 后的自身，调用即递归 → `maximum recursion depth exceeded`。

**影响范围**: 所有非 DeepSeek 模型（kimi、openai 等）在对话长度触发 compression 时都会崩溃；DeepSeek 路径不走 `_original` 分支故不受影响。

**教训**: Monkey-patch 场景下，函数体内的动态 import 存在「自引用」风险。原始引用必须在 patch 发生前（模块加载时）捕获并闭包保存。

**验证**: 模块级捕获的函数 id ≠ patch 后的函数 id，`False` 确认无递归。

---

## P1 (重要) — 全部修复 ✅

| # | 问题 | 模块 | 修复 |
|---|------|------|------|
| 1 | fsync 缺失 | core.py | 新建 _safe_write() 含 flush+fsync |
| 2 | cosine_sim 维度检查 | embed.py | 添加 len(a)!=len(b) 守卫，返回 0.0 |
| 5 | get_neighbors try/except | ahe_graph | 已有 sqlite3.Error 保护 |
| 7 | on_session_end 容错 | hooks.py | 已有 try/except 包裹 |
| 9 | 消息截断 | reflection.py | _MAX_REFLECT_TRANSCRIPT_CHARS=16000 |
| 10 | delete 异常保护 | __init__.py | path.unlink() 加 OSError 保护 |

---

## P2 (建议) — 已修复/跳过 ✅

| # | 问题 | 模块 | 状态 | 说明 |
|---|------|------|------|------|
| 1 | BM25 IDF公式 | core.py | ✅ FIXED | 已添加 k1=1.5, b=0.75 |
| 2 | CJK标记化 | core.py | ⏭️ SKIP | 已有修正，功能正常 |
| 3 | 前端解析 | core.py | ⏭️ SKIP | 设计选择（零依赖） |
| 4 | 意图原型固定 | embed.py | ✅ FIXED | 新增 CONFIG_KEY_INTENT_PROTOTYPES 配置项 |
| 6 | 意图回退率 | embed.py | ✅ FIXED | 已有 _classify_intent_stats 计数器 |
| 8 | search日志 | __init__.py | ✅ FIXED | 已有 logger.debug 记录策略 |
| 10 | check_conflict复杂度 | __init__.py | ⏭️ SKIP | 已文档化，当前规模可接受 |
| 14 | ahe_graph异常 | __init__.py | ✅ FIXED | 已有多层异常处理 |
| 17 | BFS复杂度 | ahe_graph | ⏭️ SKIP | 已文档化，max_depth=3 可控 |
| 18 | 死边裁剪 | ahe_graph | ✅ FIXED | 已有 prune_threshold=0.005 |
| 20 | on_co_occurrence限制 | ahe_graph | ✅ FIXED | 已有 schema 限制 max 20 IDs |
| 23 | late-binding缓存 | hooks.py | ✅ FIXED | 新增 _lb() 缓存机制 |
| 25 | 微反思prompt | reflection.py | ⏭️ SKIP | 设计选择（可配置） |
| 26 | 全量反思频率 | reflection.py | ⏭️ SKIP | 已可配置 |
| 27 | 图可视化安全 | dashboard | ✅ FIXED | 已有 HTML 转义 |

---

## 额外发现 — 跨插件问题 ✅

### hermes-cache-optimizer P0 无限递归 (2026-05-31)

在排查 Hermes Agent `⚠ Auxiliary background review failed: maximum recursion depth exceeded` 报错时，发现 hermes-cache-optimizer 插件的 P0 ImmutablePrefix Guard 存在严重缺陷：

- **文件**: `plugins/hermes-cache-optimizer/prefix_guard.py`
- **问题**: 非 DeepSeek 模型触发 context compression 时无限递归
- **根因**: 函数体内运行时 import 在 monkey-patch 之后执行，`_original` 指向自身
- **修复**: 模块级预捕获 `_ORIGINAL_COMPRESS_CONTEXT`，替换运行时 import
- **状态**: ✅ 已修复并验证

详见 [hermes-cache-optimizer 修复记录](../hermes-cache-optimizer/README.md#p0--immutableprefix-guard-)

---

## 批次进度

| 批次 | 内容 | 状态 |
|------|------|------|
| P0 | register 导出 + _bm25_search_scored | ✅ 完成 |
| P1-A | fsync + cosine_sim + get_neighbors | ✅ 完成 |
| P1-B | on_session_end + 消息截断 + delete | ✅ 完成 |
| P2-A | BM25参数 + CJK + 前端解析 + 意图原型 | ✅ 完成 |
| P2-B | 意图回退率 + search日志 + check_conflict + ahe_graph异常 | ✅ 完成 |
| P2-C | 文件轮转 + 代码重构 + 快速路径 | 🔴 待处理 (6项) |

---

## P2-C 待处理项详情

| # | 问题 | 模块 | 优先级 | 状态 | 说明 |
|---|------|------|--------|------|------|
| 12 | SkillStore 禁用列表 | __init__.py | 低 | ✅ FIXED | 已添加 `_disabled_project_skills` + `disable_project_skill()` / `enable_project_skill()` / `list_disabled()`，`list()` 中过滤禁用项 |
| 22 | pre_llm_call 阶段保护 | hooks.py | 中 | ✅ FIXED | `_build_context_block(query)` 调用包裹 try/except，失败时 `logger.warning` + `context = None`，静默跳过而非整体失败 |
| 27 | pending_skills 单文件 | reflection.py | 低 | ✅ FIXED | 已添加 _MAX_PENDING_SKILLS=200 + 时间戳归档 |
| 29 | reflect_log 无限增长 | reflection.py | 中 | ✅ FIXED | 已添加 _MAX_REFLECT_LOG_LINES=5000 + 自动轮转 |
| 30 | _get_mem_store 重复定义 | tools.py/hooks.py | 低 | ✅ FIXED | `tools.py` 引入 `_lb()` 缓存机制，与 `hooks.py` 统一（12 个 late-binding 函数） |
| 32 | compile_profile 快速路径 | tools.py | 低 | ✅ FIXED | profile_mode 关闭时直接返回错误，不调用 LLM |

**P2-C 全部完成: 6/6 项** ✅

---

## 代码量统计 (更新)

| 模块 | 当前行数 | 状态 |
|------|---------|------|
| `__init__.py` | 1416 | ✅ 注册/导出/兼容层 |
| `core.py` | 652 | ✅ 存储与基础模型 |
| `embed.py` | 411 | ✅ ONNX 嵌入引擎 |
| `reflection.py` | 1085 | ✅ 反射管线可通过保存的宿主 ctx 与 session 消息缓存接入 |
| `hooks.py` | 276 | ✅ 生命周期钩子已兼容 Hermes 当前 kwargs |
| `tools.py` | 830 | ✅ 12 个基础工具可注册，写入路径 smoke 通过 |
| `__init__.py` graph tools | — | ✅ 4 个图工具已通过包顶层 `register(ctx)` 注册 |
| `ahe_graph/__init__.py` | 687 | ✅ 图记忆系统 |
| `cluqi.py` | 217 | ✅ CLUQI 跨层查询 smoke 通过 |
| `query_cache.py` | 163 | ✅ 查询缓存 |
| `cross_zone.py` | 112 | ✅ 跨区分析 |
| `pagerank.py` | 81 | ✅ 中心性计算 |
| `dashboard/plugin_api.py` | 487 | ✅ Dashboard API 包导入与 graph auto-associate 兼容层已修 |
| `bench_latency.py` | 336 | ✅ 性能基准 |
| **合计** | **~6,753** | |

## 当前监控项

- 暂无已知宿主集成阻断级残余风险。
- 后续如记忆规模继续扩大，可再评估 `effectiveness` 文件大小、SQLite 图边规模和反射日志轮转阈值。

---

## 修复批次总结

| 批次 | 内容 | 状态 |
|------|------|------|
| P0 | register 导出 + BM25 签名 | ✅ 完成 |
| P1 | fsync + cosine_sim + 截断 + delete + 别名 | ✅ 完成 |
| P2-A | debug日志 + 异常细化 + 导入缓存 | ✅ 完成 |
| P2-B | 意图原型配置化 | ✅ 完成 |
| P2-C | 文件轮转 + 代码重构 + 快速路径 | ✅ 完成 (6/6 项) |
