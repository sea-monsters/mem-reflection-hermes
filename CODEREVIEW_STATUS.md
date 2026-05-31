# mem-reflection-hermes — 代码审查修复状态跟踪

**更新日期**: 2026-05-31
**版本**: v0.8.0

---

## P0 (严重) — 全部修复 ✅

| # | 问题 | 模块 | 修复 |
|---|------|------|------|
| - | register 未导出 | tools.py | __all__ 添加 "register" |
| - | _bm25_search_scored 签名不一致 | core.py | 返回类型统一为 List[Tuple[LoadedMemory, float]] |

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
| 20 | on_co_occurrence限制 | ahe_graph | ✅ FIXED | schema 限制 max 20 IDs |
| 23 | late-binding缓存 | hooks.py | ✅ FIXED | 新增 _lb() 缓存机制 |
| 25 | 微反思prompt | reflection.py | ⏭️ SKIP | 设计选择 |
| 26 | JSON解析 | reflection.py | ✅ FIXED | 已有 _repair_truncated_json |
| 33 | json default=str | tools.py | ✅ FIXED | 已添加 default=str |

---

## P2 (建议) — 仍 OPEN 🔴

| # | 问题 | 模块 | 优先级 | 说明 |
|---|------|------|--------|------|
| 12 | SkillStore 禁用列表 | __init__.py | 低 | 用户无法显式禁用项目技能 |
| 22 | pre_llm_call 阶段保护 | hooks.py | 中 | 阶段失败应静默跳过而非整体失败 |
| 27 | pending_skills 单文件 | reflection.py | 低 | 多批候选会互相覆盖 |
| 29 | reflect_log 无限增长 | reflection.py | 中 | 长期运行文件无限增长 |
| 30 | _get_mem_store 重复定义 | tools.py/hooks.py | 低 | 提取到公共模块 |
| 32 | compile_profile 快速路径 | tools.py | 低 | profile_mode 关闭时跳过 LLM 调用 |

---

## 代码量统计 (更新)

| 模块 | 当前行数 | 函数数 | 状态 |
|------|---------|--------|------|
| core.py | 790 | 49 | ✅ 零依赖叶节点 |
| embed.py | 458 | 19 | ✅ LRU 缓存已存在 |
| __init__.py | 1571 | 65 | ✅ 重构完成 |
| ahe_graph/__init__.py | 741 | 44 | ✅ 图记忆系统 |
| hooks.py | 324 | 21 | ✅ 生命周期钩子 |
| reflection.py | 1248 | 31 | ✅ 反射管线 |
| tools.py | 944 | 31 | ✅ 17 个工具 handler |
| **合计** | **~6076** | **~260** | |

---

## 修复批次总结

| 批次 | 内容 | 状态 |
|------|------|------|
| P0 | register 导出 + BM25 签名 | ✅ 完成 |
| P1 | fsync + cosine_sim + 截断 + delete + 别名 | ✅ 完成 |
| P2-A | debug日志 + 异常细化 + 导入缓存 | ✅ 完成 |
| P2-B | 意图原型配置化 | ✅ 完成 |
| P2-C | 文件轮转 + 代码重构 + 快速路径 | 🔴 待处理 (6项) |
