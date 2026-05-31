# OpenSpec — Supersedes + Graph Synergy (SDD v1.0)

## Motivation
当前 supersedes 链和 ahe_graph 是弱协同关系：
- Superseded 记忆在 flat file 层不可见，但在 graph 层仍是活跃节点（幽灵节点）
- BM25 搜索 + graph_expanded 是两阶段拼接，不是融合评分
- Palace Index 不感知 graph 内部聚类结构

## Scheme A — Supersedes 感知的图清理

### 修改点
1. `_post_tool_associate` hook — `srh_memory_write` 分支：
   - 当 args 包含 `supersedes: [old_id]` 时 → 在 graph 中将 old_id 的 importance * 0.1 (标记为过期)
   - 同时从 old_id 复制出站边到新记忆，权重 * 0.3 (知识迁移)
   
2. `_post_tool_associate` hook — `srh_memory_delete` 分支：
   - 标记被删除的 memory node 为 inactive (importance=0, strength=0)
   - 衰减与其相连的所有边 (weight *= 0.1)

### 验证
- 写一条记忆带 supersedes: [old] → 检查图节点 old 的 importance 降低
- 删除一条记忆 → 检查图节点 importance 归零

## Scheme B — 图增强的 Palace Index

### 修改点
1. `build_palace_index()` 在 zone 分组后额外查询图密度：
   - 对每个 zone，计算内部记忆之间的 graph edge 数量和平均权重
   - 高密度 (edges >= 2, avg_weight > 0.3) → 标注为 "密集关联簇"
   
2. Palace index 输出格式：在每个 zone 行后追加 `[关联簇: N个连接]`

### 验证
- 在 core zone 有 3 条关联记忆 → palace index 显示 `[关联簇: 2个连接]`

## Scheme C — 融合评分 (BM25 × Graph × Supersedes)

### 修改点
1. 新增 `_fusion_search()` 函数在 `MemoryStore` 上：
   ```
   final_score = 0.7 * bm25_score 
               + 0.3 * (graph_activation * edge_weight_max / (1 + supersedes_depth))
   ```
   其中：
   - bm25_score: 当前 BM25 结果需归一化到 [0,1]
   - graph_activation: 查询 token 命中的记忆在图中的 propagation 分数
   - supersedes_depth: 记忆被 supersede 的次数（链越长权重越低）
   
2. `_tool_srh_memory_search` 使用融合搜索替代纯 BM25 + graph_expanded 拼接：
   - 搜索返回按 final_score 排序的结果
   - `graph_expanded` 字段改为展示"图贡献度"而非重复数据

3. 向 `LoadedMemory` 中添加 `_supersedes_depth` 属性（通过递归解析 supersedes 链计算）

### 验证
- 有图关联的记忆排名高于无关联的同等 BM25 分数记忆
- 被 supersede 多次的记忆排名被压低
