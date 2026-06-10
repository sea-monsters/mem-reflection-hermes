# v1.5 架构重构计划

> **日期**: 2026-06-10
> **基线**: v1.4-beta (413 tests, 13,372 LOC)
> **触发**: v1.4 六层级审查 L6-01/02/03/04/05 发现
> **参考**: mem0 v2.0.4 + hy-memory v0.3.6 + HeLa-Mem/MemForest 论文

---

## 目标

将 4 个长期架构债务项在 v1.5 中系统性解决，不破坏 413 个现有测试和 host 合约。

| 编号 | 问题 | 当前状态 | 目标 |
|------|------|---------|------|
| L6-01 | curator 复制粘贴 + 异常沉默 | 1,121 行, 19 except, 5 处 archive+delete | 可组合 Action pipeline |
| L6-03 | ImportError fallback 10 文件 | ~250 行重复 fallback 代码 | 统一 compat_loader 或消除 |
| L6-04 | `__init__.py` God Object | 618 行 | 仅 register() + 导入 |
| L6-05 | `core/store.py` 膨胀 | 2,349 行 | 拆分为 5 个职责模块 |

---

## 参考架构对比

### mem0 借鉴点

| 模式 | mem0 实现 | SRH 可借鉴 |
|------|----------|-----------|
| Factory 注册 | `mem0/<category>/__init__.py` provider dict | `_lb` 动态绑定 → 改为显式 Factory |
| Pydantic 配置 | `MemoryConfig(BaseModel)` | `core/config.py` 已有 typed config，需扩展到全量 |
| 三层存储分离 | VectorStore + GraphStore + HistoryStore | 当前单一 MemoryStore 可保持，但内部方法需分块 |
| 可选依赖组 | `pip install mem0[nlp]` | jieba/spaCy/tiktoken 应走可选依赖而非 fallback |
| 无 fallback import | 测试用 `sys.path` 配置 | 消除生产代码中的 importlib fallback |

### hy-memory 借鉴点

| 模式 | hy-memory 实现 | SRH 可借鉴 |
|------|---------------|-----------|
| Pipeline Manager | `MemoryPipelineManager` with L0→L1→L2→L3 | curator 5-phase 改为 Action pipeline |
| SerialQueue | concurrency=1 per pipeline stage | curator 已串行，但缺错误隔离和 checkpoint |
| Action 模式 | 每阶段有独立 `execute()` / error handler | 每个 curator phase 封装为 CuratorAction |
| stable/dynamic | context 注入 split for prompt cache | v1.4 ContextBundle 已实现，保持 |
| Warm-up + timer | 渐进式触发阈值 | curator 未来可加 idle-timeout 触发 |

---

## Sprint 1: L6-01 Curator Action Pipeline

### 当前问题

```
curator.py (1,121 行)
├── 5 处 archive+delete 复制粘贴 (仅错误消息不同)
├── 4 处 pinned/keep 检查 (完全相同)
├── 3 处 effectiveness 加载 (完全相同)
├── 19 个 except Exception 块 (8 pass / 7 warning / 4 continue)
└── docstring 仍描述 v1.2 的 "5-phase"
```

### 目标架构

```python
# memory/curator/
#   __init__.py      — _run_curator() 入口, 公开 API
#   actions.py       — CuratorAction 基类 + 6 个具体 Action
#   cold_store.py    — 冷存储读写 (从当前 curator.py 提取)
#   helpers.py       — _is_pinned_or_kept(), _build_cold_entry(), _load_last_access()
#   report.py        — generate_report()

class CuratorAction:
    """单个策展动作的基类。"""
    name: str

    def execute(self, ctx: CuratorContext) -> CuratorResult:
        """执行策展动作。返回结果和错误列表。"""
        raise NotImplementedError

    def should_run(self, ctx: CuratorContext) -> bool:
        """是否应该执行此动作。"""
        return True

@dataclass
class CuratorContext:
    mem_store: MemoryStore
    errors: List[str] = field(default_factory=list)

@dataclass
class CuratorResult:
    action_name: str
    archived: int = 0
    compacted: int = 0
    merged: int = 0
    orphan_edges: int = 0
    errors: List[str] = field(default_factory=list)
```

### 6 个具体 Action

| Action | 来源函数 | 行数估计 |
|--------|---------|---------|
| `ArchiveExpired` | `archive_expired()` | ~40 |
| `CompactChains` | `compact_superseded_chains()` | ~80 |
| `ArchiveSuperseded` | `archive_superseded()` | ~90 |
| `MergeSimilar` | `scan_for_similar()` + `merge_similar()` | ~120 |
| `CleanOrphanEdges` | `clean_orphan_edges()` | ~25 |
| `GenerateReport` | `generate_report()` | ~30 |

### 提取的 Helpers

```python
# helpers.py

def is_protected(fm: MemoryFrontmatter) -> bool:
    """pinned 或 keep/permanent 标签的记忆不参与策展。"""
    if fm.pinned:
        return True
    return bool(fm.tags and any(t in ("keep", "permanent") for t in fm.tags))

def build_cold_entry(mem, context_tag: str, **extra) -> dict:
    """构造标准冷存储条目。"""
    return {
        "id": mem.id(),
        "body": _refine_body(mem.body),
        "zone": mem.frontmatter.zone,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "tags": list(mem.frontmatter.tags or []) + ["archived", "cold", context_tag],
        "original_frontmatter": { ... },
        **extra,
    }

def archive_and_delete(mem_store, mem, entry: dict, context: str) -> Tuple[bool, Optional[str]]:
    """冷存储写入 + 主动删除 + 统一错误处理。返回 (success, error_msg)。"""
    if not _append_to_cold_store(mem_store, entry):
        return False, "cold store write failed"
    try:
        mem_store.delete(mem.scope, mem.id())
        return True, None
    except Exception as e:
        logger.warning(
            "Failed to delete %s after archiving (%s): %s. Cold entry preserved.",
            mem.id(), context, e,
        )
        return False, str(e)

def load_last_access(mem_store, mid: str) -> float:
    """加载最后访问时间，失败返回 0。"""
    try:
        eff = _load_effectiveness(mem_store, mid)
        return eff.get("last_accessed", 0) if eff else 0
    except Exception:
        return 0
```

### 实施步骤

1. 创建 `memory/curator/` 目录结构
2. 从 `curator.py` 提取 `cold_store.py` (cold store 读写, ~200 行)
3. 从 `curator.py` 提取 `helpers.py` (共享 helper, ~60 行)
4. 从 `curator.py` 提取 `report.py` (~40 行)
5. 创建 `actions.py`，实现 `CuratorAction` 基类 + 6 个具体 Action (~400 行)
6. 创建 `__init__.py`，实现 `_run_curator()` pipeline + 公开 API (~80 行)
7. 旧 `memory/curator.py` 变为 thin forwarder
8. 运行全量测试，确保 413 通过

### 预期效果

| 指标 | 改前 | 改后 |
|------|------|------|
| 文件行数 | 1,121 单文件 | 5 文件 ~780 行 |
| archive+delete 重复 | 5 处 | 1 处 (`archive_and_delete`) |
| pinned/keep 重复 | 4 处 | 1 处 (`is_protected`) |
| effectiveness 重复 | 3 处 | 1 处 (`load_last_access`) |
| except Exception | 19 个 | ~12 个 (Action 基类统一处理) |

---

## Sprint 2: L6-03 ImportError Fallback 统一

### 当前问题

10 个生产文件有 ~25 行 importlib fallback 块，总计 ~250 行重复代码。
根本原因：测试使用 `importlib.util.spec_from_file_location` 隔离加载模块，
导致生产代码需要 fallback 路径。

### 方案 A: 消除 fallback (推荐)

**核心思路**: 用 `pytest.ini` 的 `pythonpath` + `conftest.py` 的包注册解决测试导入，
完全移除生产代码中的 fallback。

```ini
# tests/pytest.ini
[pytest]
pythonpath = .
```

```python
# tests/conftest.py — 已有包注册，增强即可
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# 确保 mem_reflection_hermes 可正常导入
```

**步骤**:
1. 验证 `pythonpath = .` 使所有测试可以通过正常 relative import 加载模块
2. 逐文件移除 `except ImportError` fallback 块
3. 仅保留可选依赖的 try/except (jieba, spaCy, tiktoken) — 这些是合理的
4. 运行全量测试

**影响文件**: `core/search.py`, `core/graph.py`, `memory/context.py`, `reflection/engine.py`,
`runtime/graph.py`, `web/api.py`, `memory/bridge.py`

### 方案 B: 提取 compat_loader (备选)

如果方案 A 不可行（某些测试需要 standalone 加载），则提取统一工具：

```python
# core/_compat.py
import importlib.util, sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

def compat_import(module_name: str, file_path: str):
    """从文件系统加载模块，用于 standalone 测试场景。"""
    key = f"_compat_{module_name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, str(_REPO / file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod
```

**选择**: 优先方案 A，因为 mem0 和 hy-memory 都不使用 fallback import。
如果测试迁移有阻塞，降级为方案 B。

---

## Sprint 3: L6-04 `__init__.py` 拆分

### 当前问题

```
__init__.py (618 行)
├── ~150 行 imports (合理)
├── ~60 行 singleton getters (合理)
├── ~120 行 tool schemas (应在独立模块)
├── ~100 行 register() (合理但需精简)
├── ~40 行 helper functions (match_skills, load_zone_summary 等)
└── ~50 行 __all__ + aliases
```

### 目标架构

```
__init__.py (~250 行)         — 仅 imports + singleton getters + register()
runtime/schemas.py (~130 行)  — 12 个 tool schema 定义
```

### 实施步骤

1. 创建 `runtime/schemas.py`，移入所有 `_SRH_*_SCHEMA` 定义
2. `__init__.py` 改为 `from .runtime.schemas import *` 引用
3. `match_skills`, `load_zone_summary`, `save_zone_summary` 保留在 `__init__.py`
   （它们是 _lb 的解析目标，移开会破坏动态绑定）
4. 运行全量测试

### 预期效果

| 指标 | 改前 | 改后 |
|------|------|------|
| `__init__.py` 行数 | 618 | ~250 |
| schema 定义位置 | 内联 | `runtime/schemas.py` |
| register() 可读性 | 混合 | 清晰 |

---

## Sprint 4: L6-05 `core/store.py` 拆分

### 当前问题

```
core/store.py (2,349 行) — 号称 "leaf module" 但承载 7 种职责:
├── 配置管理 (~100 行): load_config, plugin_config, *_enabled, *_dir
├── 数据模型 (~180 行): MemoryFrontmatter, LoadedMemory, SkillFrontmatter 等
├── 文件 I/O (~200 行): write_memory_atomic, read_memory, async writer
├── MemoryStore (~500 行): SQLite 读写、索引管理
├── 实体提取 (~80 行): extract_entities, _normalize_entity_text
├── Token/搜索 (~250 行): _tokenise, estimate_tokens, _bm25_search_scored
└── 工具函数 (~40 行): fast_hash, normalize_zone, is_valid_zone
```

### 目标架构

```
core/
├── store.py (~600 行)         — MemoryStore + SkillStore + 文件 I/O + SQLite
├── models.py (~200 行)        — MemoryFrontmatter, LoadedMemory, SkillFrontmatter, parse/serialize
├── config.py (~100 行)        — 已存在，需从 store.py 迁入 plugin_config, load_config, *_enabled
├── entities.py (~100 行)      — extract_entities, _normalize_entity_text, entity_enabled
├── tokenization.py (~120 行)  — _tokenise, estimate_tokens, is_cjk, cjk_ratio, adaptive_conflict_threshold
├── utils.py (~50 行)          — fast_hash, normalize_zone, is_valid_zone, sanitize_zone_filename
├── search.py (981 行)         — 不变
├── graph.py (573 行)          — 不变
└── backend.py (29 行)         — 不变
```

### 实施步骤

1. 创建 `core/models.py`，移入 dataclass 定义 + parse/serialize frontmatter 函数
2. 创建 `core/entities.py`，移入 entity extraction 相关函数
3. 创建 `core/tokenization.py`，移入 token estimation + CJK 函数
4. 创建 `core/utils.py`，移入通用工具函数
5. 扩展 `core/config.py`，从 store.py 迁入 plugin_config, load_config, *_enabled 等
6. 精简 `core/store.py`，仅保留 MemoryStore/SkillStore + 文件 I/O
7. 更新 `__init__.py` 的 import 路径
8. 旧路径通过 re-export 保持兼容
9. 运行全量测试

### 兼容性策略

所有从 `core.store` 移出的符号在 `core/store.py` 保留 re-export：

```python
# core/store.py (拆分后)
from .models import MemoryFrontmatter, LoadedMemory, ...  # noqa: F401
from .entities import extract_entities, entity_enabled     # noqa: F401
from .tokenization import _tokenise, estimate_tokens       # noqa: F401
from .config import plugin_config, load_config             # noqa: F401
```

这确保所有外部 import (`from core.store import MemoryFrontmatter`) 不受影响。

### 预期效果

| 指标 | 改前 | 改后 |
|------|------|------|
| `core/store.py` 行数 | 2,349 | ~600 |
| 职责数 | 7 | 2 (MemoryStore + FileIO) |
| 新模块 | — | models(200) + entities(100) + tokenization(120) + utils(50) |
| 外部 import | 不变 | 不变 (re-export 兼容) |

---

## 实施顺序与依赖

```
Sprint 1 (L6-01 Curator)     ← 独立，可先行
    ↓
Sprint 2 (L6-03 Fallback)    ← 独立，但 Sprint 1 创建的 curator/ 目录需同步处理
    ↓
Sprint 3 (L6-04 __init__.py) ← 依赖 Sprint 2 的 import 清理
    ↓
Sprint 4 (L6-05 store.py)    ← 依赖 Sprint 3 的 __init__.py 稳定
```

每个 Sprint 结束后：
1. 运行 `pytest tests/ -v` 确认 413 通过
2. 运行 `python -m py_compile` 确认所有文件编译
3. 检查外部 import 兼容性

---

## 风险管理

| 风险 | 影响 | 缓解 |
|------|------|------|
| 拆分后 relative import 循环 | 编译失败 | 每步 py_compile + 逐步迁移 |
| 测试 standalone 加载失败 | 方案 A 不可行 | 降级为方案 B (compat_loader) |
| `_lb` 动态绑定断链 | 运行时 KeyError | 确保所有 _lb 目标仍在 `__init__.__dict__` |
| re-export 不完整 | 外部 import 失败 | 逐文件 grep `from .core.store import` |
| curator/ 目录导致旧 import 失败 | 测试失败 | 旧 curator.py 保留为 forwarder |

---

## 预期总效果

| 指标 | v1.4 当前 | v1.5 目标 |
|------|---------|----------|
| `core/store.py` | 2,349 行 | ~600 行 |
| `memory/curator.py` | 1,121 单文件 | 5 文件 ~780 行 |
| `__init__.py` | 618 行 | ~250 行 |
| ImportError fallback | 10 文件 ~250 行 | 0 (或 1 个 compat 模块 ~30 行) |
| archive+delete 重复 | 5 处 | 1 处 |
| pinned/keep 重复 | 4 处 | 1 处 |
| 总 LOC | 13,372 | ~12,800 (净减少 ~5%) |
| 可维护性 | 低 (God Object) | 高 (单一职责模块) |
