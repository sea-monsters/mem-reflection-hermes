## Why

v1.5 完成了模块重构和策展流水线，但记忆系统的运营级基础设施仍有两大缺口：

1. **无 durable event ledger** — `MemoryStore.update()` 原地修改文件，无法追溯变更历史（何时、何人、改了什么）。参考 mem0 的 history 表，这是 multi-agent 和审计场景的基础设施。
2. **无 scoped filters** — `scope=user|project` 加上 `zone` 不足以支撑同一项目内多 agent 实例、多 run/session 的隔离。参考 mem0 的 `user_id`/`agent_id`/`run_id` 合约。

两项功能均基于现有 SQLite 架构，无外部依赖，且为后续高级功能（审计 UI、导出、context offload）奠定基础。

## What Changes

### New Capabilities

- **Memory Event Ledger**: 新增 `memory_events` SQLite 表，记录 ADD/UPDATE/DELETE/SUPERSEDE/PIN/UNPIN 事件，包含 old/new body、old/new frontmatter、session_id、actor_id。扩展 `srh_memory_history` 返回事件链。
- **Scoped Memory Filters**: 扩展 `MemoryFrontmatter` 和 SQLite `memories` 表，新增 `user_id`/`agent_id`/`run_id` 可选字段。`srh_memory_search`/`write`/`delete` 支持 filters 参数。NULL scope 对所有请求可见（向后兼容）。

### Modified Behaviors

- `MemoryStore.update()` — 写入文件前同步记录 UPDATE 事件
- `srh_memory_write` handler — add/supersede 时记录 ADD/SUPERSEDE 事件
- `srh_memory_delete` handler — 删除前记录 DELETE 事件
- `SearchIndex.search()` — 在 scoring 前应用 scope filters
- `srh_memory_history` — 扩展 `include_events` 参数返回事件链

### Non-Changes (Explicitly Out of Scope)

- Pluggable vector store — 需要真实 remote backend 需求
- Typed temporal fact graph — 独立架构轨道，延后到 W4
- Context offload / Mermaid refs — 引入新 storage 子系统，延后到 W3
- Import/export competing-tool paths — 运营功能，不影响核心记忆

## Capabilities

### New Capabilities
- `memory-event-ledger`: 记忆生命周期事件记录与查询
- `scoped-memory-filters`: user_id/agent_id/run_id 作用域隔离

### Modified Capabilities
- `srh-memory-history`: 从仅 supersedes 链扩展为支持事件链查询

## Impact

| 文件 | 变更 |
|---|---|
| `core/store.py` | 新增 `memory_events` 表 + scoped columns |
| `core/models.py` | `MemoryFrontmatter` 新增 `user_id`/`agent_id`/`run_id` |
| `core/search.py` | `search()` 增加 `filters` 参数 |
| `runtime/schemas.py` | 扩展 write/search/delete/history schemas |
| `runtime/tools.py` | 事件记录 + filter 透传 + history 扩展 |
| `runtime/hooks.py` | session_id / actor_id 注入 |
| `docs/TOOLS.md` | 工具参数更新 |
| `tests/test_memory_events.py` | 新增 (≥25 tests) |
| `tests/test_scope_filters.py` | 新增 (≥20 tests) |
| `docs/CHANGELOG.md` | v1.6 变更记录 |
| `docs/DATA_SAFETY.md` | 事件存储和隔离说明 |
