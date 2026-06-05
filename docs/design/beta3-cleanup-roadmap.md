# Beta3 细粒度清理路线图

> Version: v1.0-beta3-plan  
> Date: 2026-06-04  
> Status: 已完成文档/代码比对，进入分功能清理方案冻结阶段

---

## 当前状态对齐

| 分支 | 最新 commit | 说明 |
|------|------------|------|
| 本地 `codex/1.0-beta` | `f1f0dd7` | noise filter + search.py shadowing fix + Codex PR feedback fix |
| 远程 `origin/main` | `79648fc` | search.py shadowing fix |
| 差异 | `+2 commits` | `9f1ae6c` (Codex feedback), `f1f0dd7` (noise filter sync) |

---

## Beta2 功能边界与代码现实

Beta3 清理的目标不是继续扩功能，而是把 beta2 重构后的运行面从 pre-beta2 模块中拆出来。当前代码呈现为“新 6 模块已落地，但宿主入口仍由 legacy 模块承载”的过渡状态：

| 功能面 | beta2 源代码事实 | pre-beta2 依赖风险 | 清理原则 |
|---|---|---|---|
| 存储与 frontmatter | `store.py` 已实现 SQLite 索引 + Markdown 冷存储、`MemoryStore`、`SkillStore`、lineage、temporal/context 字段 | `__init__.py`、`hooks/lifecycle.py`、`tools/handlers.py`、`scripts/smoke_host_contract.py` 仍引用 `core.py` 符号 | 以 `store.py` 为唯一持久层入口；先提供缺失兼容符号，再删除 `core.py` |
| 搜索与召回 | `search.py` 已实现 embedding、BM25/bm25s、RRF/weighted fusion、MMR、Hebbian boost、`include_history` | `search/` 包与 `search.py` 同名导致 fallback；旧测试仍测 `search/embed.py`、`query/cache.py` | 迁移显式意图、query template 等少量缺失颗粒后，删除 `search/` 和 `query/` |
| 图记忆 | `graph.py` 已实现 `GraphIndex`、Hebbian edges、spreading activation、PageRank、cross-zone、distill；`graph/compat.py` 是过渡 shim | dashboard、脚本、旧测试仍围绕 `ahe_graph`/`pagerank`/`cross_zone`/`cluqi` | 直接面向 `GraphIndex` 改写消费者；只保留必要的 CLUQI 功能，不保留旧目录 |
| 反射 | `reflect.py` 已实现 `ReflectionEngine`、raw_chunk 默认、micro/full、日志读写、audit 结构 | skill approval/rejection、pending skills、profile compilation、部分 slash helper 仍在 `reflection/engine.py` | 将用户可见功能迁到 `reflect.py` 或新 runtime helper，再删除 legacy engine |
| 上下文装配 | `context.py` 已实现 pinned、active/relevant、triggered skills、always-active skills 的 token-aware 装配 | `__init__.py` 中仍有旧 `_build_context_block`、palace index 逻辑和 legacy mode | 将 hook 的 `pre_llm_call` 改为调用 `context.build_context()`，保留 palace 行为所需最小 helper |
| Tools | 17 个 tool 中 12 个基础工具在 `tools/handlers.py`，4 个 graph 工具 + `srh_memory_health` 在 `__init__.py` | `tools/handlers.py` 直接依赖 `core.py`、`late_binding.py`、`reflection/engine.py`、`hooks/lifecycle.py` | 新建顶层 `tools.py` 或 `runtime/tools.py` 统一注册 17 tools，旧 handler 只作为迁移参考 |
| Hooks 与 slash commands | 4 个 hooks 在 `hooks/lifecycle.py` 注册；graph hook 在 `__init__.py` 追加；slash commands 分散在 `__init__.py` 与 lifecycle | hook 注册需要 late binding；session reflection 与 slash helper 依赖旧 engine | 新建顶层 `hooks.py` 或 `runtime/hooks.py`，用依赖注入访问 store/search/graph/reflect |
| Dashboard | `dashboard/plugin_api.py` 已有新 store/graph 接口探测，但仍保留旧 `_srh_dict`/fallback 路径 | fallback 会掩盖 legacy 删除后的真实破坏点 | 改成显式调用 beta2 runtime service；测试改为新接口 mock |

---

## 实施进度记录

### 2026-06-04

- 已完成 `search/query` 迁移第一步：将 `QueryTemplate`、`QUERY_TEMPLATES`、`build_query()`、`ResultCache`、`get_cache()` 统一放到 `search.py`。
- 已将 `query/cache.py` 收敛为兼容薄壳，避免新旧两套 query/cache 实现并行。
- 已将 `tests/test_query_cache.py` 和 `scripts/check_v092.py` 改为直接验证 `search.py` 新入口。
- 已修正 `scripts/check_v092.py` 的版本断言；beta3 收尾后已提升为 `1.0-beta3`，与当前 `plugin.yaml` 对齐。
- 验证结果：
  - `python -m py_compile search.py query\\cache.py tests\\test_query_cache.py scripts\\check_v092.py`
  - `python -m pytest tests\\test_query_cache.py -q`
  - `python scripts\\check_v092.py`
- 已开始图入口收敛：`dashboard/plugin_api.py` 的 graph / neighbors / zone 分析改为直接走 `graph.compat.GraphManagerCompat`。
- 已在兼容层 `graph/compat.py` 暴露 `pagerank()` 和 `cross_zone()`，用于替换 dashboard 内的旧图 importlib fallback。
- 验证结果：
  - `python -m py_compile dashboard\\plugin_api.py graph\\compat.py`
  - `python -m pytest tests\\test_dashboard.py -q`
- 已完成图测试与旧校验脚本迁移到 compat 层：
  - `tests/conftest.py`、`tests/test_graph_operations.py`、`tests/test_wave3_retrieval.py` 已改为使用 `graph.compat.GraphStore`
  - `scripts/check_v092.py` 已移除旧 `graph.ahe_graph` / `graph.pagerank` / `graph.cross_zone` / `graph.cluqi` 直接依赖，改为验证 compat surface
- 兼容层已补齐旧测试所需的行为：`upsert_edge` 累加、`get_neighbors` 去重与 `exclude_relations`、`spread_activation`、`decay_edges`、PageRank 对孤立节点补零、`stats` 兼容键。
- 验证结果：
  - `python -m pytest tests\\test_graph_operations.py tests\\test_wave3_retrieval.py -q`
  - `python scripts\\check_v092.py`
- 已将 dashboard `/query` 从旧 `graph.cluqi` fallback 收回到轻量 compat 桥接，不再直接依赖旧 graph query 模块。
- 验证结果：
  - `python -m py_compile dashboard\\plugin_api.py`
  - `python -m pytest tests\\test_dashboard.py -q`
  - `python scripts\\check_v092.py`
- 已补齐 package 级运行桥接：新增 `runtime_hooks.py` / `runtime_tools.py`，并将 `__init__.py` 的星导入与 `register(ctx)` 切到新桥接层，避免主入口继续直连旧 `hooks.lifecycle` / `tools.handlers`。
- 已将 `reflect.py` 扩成新的 reflection 公共面，补出 `_build_audit_entry`、`_recent_reflect_outcomes` 等 helper，并把 `scripts/smoke_host_contract.py` 的反射验收切到新入口。
- 已将 `hooks/lifecycle.py` 与 `tools/handlers.py` 的 reflection 依赖收敛到 `reflect.py`，让 legacy engine 只作为过渡实现存在。
- 已把 `hooks/lifecycle.py` / `tools/handlers.py` 的 `late_binding` 依赖改成包级属性读取，避免运行链再直连 `late_binding.py`。
- 已在 `store.py` 补齐 frontmatter / lineage 的薄入口，并把 `scripts/smoke_host_contract.py` 的存储校验从 `core.py` 切到 `store.py`。
- 验证结果：
  - `python -m py_compile __init__.py reflect.py runtime_hooks.py runtime_tools.py hooks\\lifecycle.py tools\\handlers.py scripts\\smoke_host_contract.py`
  - `python scripts\\smoke_host_contract.py`
  - `python -m pytest tests\\test_dashboard.py tests\\test_query_cache.py tests\\test_host_contract_smoke.py -q`
  - `python scripts\\check_v092.py`
- 已将剩余测试面全面迁移到 `store.py`，`tests/conftest.py`、`tests/test_core_data.py`、`tests/test_tool_handlers.py`、`tests/test_bm25.py`、`tests/test_wave3_retrieval.py`、`tests/test_reflection.py` 均不再直接 import `core.py`。
- 已把遗留 `search/embed.py` 与 `reflection/engine.py` 的核心导入切到 `store.py`，并删除 `core.py`，使 runtime / scripts / tests 均不再依赖旧主模块。
- 验证结果：
  - `python -m py_compile __init__.py store.py search.py search\\embed.py reflection\\engine.py hooks\\lifecycle.py tools\\handlers.py runtime_hooks.py runtime_tools.py scripts\\smoke_host_contract.py scripts\\check_v092.py tests\\conftest.py tests\\test_core_data.py tests\\test_tool_handlers.py tests\\test_bm25.py tests\\test_wave3_retrieval.py tests\\test_reflection.py`
  - `python -m pytest tests\\test_core_data.py tests\\test_tool_handlers.py tests\\test_bm25.py tests\\test_wave3_retrieval.py tests\\test_reflection.py tests\\test_query_cache.py tests\\test_dashboard.py tests\\test_host_contract_smoke.py -q`
- 运行面补充确认：仓库内已无 runtime 直接 `import core`，`core.py` 现已移除。
- 已将 dashboard 的 cache 统计从 `query.cache` 切到 `search.get_cache()`，并删除 `query/cache.py`。
- 已删除 `query/__init__.py`，`query/` 目录不再承载任何运行面代码。
- 验证结果：
  - `python -m py_compile dashboard\\plugin_api.py search.py`
  - `python -m pytest tests\\test_query_cache.py tests\\test_dashboard.py tests\\test_host_contract_smoke.py -q`
- 已删除 `graph/cluqi.py`，并将 `graph/__init__.py` 的包级导出清理为只保留 `ahe_graph`、`pagerank`、`cross_zone`。
- 验证结果：
  - `python -m py_compile __init__.py graph\\__init__.py dashboard\\plugin_api.py store.py search.py reflection\\engine.py hooks\\lifecycle.py tools\\handlers.py scripts\\smoke_host_contract.py scripts\\check_v092.py`
  - `python -m pytest tests\\test_graph_operations.py tests\\test_dashboard.py tests\\test_host_contract_smoke.py tests\\test_query_cache.py -q`
- 已将 `__init__.py` 的 graph integration 收敛为 `graph.compat` 单一路径，去掉对 legacy `ahe_graph` 的回退分支。
- 验证结果：
  - `python -m py_compile __init__.py`
  - `python -m pytest tests\\test_graph_operations.py tests\\test_dashboard.py tests\\test_host_contract_smoke.py -q`
  - `python scripts\\check_v092.py`
- 已将 `graph/__init__.py` 收敛为只转发 `GraphIndex`，并删除 `graph/ahe_graph.py`、`graph/pagerank.py`、`graph/cross_zone.py`，避免包导入时再加载旧图实现。
- 验证结果：
  - `python -m py_compile __init__.py graph\\__init__.py graph\\compat.py dashboard\\plugin_api.py hooks\\lifecycle.py`
  - `python -m pytest tests\\test_graph_operations.py tests\\test_dashboard.py tests\\test_host_contract_smoke.py -q`
- 已删除 `late_binding.py`，并将 `reflection/engine.py` 改为直接通过包根公开函数读取运行时依赖，彻底去掉 late-binding 运行桥。
- 验证结果：
  - `python -m py_compile reflection\\engine.py __init__.py`
  - `python -m pytest tests\\test_reflection.py tests\\test_dashboard.py tests\\test_host_contract_smoke.py -q`
- 最终门禁已通过：`python -m pytest tests -q` -> 215 passed, 1 warning。

### 2026-06-05

- 已按 beta3 计划完成一轮代码现实复核：当前 Python 运行面已无 `core.py`、`late_binding.py`、`search.embed`、`query.cache`、`graph.ahe_graph`、`graph.pagerank`、`graph.cross_zone`、`graph.cluqi` 的直接引用。
- 已发现并修复 graph 工具链的清理回归：
  - `srh_graph_viz` 仍按旧 schema 查询 `graph_memory_meta` / `graph_edges` / `id`。
  - graph post hook 仍按旧 schema 更新图节点状态。
  - package graph 单例把目录传给 `GraphManagerCompat`，而兼容层实际需要 `graph.db` 文件路径。
- 已将上述路径对齐到当前 `GraphIndex` schema：
  - 节点表：`graph_meta`
  - 边表：`edges`
  - 记忆主键：`memory_id`
  - graph db 路径：`plugin_data_dir() / "graph.db"`
- 已将 `scripts/check_issues.py` 从已删除 legacy 文件迁移到当前 canonical 文件：
  - `tools/handlers.py`
  - `search.py`
  - `store.py`
  - `reflection/engine.py`
  - `graph.py`
- 本轮审查未修改任何测试文件；当前测试文件的已有改动属于前序迁移遗留，需要单独按变更范围确认。
- 验证结果：
  - `python scripts\\check_issues.py` -> P0/P1/P2 sample checks all passed
  - `python -m py_compile scripts\\check_issues.py __init__.py graph\\compat.py graph\\__init__.py dashboard\\plugin_api.py hooks\\lifecycle.py tools\\handlers.py store.py search.py reflection\\engine.py`
  - `python -m pytest tests -q` -> 215 passed, 1 warning
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed
- 已继续完成 beta3 深清理推进：
  - `runtime_tools.py` 已从 facade 提升为工具 canonical 实现，`tools/handlers.py` 仅保留兼容入口。
  - `runtime_hooks.py` 已从 facade 提升为 hook canonical 实现，`hooks/lifecycle.py` 仅保留兼容入口。
  - `runtime_graph.py` 已承接 `GraphManagerCompat` / `GraphStore` 兼容实现，`__init__.py`、dashboard 与 `scripts/check_v092.py` 均改为使用 runtime graph 面；`graph/compat.py` 仅保留兼容入口。
  - `runtime_reflection.py` 已承接旧 reflection engine 的完整运行实现，`reflect.py` 不再委托 `reflection/engine.py`，而是委托 runtime reflection；`reflection/engine.py` 仅保留兼容入口。
  - 兼容入口采用 runtime 源码执行或模块别名方式，确保旧路径下的私有符号访问与 monkeypatch 仍作用于实际运行全局。
- 已同步验证脚本 canonical 指向：
  - `scripts/check_issues.py` 的 tools 检查切到 `runtime_tools.py`。
  - `scripts/check_issues.py` 的 reflection 检查切到 `runtime_reflection.py`。
  - `scripts/check_v092.py` 的 graph 检查切到 `runtime_graph.py`。
- 本轮深清理未修改任何测试文件；全量测试仍通过。
- 已完成 manifest / check script 术语收敛复核：
  - `plugin.yaml` 已对齐为 17 tools 与 runtime graph 描述，不再保留 16 tools / `ahe_graph` / `CLUQI` 的发布描述。
  - `scripts/check_v092.py` 的 graph 验证命名改为 runtime graph surface / extensions，保留原断言覆盖面不变，避免验证脚本继续输出 pre-beta2 术语。
- 验证结果：
  - `python -m py_compile scripts\\check_v092.py scripts\\smoke_host_contract.py`
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python scripts\\check_issues.py` -> Total issues found: 0
  - `python -m pytest tests -q` -> 215 passed, 1 warning
  - `rg -n "16 SRH|ahe_graph|CLUQI|test_cluqi|test_ahe|v0\\.9\\.2|v1\\.0-beta Verification" plugin.yaml scripts/check_v092.py` -> no matches
- 已进一步压缩兼容入口参与范围：
  - `tools/__init__.py` 直接从 `runtime_tools.py` 导出，不再经由 `tools/handlers.py`。
  - `hooks/__init__.py` 直接从 `runtime_hooks.py` 导出，不再经由 `hooks/lifecycle.py`。
  - `reflection/__init__.py` 直接从 `runtime_reflection.py` 导出，不再经由 `reflection/engine.py`。
  - `tools/handlers.py`、`hooks/lifecycle.py`、`reflection/engine.py` 继续仅作为显式旧路径兼容入口保留。
- 验证结果：
  - `python -m py_compile tools\\__init__.py hooks\\__init__.py reflection\\__init__.py runtime_tools.py runtime_hooks.py runtime_reflection.py tools\\handlers.py hooks\\lifecycle.py reflection\\engine.py __init__.py`
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed
  - `python scripts\\check_issues.py` -> Total issues found: 0
  - `python -m pytest tests -q` -> 215 passed, 1 warning
  - `rg -n "from \\.(handlers|lifecycle|engine) import|handlers import \\*|lifecycle import \\*|engine import \\*" tools\\__init__.py hooks\\__init__.py reflection\\__init__.py` -> no matches
- 已完成非测试/非路线图历史段的 pre-beta2 残留扫描：
  - `rg -n "core\\.py|late_binding\\.py|search/embed|search\\.embed|query/cache|query\\.cache|graph/ahe_graph|graph\\.ahe_graph|graph/pagerank|graph\\.pagerank|graph/cross_zone|graph\\.cross_zone|graph/cluqi|graph\\.cluqi|CLUQI|ahe_graph|v0\\.9\\.2|16 SRH" --glob "!tests/**" --glob "!docs/design/beta3-cleanup-roadmap.md"` -> no matches
  - `rg --files | rg "(^|/)(core\\.py|late_binding\\.py|embed\\.py|cache\\.py|ahe_graph\\.py|pagerank\\.py|cross_zone\\.py|cluqi\\.py)$|(^|/)query/"` -> no matches
  - 当前剩余旧命名只存在于路线图的历史依赖分析段和测试历史 diff；本轮仍未修改任何测试文件。
- 已清理 package 根的 runtime 星导入：
  - `__init__.py` 不再 `from .reflect import *`、`from .runtime_tools import *`、`from .runtime_hooks import *`。
  - package 根保留显式 `_auto_rebalance_zones()` 委托，供 runtime reflection/hooks 的 late-binding 消费者使用。
  - `register(ctx)` 继续显式调用 `runtime_tools.register_tools(ctx)`、`runtime_hooks.register_commands(ctx)` 和 `runtime_graph.register_graph_features(...)`，宿主注册面保持 17 tools / 4 hooks / 8 slash commands。
- 验证结果：
  - `python -m py_compile __init__.py runtime_tools.py runtime_hooks.py runtime_reflection.py`
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed
  - `python scripts\\check_issues.py` -> Total issues found: 0
  - `python -m pytest tests -q` -> 215 passed, 1 warning
  - `rg -n "from \\.(reflect|runtime_tools|runtime_hooks) import \\*|engine.py's __all__|overwrites the root-native" __init__.py` -> no matches
  - `rg -n "core\\.py|late_binding\\.py|search/embed|search\\.embed|query/cache|query\\.cache|graph/ahe_graph|graph\\.ahe_graph|graph/pagerank|graph\\.pagerank|graph/cross_zone|graph\\.cross_zone|graph/cluqi|graph\\.cluqi|CLUQI|ahe_graph|v0\\.9\\.2|16 SRH" --glob "!tests/**" --glob "!docs/design/beta3-cleanup-roadmap.md"` -> no matches
- 验证结果：
  - `python -m py_compile runtime_tools.py runtime_hooks.py runtime_graph.py runtime_reflection.py tools\\handlers.py hooks\\lifecycle.py graph\\compat.py reflection\\engine.py __init__.py dashboard\\plugin_api.py scripts\\check_issues.py scripts\\check_v092.py scripts\\smoke_host_contract.py`
  - `python -m pytest tests -q` -> 215 passed, 1 warning
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python scripts\\check_issues.py` -> P0/P1/P2 sample checks all passed
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed
- 当前剩余架构债务：
  - `tools/handlers.py`、`hooks/lifecycle.py`、`graph/compat.py`、`reflection/engine.py` 仍作为 deprecated 兼容入口存在，用于保护显式旧导入和现有外部 contract。
  - package 根 runtime 星导入债务已清理，`register(ctx)` 现在通过显式 runtime module 调用注册功能。
  - 若下一轮要删除兼容入口，需要先明确外部插件宿主和历史测试是否仍允许旧模块路径消失。
- 已给保留兼容入口加上明确 beta3 deprecated docstring：
  - `tools/handlers.py` -> canonical `runtime_tools.py`
  - `hooks/lifecycle.py` -> canonical `runtime_hooks.py`
  - `graph/compat.py` -> canonical `runtime_graph.py`
  - `reflection/engine.py` -> canonical `runtime_reflection.py`
- 已删除额外发现的顶层旧图实现：
  - `ahe_graph/__init__.py` 是 pre-beta2 AHE graph 完整旧实现，当前非测试源码无引用。
  - 删除后 graph canonical/compat 边界保持为 `runtime_graph.py` / `graph/compat.py`。
- 验证结果：
  - `rg --files | rg "(^|/)(ahe_graph|core\\.py|late_binding\\.py|embed\\.py|cache\\.py|pagerank\\.py|cross_zone\\.py|cluqi\\.py)$|(^|/)query/"` -> no matches
  - `rg -n "ahe_graph|graph\\.ahe_graph|mem_reflection_hermes\\.ahe_graph" --glob "*.py" --glob "!tests/**"` -> no matches
  - `python -m py_compile __init__.py runtime_graph.py graph\\compat.py graph\\__init__.py scripts\\check_v092.py scripts\\smoke_host_contract.py`
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed
  - `python scripts\\check_issues.py` -> Total issues found: 0
  - `python -m pytest tests -q` -> 215 passed, 1 warning
- 已同步当前入口文档的 beta3 runtime 命名：
  - `README.md`、`REFERENCES.md`、`docs/ARCHITECTURE.md`、`docs/DASHBOARD.md`、`docs/TOOLS.md`、`docs/MEMORY_FORMAT.md`、`CLAUDE.md` 不再把 `ahe_graph`、`CLUQI`、`core.py`、`search/embed.py`、`query/cache.py` 等旧模块描述为当前运行架构。
  - 历史 changelog/review/internal 文档保留旧术语作为版本证据，不参与当前运行面判定。
- 验证结果：
  - `rg -n "ahe_graph|CLUQI|v0\\.9\\.2|16 SRH|graph_memory_meta|graph_edges|query/cache|search/embed|late_binding|core\\.py" README.md REFERENCES.md docs\\ARCHITECTURE.md docs\\DASHBOARD.md docs\\TOOLS.md docs\\MEMORY_FORMAT.md CLAUDE.md plugin.yaml` -> only retired-file/history-version references remain
  - `python -m py_compile __init__.py runtime_tools.py runtime_hooks.py runtime_graph.py runtime_reflection.py dashboard\\plugin_api.py scripts\\check_v092.py scripts\\smoke_host_contract.py scripts\\check_issues.py`
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed
  - `python scripts\\check_issues.py` -> Total issues found: 0
  - `python -m pytest tests -q` -> 215 passed, 1 warning
- 验证结果：
  - `python -m py_compile tools\\handlers.py hooks\\lifecycle.py graph\\compat.py reflection\\engine.py __init__.py runtime_tools.py runtime_hooks.py runtime_graph.py runtime_reflection.py`
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed
  - `python scripts\\check_issues.py` -> Total issues found: 0
  - `python -m pytest tests -q` -> 215 passed, 1 warning

### 2026-06-05 最终完成性审计

- 目标拆解：
  - 清理当前版本遗留且弃用的 pre-beta2 代码。
  - 不影响 beta2 重构后的 17 tools / 4 hooks / 8 slash commands / dashboard / graph / reflection 功能。
  - 清理过程中不修改测试文件。
  - 测试验证必须全部通过。
- 代码与文件树证据：
  - `rg --files | rg "(^|/)(ahe_graph|core\\.py|late_binding\\.py|embed\\.py|cache\\.py|pagerank\\.py|cross_zone\\.py|cluqi\\.py)$|(^|/)query/"` -> no matches。
  - `rg -n "core\\.py|late_binding\\.py|search/embed|search\\.embed|query/cache|query\\.cache|ahe_graph|graph\\.ahe_graph|graph/pagerank|graph\\.pagerank|graph/cross_zone|graph\\.cross_zone|graph/cluqi|graph\\.cluqi|CLUQI|16 SRH" --glob "!tests/**" --glob "!docs/design/beta3-cleanup-roadmap.md" --glob "!docs/review/**" --glob "!docs/internal/**" --glob "!docs/research/**" --glob "!docs/CHANGELOG.md" --glob "!docs/design/PLAN_0_9_2_BETA2.md"` -> no matches。
  - `tools/handlers.py`、`hooks/lifecycle.py`、`graph/compat.py`、`reflection/engine.py` 均只作为 beta3 deprecated 兼容入口存在，并在文件 docstring 中指向 canonical `runtime_*` 模块。
- 运行面证据：
  - `scripts/smoke_host_contract.py` 验证 `register(ctx)` 注册 17 tools、4 unique hooks、8 slash commands，且 `plugin.yaml` 的 `provides_tools` / `provides_hooks` 与实际注册面一致。
  - `scripts/check_v092.py` 已改为 runtime graph / search / dashboard / version 验证，不再 import 已删除旧模块。
  - `scripts/check_issues.py` 已改为检查 canonical runtime/store/search/graph/reflection 文件。
- 测试约束：
  - 本阶段收尾清理未修改任何测试文件。
  - 当前工作区的测试文件 diff 属于前序迁移历史状态；按用户约束，本阶段不再触碰、不回滚、不扩大。
- 当前结论：
  - pre-beta2 实现文件与当前入口文档中的旧运行面描述已清理完成。
  - 仅保留四个 deprecated 显式旧导入兼容入口；这不是实现迁移未完成，而是外部 contract 保护。删除它们需要另行确认外部宿主和历史导入路径可以消失。
  - 下方“分功能细粒度清理方案”和原始依赖分析段继续保留为决策历史，其中的 `NEEDS_MIGRATION` / `未开始` 行是初始快照，不代表当前完成状态。

### 2026-06-05 Beta3 第一轮代码审查

- 已新增 `docs/review/CODE_REVIEW_v1.0-beta3_R1.md`。
- 审查范围：`store.py`、`search.py`、`graph.py`、`runtime_tools.py`、`runtime_hooks.py`、`runtime_graph.py`、`runtime_reflection.py`、`dashboard/plugin_api.py`、`__init__.py`。
- 审查维度：
  - 功能意图：17 tools、4 hooks、8 slash commands、dashboard CRUD/query/graph、search/graph/reflection/store runtime。
  - 逻辑实现：runtime canonical 路径、compat 边界、cache/index invalidation、zone-scoped retrieval、graph connection lifecycle。
  - 边界处理：SQLite closed connection recovery、mutation aftereffects、dashboard delete cleanup、version metadata and verification script alignment。
- 已修复 4 项：
  - `MemoryStore` mutation 后统一标记 palace index dirty 并失效 search cache。
  - `GraphIndex._get_conn()` 检测并恢复已关闭 thread-local SQLite 连接。
  - dashboard 删除记忆时不再关闭 runtime graph 共享连接。
  - `SearchIndex.search(zone=...)` 将 zone 过滤提前到候选池构建阶段。
- 版本描述已提升到 `1.0-beta3`：
  - `plugin.yaml`
  - `README.md`
  - `CLAUDE.md`
  - `docs/ARCHITECTURE.md`
  - `docs/DASHBOARD.md`
  - `docs/TOOLS.md`
  - `docs/DATA_SAFETY.md`
  - `docs/testing/test-coverage.md`
  - `docs/CHANGELOG.md`
  - `scripts/check_v092.py`
- 验证结果：
  - `python -m py_compile __init__.py runtime_tools.py runtime_hooks.py runtime_graph.py runtime_reflection.py store.py search.py graph.py reflect.py context.py dashboard\\plugin_api.py scripts\\check_v092.py scripts\\smoke_host_contract.py scripts\\check_issues.py`
  - `python -m pytest tests\\test_store.py tests\\test_search.py tests\\test_fusion_rerank.py tests\\test_graph.py tests\\test_dashboard.py tests\\test_host_contract_smoke.py -q` -> 75 passed, 1 warning
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python scripts\\check_issues.py` -> Total issues found: 0
  - `python -m pytest tests -q` -> 215 passed, 1 warning
  - `rg -n "1\\.0-beta2|v1\\.0-beta2|Expected 1\\.0-beta2|v1\\.0-beta2 Runtime" README.md CLAUDE.md docs\\ARCHITECTURE.md docs\\DASHBOARD.md docs\\TOOLS.md docs\\DATA_SAFETY.md docs\\testing\\test-coverage.md plugin.yaml scripts\\check_v092.py` -> only historical `1.0-beta2-redesign.md` link remains

---

## 分功能细粒度清理方案

### F1. 存储、frontmatter、lineage

**当前实现**

- 新实现：`store.py` 的 `MemoryFrontmatter`、`LoadedMemory`、`MemoryStore`、`SkillStore`、`parse_frontmatter()`、`serialize_frontmatter()`、`write_memory_atomic()`、`latest_for()`、`lineage_chain()`、`is_superseded()`。
- 遗留依赖：`__init__.py` 从 `core.py` import 约 40 个符号；`scripts/smoke_host_contract.py` 仍从 `mem_reflection_hermes.core` 验证 frontmatter 和 lineage helper；旧 tests 仍保留 `test_core_data.py`。

**清理操作**

1. 在 `store.py` 补齐仍被公开消费的兼容函数名，优先用薄 wrapper 指向 canonical 方法：
   - `_lineage_latest(store, id)` -> `store.latest_for(id)`
   - `_lineage_root(store, id)` -> `store.lineage_chain(id)[0]`
   - `_lineage_depth(store, id)` -> `len(store.lineage_chain(id)) - 1`
   - `_lineage_cycle_check(store, id)` -> 迁移现有 cycle guard
   - `_classify_update_intent()`、`_is_expired()`、`_is_context_mismatch()` -> 若仍用于工具/schema，迁入 `store.py`
2. 将 `__init__.py` 的 `from .core import ...` 改为 `from .store import ...`，只保留 beta2 runtime 仍需要的符号。
3. 将 `scripts/smoke_host_contract.py` 的 frontmatter/lineage import 改为 `mem_reflection_hermes.store`。
4. 将 `tests/test_core_data.py` 拆成：
   - `tests/test_store_frontmatter.py`
   - `tests/test_store_lineage.py`
   并删除对 `core.py` 的直接 import。
5. 确认 `core.py` 不再被 runtime、scripts、tests 引用后删除。

**验收**

- `python -m pytest tests/test_store.py tests/test_store_frontmatter.py tests/test_store_lineage.py -q`
- `python scripts/smoke_host_contract.py`
- `rg -n "from .*core|import .*core|mem_reflection_hermes\\.core" --glob "*.py"` 无 runtime 命中。

### F2. 搜索、embedding、query template、缓存

**当前实现**

- 新实现：`search.py` 已包含 `_embed_single()`、`_cosine_sim()`、`_extract_keywords()`、`SearchIndex`、TTL result cache、BM25/bm25s fallback。
- 缺口：`query/cache.py` 的 `QUERY_TEMPLATES` 与 `build_query()` 尚未在新模块中提供；`search/embed.py` 的 `_is_explicit_memory_intent()` 在 `hooks/lifecycle.py` fallback 中仍被使用。

**清理操作**

1. 将 `_is_explicit_memory_intent()` 迁入 `reflect.py` 或 `search.py`。建议放入 `reflect.py`，因为它是反射触发语义，不是检索语义。
2. 将 `QUERY_TEMPLATES` 与 `build_query()` 迁入 `search.py`，命名为：
   - `QUERY_TEMPLATES`
   - `build_query(template: str, **params)`
   保持旧测试用例期望，删除自定义 `ResultCache`。
3. 把 `tests/test_query_cache.py` 改成只验证 query template 与 `SearchIndex` TTL cache 边界，不再 import `query.cache`。
4. 删除 `__init__.py`、`hooks/lifecycle.py`、`reflection/engine.py`、`tools/handlers.py` 中所有 `search.embed` importlib fallback。
5. 删除 `search/` 和 `query/` 目录。

**验收**

- `python -m pytest tests/test_search.py tests/test_query_cache.py tests/test_reflect.py -q`
- `rg -n "search\\.embed|query\\.cache|ResultCache|get_cache" --glob "*.py"` 无命中。
- `python -m pytest tests/test_host_contract_smoke.py -q` 仍通过。

### F3. 图记忆、PageRank、cross-zone、CLUQI

**当前实现**

- 新实现：`graph.py` 已覆盖 `associate()`、`neighbors()`、`spread()`、`pagerank()`、`cross_zone()`、`distill()`。
- 过渡层：`graph/compat.py` 暴露 legacy manager/store shape，供旧 `__init__.py`、hooks、dashboard 暂时工作。
- 缺口：`graph/cluqi.py` 的 CLUQI 查询对象尚未进入 `graph.py` 或新 search/context 层。

**清理操作**

1. 把 dashboard 图接口改为直接使用 `GraphIndex`：
   - stats -> `graph.stats()`
   - neighbors -> `graph.neighbors(memory_id)`
   - zone analysis -> `graph.cross_zone(store)`
   - PageRank -> `graph.pagerank()`
2. 将 `srh_associate`、`srh_graph_retrieve`、`srh_graph_stats`、`srh_graph_viz` 的 handler 从 `__init__.py` 迁入新 tools 注册模块，并直接调用 `GraphIndex`。
3. 将旧 CLUQI 的可见能力收敛为 beta2 的“跨层 palace/search 聚合”：
   - 若 dashboard/工具只需要 query orchestration，迁入 `search.py` 的 `cross_layer_search()` 或 `tools.py` 的 handler 私有函数。
   - 若仍需要 `CLUQIResult` 结构，创建 `graph_query.py` 或在 `search.py` 中定义轻量 dataclass，禁止保留 `graph/cluqi.py`。
4. 将 `scripts/check_v092.py` 改为直接检查 `GraphIndex`、`SearchIndex`、`MemoryStore`。
5. 将 `tests/test_graph_operations.py`、`tests/test_wave3_retrieval.py` 的 legacy imports 改为 `graph.py` API；无法映射的 legacy-only 断言删除或移动到 migration note。
6. 删除 `graph/ahe_graph.py`、`graph/pagerank.py`、`graph/cross_zone.py`、`graph/cluqi.py` 和 `graph/compat.py`。

**验收**

- `python -m pytest tests/test_graph.py tests/test_graph_operations.py tests/test_wave3_retrieval.py tests/test_dashboard.py -q`
- `rg -n "ahe_graph|graph\\.compat|graph\\.pagerank|graph\\.cross_zone|graph\\.cluqi|CLUQI" --glob "*.py"` 只允许新 CLUQI 命名位置命中。

### F4. Reflection、skill approval、profile compilation

**当前实现**

- 新实现：`reflect.py` 覆盖 raw_chunk、micro/full reflection、audit、log recent。
- 遗留功能：pending skill candidates、approve/reject skill、profile compilation、若干 slash helper 仍在 `reflection/engine.py`。

**清理操作**

1. 将 pending skill 数据结构和文件路径迁入 `reflect.py`：
   - `load_pending_skill_candidates()`
   - `save_pending_skill_candidates()`
   - `format_pending_skills_for_display()`
   - `approve_skill(pending_id)`
   - `reject_skill(pending_id, reason)`
2. 将 profile compilation 迁入 `context.py` 或 `reflect.py`。建议：
   - LLM/反射生成逻辑放 `reflect.py`
   - profile 读取/注入放 `context.py`
3. 将 `srh_reflect_now` 和 `srh_compile_profile` handler 改为调用 `_get_reflection_engine()` 与新 profile helper。
4. 将 slash commands `/reflect`、`/pending-skills`、`/approve-skill`、`/reject-skill`、`/compile-profile` 改为调用新 helper。
5. 将 `scripts/smoke_host_contract.py` 的 `_build_audit_entry`、`_append_reflect_log`、`_recent_reflect_outcomes` import 改为 `reflect.py`。
6. 删除 `reflection/engine.py` 与 `reflection/__init__.py`。

**验收**

- `python -m pytest tests/test_reflect.py tests/test_reflection.py tests/test_tool_handlers.py -q`
- 新增或迁移 slash/helper 测试覆盖 approve/reject/profile。
- `rg -n "reflection\\.engine|from .*reflection import|_run_full_reflection|_approve_skill|_compile_profile" --glob "*.py"` 无 legacy 命中。

### F5. Hooks 与上下文注入

**当前实现**

- 旧实现：`hooks/lifecycle.py` 负责 `_on_session_start`、`_on_session_end`、`_pre_llm_call`、`_post_tool_call`、session message buffer、graph enrichment、slash helper。
- 新实现：`context.py` 与 `reflect.py` 已具备 hook 所需核心能力，但尚无新 hook 模块。

**清理操作**

1. 新建顶层 `hooks.py`，提供：
   - `set_plugin_context(ctx)`
   - `on_session_start(**kwargs)`
   - `on_session_end(**kwargs)`
   - `pre_llm_call(**kwargs)`
   - `post_tool_call(**kwargs)`
   - `register_hooks(ctx)`
2. `pre_llm_call` 只做三件事：
   - 提取用户 query
   - 调用 `context.build_context(store, search, skills, query)`
   - 返回 host 期望的注入结构
3. `on_session_end` 只做 session buffer flush 与 `ReflectionEngine.full()`。
4. `post_tool_call` 合并 graph 维护行为：
   - memory write -> `graph.ensure_meta()`、`graph.associate()`、supersedes edge migration
   - memory delete -> 标记 graph meta inactive/deleted，并衰减相关边
5. `tools.register(ctx)` 不再注册 hooks；hook 注册统一由 `hooks.register_hooks(ctx)` 完成，避免 4 hooks + graph post hook 分散。
6. 删除 `hooks/lifecycle.py` 与 `hooks/__init__.py`。

**验收**

- `scripts/smoke_host_contract.py` 确认 package-level `register(ctx)` 注册 4 个 hook 名，且 `pre_llm_call`/`post_tool_call` 接受 Hermes 当前 kwargs。
- `python -m pytest tests/test_context.py tests/test_host_contract_smoke.py -q`
- `rg -n "hooks\\.lifecycle|late_bind|late_binding" --glob "*.py"` 无命中。

### F6. Tools 注册与 17 个工具

**当前实现**

- `tools/handlers.py` 注册 12 个基础工具，并顺带注册 hooks。
- `__init__.py` 注册 5 个图/健康工具。
- `plugin.yaml` 声明 17 个工具，host smoke 也以 17 个为验收。

**清理操作**

1. 新建顶层 `tools.py`，统一注册 17 个工具：
   - memory: `srh_memory_search`、`srh_memory_write`、`srh_memory_delete`、`srh_memory_history`、`srh_memory_health`
   - skills/reflection/profile: `srh_skill_search`、`srh_reflect_now`、`srh_compile_profile`
   - palace: `srh_palace_zones`、`srh_palace_read_zone`、`srh_palace_recall`、`srh_palace_search`、`srh_palace_rebalance`
   - graph: `srh_associate`、`srh_graph_retrieve`、`srh_graph_stats`、`srh_graph_viz`
2. Handler 依赖只允许来自 `store.py`、`search.py`、`graph.py`、`reflect.py`、`context.py` 和 package-level service getter。
3. 把 `tools/handlers.py` 中可复用的 schema 文本和参数校验按功能搬迁；禁止携带 `late_binding` 和旧 reflection imports。
4. 将 `register(ctx)` 改为：
   - 初始化 runtime services
   - `tools.register_tools(ctx, services)`
   - `hooks.register_hooks(ctx, services)`
   - `commands.register_commands(ctx, services)`（若拆命令）
5. 删除 `tools/handlers.py` 与 `tools/__init__.py`。

**验收**

- `python scripts/smoke_host_contract.py` 输出 17 tools、4 hooks。
- `python -m pytest tests/test_tool_handlers.py tests/test_host_contract_smoke.py tests/test_e2e.py -q`
- `plugin.yaml` 的 `provides_tools` 与 fake ctx 实际注册集合完全一致。

### F7. Slash commands

**当前实现**

- `__init__.py` 注册 `/reflect`、`/pending-skills`、`/approve-skill`、`/reject-skill`、`/memories`、`/skills`、`/compile-profile`。
- graph 集成块额外注册 `/graph`。

**清理操作**

1. 新建 `commands.py` 或在 `tools.py` 底部提供 `register_commands(ctx, services)`。
2. 将 8 个命令统一迁移：
   - `/reflect`
   - `/pending-skills`
   - `/approve-skill`
   - `/reject-skill`
   - `/memories`
   - `/skills`
   - `/compile-profile`
   - `/graph`
3. 命令 handler 只调用新 tools/reflect/graph helper，不直接读写 legacy 模块。
4. smoke 脚本把 slash command 数量纳入验收，避免 beta3 清理后静默丢命令。

**验收**

- fake ctx 注册命令集合为上述 8 个。
- `/graph stats` 调用 `GraphIndex.stats()`；`/graph associate` 调用 `GraphIndex.associate()`。

### F8. Dashboard API

**当前实现**

- dashboard 已有 `_get_store()`、`_get_graph_interface()` 等新接口探测。
- 但文件开头仍通过 `sys.modules` 或 `from .. import __dict__ as _srh_dict` 访问 package 内部函数，导致旧导出删除后容易隐性失败。

**清理操作**

1. 引入显式 runtime service 获取：
   - `from mem_reflection_hermes import _get_indexed_mem_store, _get_search_index, _get_graph_index, _get_reflection_engine`
2. 对 dashboard 测试保留 mock，但 mock 新 service getter，而不是 mock `_srh_dict`。
3. 删除 dashboard 中对 `ahe_graph`、`cluqi`、`query.cache`、旧 tool handler 的 fallback。
4. 保持 API response shape 不变；只替换内部调用路径。

**验收**

- `python -m pytest tests/test_dashboard.py -q`
- 手工 smoke：list/create/delete/reorder/stats/graph/reflections/zones endpoint 均可在 isolated temp home 下运行。

### F9. 测试与脚本迁移

**当前实现**

- `docs/testing/test-coverage.md` 明确记录 215 tests，其中 legacy coverage 仍测试 `core.py`、`graph/`、`search/`、`query/cache.py`、`reflection/engine.py`、`tools/handlers.py`。

**清理操作**

1. 先将 legacy tests 改为新模块验收，禁止先删测试。
2. 每个 legacy 测试文件处理规则：
   - `test_core_data.py` -> store/frontmatter/lineage
   - `test_graph_operations.py` -> `GraphIndex`
   - `test_bm25.py` -> `store._tokenise` + `search._bm25_search_scored`
   - `test_query_cache.py` -> `search.build_query` + `SearchIndex` TTL
   - `test_reflection.py` -> `reflect.py`
   - `test_tool_handlers.py` -> new `tools.py`
   - `test_wave3_retrieval.py` -> `GraphIndex` + `SearchIndex`
3. `scripts/check_v092.py` 从 beta2 release check 迁移为 beta3 cleanup check：
   - import 新模块
   - assert no legacy module imports
   - assert package register count
4. `scripts/smoke_host_contract.py` 不再 import legacy 模块，作为 beta3 最终 gate。

**验收**

- `python -m pytest tests/ -q`
- `python scripts/smoke_host_contract.py`
- `python scripts/check_v092.py`
- `python -m py_compile __init__.py store.py search.py graph.py reflect.py context.py tools.py hooks.py`

---

## 建议实施顺序（按功能风险递增）

| Step | 功能颗粒 | 主要文件 | 删除目标 | 为什么先/后 |
|---|---|---|---|---|
| 1 | 搜索/query 小颗粒 | `search.py`, `tests/test_query_cache.py` | `query/`, `search/` fallback | 缺口小，能快速消除同名 shadowing |
| 2 | 图 API 消费者 | `graph.py`, `dashboard/plugin_api.py`, graph tests | `graph/ahe_graph.py`, `pagerank.py`, `cross_zone.py` | `GraphIndex` 覆盖度最高，适合先迁移 |
| 3 | CLUQI 收敛 | `search.py` 或 `graph_query.py` | `graph/cluqi.py` | 先决定保留能力形态，再删除旧模块 |
| 4 | store/core 替换 | `store.py`, `__init__.py`, smoke/tests | `core.py` | 影响面大，但多数符号已有新源头 |
| 5 | reflection 能力补齐 | `reflect.py`, reflection tests | `reflection/engine.py` | skill/profile 是真实功能，不可直接删 |
| 6 | 新 hooks | `hooks.py`, `context.py`, smoke | `hooks/lifecycle.py`, `late_binding.py` | hook 是宿主入口，必须有 smoke 网兜 |
| 7 | 新 tools 统一注册 | `tools.py`, `plugin.yaml`, smoke | `tools/handlers.py` | 17 tools 是最终用户面，最后收口 |
| 8 | package 入口瘦身 | `__init__.py`, docs/testing | 所有 legacy star import/fallback | 完成后 package 只负责 service wiring |

---

## Beta3 清理完成判定

清理完成必须同时满足以下条件：

- `plugin.yaml` 声明的 17 tools 与 `register(ctx)` 实际注册集合一致。
- 4 个 public hooks 仍为 `on_session_start`、`on_session_end`、`pre_llm_call`、`post_tool_call`。
- 8 个 slash commands 仍可注册。
- 默认 search/CLUQI/palace recall 不返回 superseded memory，除非显式 `include_history=true`。
- graph API response 仍包含 `graph_semantics: associative_coactivation`。
- reflection log 仍可读旧记录，并可写 beta2 audit 字段。
- dashboard API response shape 不因内部迁移改变。
- 仓库内无 runtime import 命中：
  - `core.py`
  - `late_binding.py`
  - `search/embed.py`
  - `reflection/engine.py`
  - `hooks/lifecycle.py`
  - `tools/handlers.py`
  - `graph/ahe_graph.py`
  - `graph/pagerank.py`
  - `graph/cross_zone.py`
  - `graph/cluqi.py`
  - `query/cache.py`

最终 gate：

```powershell
python -m pytest tests/ -q
python scripts/smoke_host_contract.py
python scripts/check_v092.py
python -m py_compile __init__.py store.py search.py graph.py reflect.py context.py tools.py hooks.py
rg -n "core|late_binding|search\\.embed|reflection\\.engine|hooks\\.lifecycle|tools\\.handlers|ahe_graph|graph\\.pagerank|graph\\.cross_zone|graph\\.cluqi|query\\.cache" --glob "*.py"
```

最后一条 `rg` 允许命中迁移说明文档，但不允许命中 runtime、scripts、tests 中的旧 import。

## 旧模块清单（15 个文件/目录）

### 文件级清单

| # | 文件 | 行数 | 类型 | 新模块替代 |
|---|------|------|------|-----------|
| 1 | `core.py` | ~1,000 | 模块 | `store.py` (MemoryStore/Frontmatter/Config) + `search.py` (BM25) |
| 2 | `late_binding.py` | ~40 | 工具 | 无（新模块无循环导入） |
| 3 | `search/__init__.py` | ~5 | 包标记 | `search.py` (顶层模块) |
| 4 | `search/embed.py` | ~500 | 模块 | `search.py` (embedding/BM25/keywords) |
| 5 | `reflection/__init__.py` | ~5 | 包标记 | `reflect.py` |
| 6 | `reflection/engine.py` | ~1,700 | 模块 | `reflect.py` (ReflectionEngine) |
| 7 | `graph/__init__.py` | ~5 | 包标记 | `graph.py` (顶层模块) |
| 8 | `graph/ahe_graph.py` | ~1,000 | 模块 | `graph.py` (GraphIndex) + `graph/compat.py` (兼容层) |
| 9 | `graph/cluqi.py` | ~300 | 模块 | 需合并到 `graph.py` 或保留独立 |
| 10 | `graph/pagerank.py` | ~120 | 模块 | `graph.py` (内置 pagerank()) |
| 11 | `graph/cross_zone.py` | ~130 | 模块 | `graph.py` (内置 cross_zone()) |
| 12 | `hooks/lifecycle.py` | ~420 | 模块 | 需重写为 `hooks.py` 或合并到 `__init__.py` |
| 13 | `tools/handlers.py` | ~1,000 | 模块 | 需重写为 `tools.py` 或合并到 `__init__.py` |
| 14 | `query/__init__.py` | ~5 | 包标记 | 无（cachetools 替代） |
| 15 | `query/cache.py` | ~210 | 模块 | `search.py` (TTLCache) |

---

## 细颗粒依赖分析

### 当前完成状态（2026-06-05 复核）

> 本节保留下方原始依赖分析作为决策依据；以下状态表记录当前工作树已经完成到哪一层。

| Wave | 颗粒 | 当前状态 | 当前 canonical/兼容入口 | 说明 |
|---|---|---|---|---|
| Wave 1 | `graph/ahe_graph.py` | ✅ 已迁移并删除旧实现 | `runtime_graph.py` / `graph/compat.py` | `GraphStore`/manager 形状由 runtime graph 兼容面承接；旧文件已删除。 |
| Wave 1 | `graph/cluqi.py` | ✅ 已迁移并删除旧实现 | dashboard runtime query path | dashboard `/query` 不再依赖旧 CLUQI 文件；旧文件已删除。 |
| Wave 1 | `graph/pagerank.py` | ✅ 已迁移并删除旧实现 | `GraphIndex.pagerank()` / `runtime_graph.py` | `scripts/check_v092.py` 已改为 runtime graph 验证。 |
| Wave 1 | `graph/cross_zone.py` | ✅ 已迁移并删除旧实现 | `GraphIndex.cross_zone()` / `runtime_graph.py` | dashboard 与脚本不再直接 import 旧模块。 |
| Wave 2 | `query/cache.py` | ✅ 已迁移并删除旧实现 | `search.py` | `QUERY_TEMPLATES`、`build_query()`、cache surface 已进入 `search.py`。 |
| Wave 3 | `search/embed.py` | ✅ 已迁移并删除旧实现 | `search.py` | embedding、cosine、keyword、intent helpers 已由 `search.py` 承接。 |
| Wave 4 | `reflection/engine.py` | ✅ 运行实现已迁出 | `runtime_reflection.py` / `reflection/engine.py` | 旧路径保留为兼容入口，在旧模块命名空间执行 runtime 实现以支持历史 monkeypatch/私有符号访问。 |
| Wave 5 | `hooks/lifecycle.py` | ✅ 运行实现已迁出 | `runtime_hooks.py` / `hooks/lifecycle.py` | hook canonical 实现已迁入 runtime；旧路径保留为兼容入口。 |
| Wave 5 | `tools/handlers.py` | ✅ 运行实现已迁出 | `runtime_tools.py` / `tools/handlers.py` | 12 个基础 tool handler canonical 实现已迁入 runtime；旧路径保留为兼容入口。 |
| Wave 6 | `core.py` | ✅ 已迁移并删除旧实现 | `store.py` / `search.py` | store/frontmatter/config/lineage 由 `store.py` 承接；BM25/search 由 `search.py` 承接。 |
| Wave 6 | `late_binding.py` | ✅ 已删除 | 无 | 运行面改用 package/root 或 runtime 显式导入，不再需要 late binding helper。 |

当前剩余不是“实现迁移未完成”，而是兼容 contract 决策：

- `graph/compat.py`、`reflection/engine.py`、`hooks/lifecycle.py`、`tools/handlers.py` 仍作为旧导入入口保留。
- `graph/__init__.py`、`search/__init__.py`、`reflection/__init__.py`、`hooks/__init__.py`、`tools/__init__.py` 仍作为包级兼容包装存在。
- 在“不修改测试文件”的约束下，这些兼容入口暂时保留；进一步删除需要先确认外部宿主和历史导入 contract 可以消失。

当前验证门禁：

- `python -m pytest tests -q` -> 215 passed, 1 warning
- `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - 覆盖 `plugin.yaml` provides_tools/provides_hooks 与 `register(ctx)` 实际注册集合完全一致。
  - 覆盖 8 个 slash commands 注册集合。
- `python scripts\\check_issues.py` -> 0 issues
- `python scripts\\check_v092.py` -> 7 passed, 0 failed
- `python -m py_compile runtime_tools.py runtime_hooks.py runtime_graph.py runtime_reflection.py ...` -> passed

### 2026-06-05 graph 注册深清理

- 已将 package 根 `__init__.py` 中的 graph 工具、graph post hook、`/graph` 命令、`srh_memory_health` 注册逻辑迁入 `runtime_graph.register_graph_features()`。
- `__init__.py` 现在只负责调用 runtime graph 注册入口并把 graph getter/path 传给 runtime hooks，不再承载 graph handler 实现体。
- 已删除 `__init__.py` 中旧的不可达内联 graph 注册块。
- 已清理 `__init__.py` 中随 graph 内联块迁出后不再使用的 `json`、`threading` import。
- 验证结果：
  - `python -m py_compile __init__.py runtime_graph.py`
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python -m pytest tests -q` -> 215 passed, 1 warning
  - `python scripts\\check_issues.py` -> 0 issues
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed

### 2026-06-05 slash command 注册深清理

- 已将 7 个基础 slash command 的注册动作从 `__init__.py` 迁入 `runtime_hooks.register_commands()`：
  - `/reflect`
  - `/pending-skills`
  - `/approve-skill`
  - `/reject-skill`
  - `/memories`
  - `/skills`
  - `/compile-profile`
- `/graph` 继续由 `runtime_graph.register_graph_features()` 注册。
- `__init__.py` 的注册函数已改名为 `_register_runtime_features()`，现在只负责调用 runtime command/graph 注册入口。
- 验证结果：
  - `python -m py_compile runtime_tools.py runtime_hooks.py runtime_graph.py runtime_reflection.py tools\\handlers.py hooks\\lifecycle.py graph\\compat.py reflection\\engine.py __init__.py dashboard\\plugin_api.py scripts\\check_issues.py scripts\\check_v092.py scripts\\smoke_host_contract.py`
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed
  - `python -m pytest tests -q` -> 215 passed, 1 warning
  - `python scripts\\check_issues.py` -> 0 issues
  - `python scripts\\check_v092.py` -> 7 passed, 0 failed

### 2026-06-05 package 根 wiring 精简

- 已删除 `__init__.py` 中冗余的 `_gm_getter_func` / `_gm_getter_path` 转发层。
- graph manager getter/path 现在由 `runtime_graph.register_graph_features()` 直接注入 `runtime_hooks`。
- `__init__.py` 的 `_register_runtime_features()` 只负责调用 runtime command/graph 注册入口，不再保存 graph runtime 状态。
- 验证结果：
  - `python -m py_compile __init__.py runtime_graph.py runtime_hooks.py runtime_tools.py`
  - `python scripts\\smoke_host_contract.py` -> 37 passed, 0 failed

### Wave 1:  graph/ 目录（4 个模块，可最先清理）

#### 8. graph/ahe_graph.py
- **功能颗粒**: GraphStore, GraphMemoryManager, AssociationEngine, RetrievalRouter
- **新模块覆盖**: `graph.py` GraphIndex + `graph/compat.py` GraphManagerCompat
- **消费者分析**:
  - `__init__.py`: 通过 `graph.compat` shim 优先加载 ✅ 已兼容
  - `dashboard/plugin_api.py`: 有 `ahe_graph` importlib fallback → 需删除 fallback
  - `scripts/check_v092.py`: 直接 `from mem_reflection_hermes.graph.ahe_graph import GraphStore`
  - `tests/conftest.py`: `from graph.ahe_graph import GraphStore`
  - `tests/test_graph_operations.py`: `from graph.ahe_graph import GraphStore`
  - `tests/test_wave3_retrieval.py`: `from graph.ahe_graph import GraphStore`
- ** verdict**: 🔶 **NEEDS_MIGRATION**
- **行动**: 更新 tests + check_v092 + 删除 dashboard fallback

#### 9. graph/cluqi.py
- **功能颗粒**: CLUQI (Cross-Layer Unified Query), CLUQIResult
- **新模块覆盖**: `graph.py` 不包含此功能 ❌
- **消费者**:
  - `dashboard/plugin_api.py`: `_get_cross_layer_query()` importlib fallback
  - `scripts/check_v092.py`: 直接 import
- **verdict**: 🔶 **NEEDS_MIGRATION**
- **行动**: 将 CLUQI 合并到 `graph.py` 或保留为独立模块

#### 10. graph/pagerank.py
- **功能颗粒**: `compute_pagerank()`, `get_top_pagerank()`
- **新模块覆盖**: `graph.py` 内置 `GraphIndex.pagerank()` ✅
- **消费者**:
  - `dashboard/plugin_api.py`: importlib fallback → 需删除
  - `scripts/check_v092.py`: 直接 import
  - `tests/test_graph_operations.py`: 直接 import
  - `tests/test_wave3_retrieval.py`: 直接 import
- **verdict**: 🔶 **NEEDS_MIGRATION**
- **行动**: 更新 consumers 调用 `GraphIndex.pagerank()`

#### 11. graph/cross_zone.py
- **功能颗粒**: `analyze_zone_connections()`, `get_zone_recommendations()`
- **新模块覆盖**: `graph.py` 内置 `GraphIndex.cross_zone()` ✅
- **消费者**:
  - `dashboard/plugin_api.py`: importlib fallback → 需删除
  - `scripts/check_v092.py`: 直接 import
- **verdict**: 🔶 **NEEDS_MIGRATION**
- **行动**: 更新 consumers 调用 `GraphIndex.cross_zone()`

---

### Wave 2: query/ 目录（1 个模块）

#### 14. query/cache.py
- **功能颗粒**: ResultCache (TTL + LRU), QUERY_TEMPLATES, `build_query()`
- **新模块覆盖**: `search.py` 使用 `cachetools.TTLCache` 替代了 ResultCache；但 **QUERY_TEMPLATES 和 build_query 没有替代**
- **消费者**:
  - `dashboard/plugin_api.py`: `from ..query.cache import get_cache` (try/except 内)
  - `scripts/check_v092.py`: 直接 import ResultCache, get_cache, build_query, QUERY_TEMPLATES
- **verdict**: 🔶 **NEEDS_MIGRATION**
- **行动**: 将 QUERY_TEMPLATES + build_query 合并到 `search.py`；ResultCache 已废弃

---

### Wave 3: search/ 目录（1 个模块）

#### 4. search/embed.py
- **功能颗粒**: ONNX embedding engine, `_embed_single`, `_cosine_sim`, `_extract_keywords`, intent classification
- **新模块覆盖**: `search.py` 已覆盖 ✅（但 search.py 与 search/ 包同名导致 shadowing）
- **消费者**:
  - `__init__.py`: `from .search.embed import _embed_single, _cosine_sim` + importlib fallback ✅ 已修复
  - `hooks/lifecycle.py`: `from ..search.embed import _is_explicit_memory_intent` + fallback ✅ 已修复
  - `reflection/engine.py`: 6 个符号 + fallback ✅ 已修复
  - `tools/handlers.py`: `_extract_keywords` + fallback ✅ 已修复
- **verdict**: 🔶 **NEEDS_MIGRATION**
- **行动**: 删除 `search/` 目录后，`search.py` shadowing 问题自动消失；consumers 的 importlib fallback 可简化

---

### Wave 4: reflection/ 目录（1 个模块）

#### 6. reflection/engine.py
- **功能颗粒**: ~1,700 行完整反射管道（micro/full/embedding/embedding_micro + skill + profile）
- **新模块覆盖**: `reflect.py` (~403 行) 覆盖核心功能，但 skill approval/rejection、profile compilation 未迁移 ❌
- **消费者**:
  - `__init__.py`: `from .reflection.engine import *` (star import)
  - `tools/handlers.py`: 7 个符号 (`_append_reflect_log`, `_recent_reflect_outcomes`, etc.)
  - `hooks/lifecycle.py`: 8 个符号 (`_run_full_reflection`, `_run_micro_reflection`, etc.)
  - `scripts/smoke_host_contract.py`: 3 个符号 (`_build_audit_entry`, `_append_reflect_log`, `_recent_reflect_outcomes`)
- **verdict**: 🔴 **NEEDS_MIGRATION**（最大工作量）
- **行动**:
  1. 将 skill approval/rejection 逻辑迁移到 `reflect.py`
  2. 将 profile compilation 逻辑迁移到 `reflect.py`
  3. 更新 `__init__.py`, `tools/handlers.py`, `hooks/lifecycle.py` 的 import
  4. 更新 `scripts/smoke_host_contract.py`

---

### Wave 5: hooks/ + tools/ 目录（2 个模块）

#### 12. hooks/lifecycle.py
- **功能颗粒**: Session hooks (`on_session_start`, `on_session_end`, `pre_llm_call`, `post_tool_call`), graph manager singleton, slash commands
- **新模块覆盖**: 无 — `context.py` 只替代了 context 装配部分，hooks 未迁移 ❌
- **消费者**:
  - `__init__.py`: `from .hooks.lifecycle import *` (star import)
  - `tools/handlers.py`: `from ..hooks.lifecycle import _on_session_start, ...` (line 980)
  - `scripts/smoke_host_contract.py`: `from mem_reflection_hermes.hooks.lifecycle import _pre_llm_call`
- **verdict**: 🔴 **NEEDS_MIGRATION**
- **行动**: 创建新的 `hooks.py` 运行时模块，或合并到 `__init__.py`

#### 13. tools/handlers.py
- **功能颗粒**: 12 个 SRH tool handlers, zone rebalancing, profile compilation
- **新模块覆盖**: 无 — 新模块没有 tool handler 实现 ❌
- **消费者**:
  - `__init__.py`: `from .tools.handlers import *` (star import)
  - `scripts/smoke_host_contract.py`: `from mem_reflection_hermes.tools.handlers import register`
- **verdict**: 🔴 **NEEDS_MIGRATION**
- **行动**: 创建新的 `tools.py` 运行时模块，或合并到 `__init__.py`

---

### Wave 6: core.py + late_binding.py（最后清理）

#### 1. core.py
- **功能颗粒**: MemoryStore, SkillStore, LoadedMemory, MemoryFrontmatter, Config, BM25, lineage helpers, zone helpers
- **新模块覆盖**: `store.py` 覆盖 MemoryStore/Frontmatter/Config；`search.py` 覆盖 BM25
- **消费者**: **所有 legacy 模块** + `__init__.py` 直接 import ~40 个符号
- **verdict**: 🔴 **最后删除**（所有其他 legacy 模块删除后才能删）
- **行动**:
  1. 让 `__init__.py` 从 `store.py` import 符号，逐步替换 `core.py` import
  2. 确认没有 legacy 模块引用后删除

#### 2. late_binding.py
- **功能颗粒**: `late_bind()` 运行时符号解析
- **新模块覆盖**: 无（新模块无循环导入）
- **消费者**: `tools/handlers.py`, `hooks/lifecycle.py`, `reflection/engine.py`
- **verdict**: 🟢 **SAFE_DELETE**（Wave 4/5 完成后）

---

## 无依赖可立即删除的颗粒

| 颗粒 | 说明 |
|------|------|
| `search/__init__.py` | 包标记，已被 `search.py` shadow |
| `reflection/__init__.py` | 包标记，无独立消费者 |
| `graph/__init__.py` | 包标记，已被 `graph.py` shadow |
| `query/__init__.py` | 包标记，无独立消费者 |

> ⚠️ 但删除这些包标记会破坏 `from .search.embed import ...` 的相对导入（在 fallback 代码中）。必须在 Wave 1-5 的 consumers 全部迁移后，一次性删除整个目录。

---

## 迁移优先级矩阵

```
Phase 1 (低 hanging fruit):
  - graph/pagerank.py → graph.py 内置 ✅
  - graph/cross_zone.py → graph.py 内置 ✅
  - graph/ahe_graph.py → graph/compat.py 已兼容 ✅
  - 更新 dashboard fallback + tests

Phase 2 (中等工作量):
  - query/cache.py → 提取 QUERY_TEMPLATES 到 search.py
  - graph/cluqi.py → 合并到 graph.py 或保留

Phase 3 (高工作量):
  - reflection/engine.py → reflect.py（skill + profile 逻辑迁移）
  - hooks/lifecycle.py → hooks.py（或合并到 __init__.py）
  - tools/handlers.py → tools.py（或合并到 __init__.py）

Phase 4 (最后):
  - core.py → store.py（符号迁移）
  - late_binding.py → 删除
```

---

## 消费者迁移检查清单

### scripts/ 验证脚本

| 脚本 | 引用的旧模块 | 迁移状态 |
|------|------------|---------|
| `scripts/check_v092.py` | graph.ahe_graph, graph.cluqi, graph.pagerank, graph.cross_zone, query.cache | ❌ 未开始 |
| `scripts/smoke_host_contract.py` | core, reflection.engine, tools.handlers, hooks.lifecycle | ❌ 未开始 |
| `scripts/migrate_memory_index.py` | 无（独立工具） | ✅ 无需迁移 |
| `scripts/bench_latency.py` | 动态 importlib | ⚠️ 需验证 |

### tests/ 测试文件

| 测试 | 引用的旧模块 | 迁移状态 |
|------|------------|---------|
| `test_graph_operations.py` | graph.ahe_graph, graph.pagerank | ❌ 未开始 |
| `test_wave3_retrieval.py` | graph.ahe_graph, graph.pagerank | ❌ 未开始 |
| `test_query_cache.py` | query.cache | ❌ 未开始 |
| `test_core_data.py` | core | ⚠️ 保留为回归测试 |
| `test_tool_handlers.py` | core | ⚠️ 保留为回归测试 |
| `conftest.py` | graph.ahe_graph | ❌ 未开始 |
