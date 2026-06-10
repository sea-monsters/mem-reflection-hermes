# Sprint 1 测试有效性审查报告

**审查日期**: 2026-06-10
**审查对象**: `tests/test_curator_pipeline.py` 对 `docs/design/1.5/sprint1-curator-pipeline-sdd.md` 的覆盖
**结论**: **未完全覆盖功能意图**。33 个测试中约 14 个有效对齐意图，6 个存在断言缺陷，13 个测试位点缺失。

---

## 1. 覆盖情况逐项审查

### 1.1 数据结构测试（TestCuratorContext / TestCuratorResult）—— 合格

- `CuratorContext` 持有 `mem_store` ✓
- `errors` 默认空列表 ✓
- `CuratorResult` 各计数字段默认 0 ✓
- 这些测试准确对应 SDD §5.2 的契约。

### 1.2 Helper 测试 —— 基本合格，但有缺口

| Helper | 测试项 | 状态 | 缺口 |
|--------|--------|------|------|
| `is_protected` | 4 项（pinned、keep、permanent、无保护） | ✓ | 缺少 `None` tags、空 tags |
| `load_last_access` | 3 项（成功、缺失、异常） | ✓ | 缺少返回类型为 float 的验证 |
| `build_cold_entry` | 2 项（必要字段、context_tag） | △ | **未测 `**extra` 透传、`original_frontmatter` 字段完整性、`_refine_body` 集成** |
| `archive_and_delete` | 2 项（成功、失败） | △ | 未测 delete 失败但 cold 已写入的分支；冷存储失败的断言依赖 null-byte 路径是平台 workaround，不是意图本身 |

### 1.3 Action 独立测试 —— 严重不足

| Action | 测试数 | 状态 | 关键缺失 |
|--------|--------|------|----------|
| `ArchiveStale` | 3 | △ | 未测 `valid_until` 解析失败回退、空 store、仅 effectiveness 触发 stale、混合 fresh/stale 的正确计数 |
| `CompactChains` | 2 | △ | 未测 chain < `min_chain` 被跳过、intermediate 被保护、recent access 保护、update head 失败、循环保护 |
| `ArchiveSuperseded` | 2 | △ | 未测 depth < 3 被跳过、protected 节点跳过、recent access 保护 |
| `MergeSimilar` | 2 | △ | 未测 score < `merge_threshold` 不合并、identical body 的归档分支、`llm_merge` 配置路径、空/单 memory store、is_superseded 跳过 |
| `CleanOrphanEdges` | 2 | △ | **仅测了 graph manager 不可用的 0 分支，未测真正的 orphan edge 清理路径** |
| `GenerateReport` | **1** | ✗ | **只有 name 测试。未测报告文本生成、errors 包含、空结果输出 "No curator actions"、`report_text` side-channel** |

### 1.4 Pipeline Orchestration 测试 —— 关键断言失效

#### `test_compact_runs_before_archive`（line 518）—— **伪断言**

当前断言：
```python
assert "compacted" in result
assert "superseded" in result
```

问题：`_run_curator` 的返回字典**始终包含**这两个键，无论 actions 是否执行或以何种顺序执行。该测试**无法验证 ordering 意图**。正确的测试应使用 spy/mock 记录 action 调用顺序，或构造一个只有特定顺序才能产生正确最终状态的 chain。

#### `test_action_failure_does_not_skip_later_actions`（line 535）—— **断言过弱**

当前断言：
```python
assert len(result.get("errors", [])) >= 1
assert "compacted" in result
...
```

问题：
1. `>= 1` 不保证错误来自 `ArchiveStale`。
2. 键存在不等于后续 actions 执行过——它们可能因 `should_run=False` 或空 store 而直接返回 0。
3. 没有验证 `CompactChains`、`ArchiveSuperseded`、`MergeSimilar`、`CleanOrphanEdges` 在 ArchiveStale 失败后**确实被调用并贡献了结果**。

### 1.5 Backward Compatibility 测试 —— 仅测导入，未测行为

3 个测试只验证：
- `from memory.curator import _run_curator` callable
- `from memory.curator import is_protected, build_cold_entry`
- `from memory.curator.actions import ArchiveStale, CompactChains, CuratorContext`

SDD §5.6 明确说明外部调用者（`runtime/hooks.py`、`tests/test_memory_curator.py`）继续从 `memory.curator` 导入。**测试只验证了符号存在，未验证 7 个 legacy wrapper 函数的行为**：
- `scan_for_stale()` 返回列表
- `archive_expired(mem_store, ids)` 返回正确计数
- `archive_superseded()` 委托 `ArchiveSuperseded`
- `compact_superseded_chains()` 委托 `CompactChains`
- `scan_for_similar()` 委托 `MergeSimilar._scan_for_similar`
- `merge_similar()` 委托 `MergeSimilar`
- `clean_orphan_edges()` 委托 `CleanOrphanEdges`

### 1.6 Cold Store 测试 —— 完全缺失

SDD §5.1 将 cold store 提取为独立模块 `cold_store.py`，包含 `_load_cold_store`、`_append_to_cold_store`、`_prune_cold_store`、`_restore_from_cold`。`tests/test_curator_pipeline.py` **没有任何直接测试**。当前仅通过 `archive_and_delete` 间接触及 append。

### 1.7 Config / `should_run` 测试 —— 完全缺失

- `_curator_config` 默认值合并未测
- `_curator_enabled` 开关未测
- 单个 action 的 `should_run` 机制未测（所有测试都隐式假设为 True）
- `ArchiveStale` 的 `stale_days`、`effectiveness_threshold` 配置驱动未测
- `MergeSimilar` 的 `similarity.enabled=False` 跳过路径未测
- `CompactChains` 的 `compact_min_chain`、`protect_days` 配置驱动未测

### 1.8 聚合与报告持久化测试 —— 完全缺失

- `_run_curator` 的 `total_archived` 计算逻辑未测
- 多 action 同时产生计数时的聚合正确性未测
- `_persist_report` 写入 JSON 文件未测
- report 路径推导、失败回退未测

---

## 2. 与 SDD 验收准则的对照

| SDD AC | 要求 | 测试覆盖 |
|--------|------|----------|
| AC1 | `curator.py` → package | 结构完成，有 import 测试 |
| AC2 | 6 阶段行为与 v1.4 一致 | **✗ 无直接等价性测试**（未与 legacy 行为做 regression diff） |
| AC3 | `_run_curator` 返回字典语义一致 | △ 只验证了键存在 |
| AC4 | `archive+delete` 只出现一次 | ✗ 无结构检查/inspection 测试 |
| AC5 | `pinned/keep` guard 只出现一次 | ✗ 无结构检查/inspection 测试 |
| AC6 | `_load_effectiveness` wrapper 只出现一次 | ✗ 无结构检查/inspection 测试 |
| AC7 | 无 `except Exception: pass` | ✗ 无静态/inspection 测试 |
| AC8 | 导入兼容 | △ 只验证了符号存在 |
| AC9 | 413 旧测试通过 | 已验证 |
| AC10 | 每个 action 独立测试 | △ 数量及格，边界严重不足 |
| AC10 | Pipeline ordering | **✗ 当前测试不能验证 ordering** |
| AC10 | Error isolation | △ 测试存在，断言不能验证意图 |
| AC10 | Helper edge cases | △ 基本覆盖，缺少 cold_store 和 config |

---

## 3. 关键风险

1. **`GenerateReport` 仅有 name 测试**——若其实现产生错误报告文本或遗漏 errors，测试不会发现。
2. **Ordering 测试是 false positive**——未来若有人误调整 `_ACTION_CLASSES` 顺序，现有测试仍会通过。
3. **Error isolation 测试不能证明隔离**——一个未捕获的异常仍可能通过 "errors 长度 >=1" 这一弱断言。
4. **Legacy wrapper 行为未回归**——`runtime/hooks.py` 仍调用这些 wrapper，若 wrapper 委托错误，测试不会发现。
5. **Cold store 模块无直接测试**——容量裁剪、JSONL 解析容错、restore 路径均未覆盖。

---

## 4. 建议补齐的测试（优先级）

### P0 —— 阻塞发布

1. 重写 `test_compact_runs_before_archive`：mock action 调用顺序或使用状态依赖的 chain 证明顺序。
2. 重写/补强 `test_action_failure_does_not_skip_later_actions`：spy 验证后续 action 确实被调用，或验证它们的计数非零。
3. 为 `GenerateReport` 增加 3-4 个测试：空结果、errors 包含、`report_text` 字段、多 action 聚合。
4. 为 7 个 legacy wrapper 函数各增加行为测试。

### P1 —— 重要

5. `build_cold_entry` 的 `**extra` 透传和 `_refine_body` 集成。
6. `ArchiveStale` 的 `valid_until` 解析失败、effectiveness-only stale、空 store。
7. `CompactChains` 的 chain < min_chain、recent access 保护、protected intermediate。
8. `MergeSimilar` 的 below-threshold 不合并、identical body 分支。
9. `CleanOrphanEdges` 真实 orphan cleanup（需要 mock graph manager）。
10. `_persist_report` 的 JSON 写入和路径推导。

### P2 —— 建议

11. Cold store 直接测试：append、prune、restore、JSONL parse error skip。
12. Config 测试：curator disabled、`similarity.enabled=False`、自定义阈值。
13. `_run_curator` 的 `total_archived` 和多 error 聚合。
14. 结构检查测试：grep/AST 确认 `archive+delete`、`is_protected`、`_load_effectiveness` 各出现一次。

---

## 5. 数量估计

当前：33 个测试。
建议补齐后：约 **55-65 个测试** 才能完整覆盖 Sprint 1 的功能意图和边界条件。

---

## 6. 审查结论

`tests/test_curator_pipeline.py` **不能视为已冻结的有效测试集**。核心 orchestration 测试存在伪断言，多个 action 的边界条件和错误路径未覆盖，legacy 兼容层只测导入未测行为，cold store 模块完全缺失直接测试。

在继续 Sprint 2 之前，建议先对 P0 和 P1 测试进行补强并重新冻结。
