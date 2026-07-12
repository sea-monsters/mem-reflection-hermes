## Context

mem-reflection-hermes v1.5 完成了模块重构（`core/` 10 模块拆分）、策展流水线（`memory/curator/` 6 阶段 action）、runtime 包拆分（`runtime/` 7 模块）以及 schema/handler 对齐。测试集 511 passed，运营阻塞项已清除。

当前基线存在两个运营级基础设施缺口：

1. **Memory lifecycle is opaque** — `update()` 原地修改文件，`delete()` 直接移除，无 before/after 记录。mem0 通过 `history` 表实现完整审计链。
2. **Multi-tenancy is weak** — `scope=user|project` 过于粗粒度，无法隔离同一项目内的多 agent 或多 run。mem0 的 `user_id`/`agent_id`/`run_id` 是 hosted service 的标配。

两项功能均完全基于现有 SQLite 架构，不引入外部依赖，且为后续 W3（context offload）和 W4（typed fact graph）提供数据基础。

## Goals / Non-Goals

**Goals:**
- 为记忆操作建立 durable event ledger，支持全生命周期追溯
- 引入 `user_id`/`agent_id`/`run_id` 作用域过滤，实现多租户隔离
- 所有变更向后兼容：无 scope 时行为与 v1.5 完全一致
- 零外部依赖，继续使用 SQLite WAL 模式

**Non-Goals:**
- Pluggable vector store（需要真实 remote backend 需求）
- Typed temporal fact graph（独立架构轨道，延后 W4）
- Context offload / Mermaid refs（引入新 storage 子系统，延后 W3）
- Import/export competing-tool paths（运营功能）
- 分布式/多节点存储（超出单机插件范围）

## Decisions

### Decision 1: Event ledger 使用独立 SQLite 表而非文件日志

**选择**: 独立 `memory_events` 表，每事件一行。

**理由**:
- 与现有 SQLite 架构一致，无需新存储系统
- 可利用 SQL 索引高效查询（by memory_id, session_id, created_at）
- WAL 模式保证与 memory 写入的原子性
- 文件日志（如 JSONL）虽简单但查询和分析成本高

**替代方案**: JSONL 追加日志 — 拒绝，查询时需要全量扫描。

### Decision 2: Scope filters 在 scoring 前应用而非后过滤

**选择**: 在 `SearchIndex.search()` 的 BM25/embedding 阶段前，用 SQL `WHERE` 过滤候选集。

**理由**:
- 减少进入 scoring 的候选数量，提升性能
- 保持 ranking 语义不变：filter 不改变分数计算方式
- 与 mem0 的行为一致（filters 作为 query constraints）

**替代方案**: 先全量搜索再 client-side 过滤 — 拒绝，会导致 k 值不准确且性能差。

### Decision 3: NULL scope 对所有请求可见

**选择**: `user_id IS NULL` 的行匹配所有搜索请求。

**理由**:
- 向后兼容：v1.5 的记忆无 scope 字段，必须可被检索
- 渐进采用：用户可逐步为新增记忆添加 scope，不影响旧记忆
- 与 mem0 的语义不同（mem0 要求至少一个 scope），但我们作为插件不能破坏现有数据

**风险**: 可能意外暴露无 scope 的记忆给有 scope 的搜索 — 缓解：文档明确说明此行为，并推荐生产环境始终使用 scope。

### Decision 4: Scope 字段在 update 时不可变

**选择**: 更新记忆时忽略传入的 scope 字段变化，保留原始值。

**理由**:
- 防止 tenant escape：agent_a 不应能通过更新将记忆转移到 agent_b
- 如需迁移 scope，应使用 delete + recreate（产生审计事件）
- 简化并发控制：scope 是静态分区键

### Decision 5: Event frontmatter 过大时截断存储

**选择**: 设置 frontmatter JSON 大小上限（如 4KB），超限只保留关键字段。

**理由**:
- 防止极端情况（如 huge tags list）膨胀 events 表
- 关键审计字段（id, created, source, confidence, pinned, tags, zone, supersedes）必须保留
- body 本身有独立列存储，不受此限制

## Risks / Trade-offs

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| SQLite schema migration 失败 | 低 | 高 | `ALTER TABLE ADD COLUMN` 原生支持；启动时检测 schema 版本并自动迁移 |
| Event 记录影响写入性能 | 中 | 中 | 事件写入与文件写入在同 SQLite 事务内；benchmark 验证延迟增加 <5% |
| Scoped filters 破坏现有搜索排序 | 低 | 高 | filter 在 scoring 前应用，不改变排序逻辑；完整回归测试 |
| Frontmatter YAML 解析失败 | 低 | 中 | 新增字段为 optional；parser 忽略未知字段并 warn |
| Events 表无限增长 | 中 | 中 | curator 可扩展为定期 prune 旧事件（v1.7）；当前不设上限 |
| NULL scope 的隐式可见性导致数据泄漏 | 低 | 高 | 文档明确说明；生产环境推荐 always use scope；后续可选 `strict_scoping` 配置 |

## Migration Plan

### Schema Migration

```sql
-- Step 1: Add memory_events table (new stores and existing stores)
CREATE TABLE IF NOT EXISTS memory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    old_body TEXT,
    new_body TEXT,
    old_frontmatter TEXT,
    new_frontmatter TEXT,
    session_id TEXT,
    actor_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_events_memory_id ON memory_events(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_session_id ON memory_events(session_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_created_at ON memory_events(created_at);

-- Step 2: Add scope columns to memories (existing stores)
ALTER TABLE memories ADD COLUMN user_id TEXT;
ALTER TABLE memories ADD COLUMN agent_id TEXT;
ALTER TABLE memories ADD COLUMN run_id TEXT;
CREATE INDEX IF NOT EXISTS idx_memories_user_id ON memories(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_agent_id ON memories(agent_id);
CREATE INDEX IF NOT EXISTS idx_memories_run_id ON memories(run_id);
CREATE INDEX IF NOT EXISTS idx_memories_scoped ON memories(user_id, agent_id, run_id);
```

### Rollback

- `memory_events` 表删除不影响核心功能（仅丢失审计能力）
- Scope 列删除后，记忆仍可通过无 filter 搜索访问
- 建议备份 SQLite 文件后再执行迁移

## Open Questions

1. **Event retention policy**: 是否需要在 curator 中增加事件清理逻辑？建议 v1.7 处理。
2. **Session ID format**: 使用 host 提供的 session_id 还是内部生成 UUID？建议复用 hook 上下文中的 session_id。
3. **Actor ID granularity**: tool 名作为 actor 是否足够？是否需要区分 subagent？当前建议 tool 名即可。
