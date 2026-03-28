# 技术补充说明

项目的主叙事、公开 API 接入流、分层记忆解释和快速开始，现在统一放在 `README_zh.md` 中。

这个文件不再承担“大而全主文档”的角色，而是保留给更偏内部实现的补充说明。

## 如果你需要更深入了解，可以重点看这些

- embedding 模型与向量维度假设
- 调度器与自动化触发机制
- L1 / L2 / L3 的 SQLModel 结构
- 蒸馏、归档、监控等内部实现说明

## 当前保留的技术补充点

### Embedding

- 默认本地模型：`BAAI/bge-small-zh-v1.5`
- 可选使用 OpenAI embedding
- embedding 主要用于：
  - L1 情景记忆语义召回
  - L2 摘要向量召回

### 蒸馏流水线

当前长期记忆蒸馏流程为：

1. 过滤低信号或短期片段
2. 提取画像 / 事实 / 事件 / 摘要候选
3. 与已有 L2 做对账
4. 提交画像、事实、事件、摘要、冲突日志

### 存储分工

- Redis：短期 session context
- SQL：在线状态真相源、结构化元数据、证据链、对账与副本状态
- Qdrant：派生向量副本，用于语义检索
- JSONL：派生归档副本，用于长期保存与可移植性

### 副本一致性

- 在线写入优先提交 SQL 主记录
- 向量和 JSONL 同步以副本状态跟踪，不再作为主提交边界
- 默认搜索只面向活跃 L1；已归档 L1 不参与默认在线检索
- L3 仍是长周期归档层，后续会通过深度归档检索重新参与远期召回

### 调度默认值

- 蒸馏阈值：`100`
- 蒸馏时间：`02:00`
- 归档时间：`03:00`
- 归档天数：`30`

### 关键内部模块

- `src/mems/services/redis_service.py`
- `src/mems/services/l0_sync.py`
- `src/mems/services/vector_service.py`
- `src/mems/services/distill.py`
- `src/mems/services/archive.py`
- `src/mems/routers/memories.py`
- `src/mems/routers/simulator.py`
- `src/mems/routers/monitor.py`

## 推荐阅读顺序

1. `README_zh.md`
2. `README.md`（如需英文版本）
3. `AGENTS.md` 了解仓库开发约定
4. 再看本文件补充实现细节
