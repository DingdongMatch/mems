# `POST /v1/mems/query` — 记忆召回

跨 L0 热快照、活跃 L1 情景记录与 L2 蒸馏知识召回记忆。调用方自行负责从结果
中组装上下文。

## 当前检索信号

- L0 热记忆快照（与快照内容的词法重叠）
- 基于 Qdrant 向量检索的 L1 情景召回
- 基于 Qdrant 向量检索的 L2 摘要召回
- 基于结构化数据库查询的 L2 画像、事实、事件召回
- 意图加权、新鲜度与验证衰减加分

## 当前实现说明

L1 情景记录与 L2 摘要通过 Qdrant 向量检索召回。画像、事实、事件通过结构化
数据库查询排序。L0 快照按与当前快照内容的词法重叠打分。

## 响应字段

- `source`：记忆来源（`l0_hot`、`l1_episodic`、`l2_profile`、`l2_fact`、`l2_event`、`l2_summary`）
- `content`：记忆文本
- `score`：相关度分数
- `created_at`：来源记录的创建时间（L0 无此字段）
- `source_l1_ids`：可追溯的 L1 记录 id，用于证据溯源
- `session_id`：可选来源会话（仅 L0/L1）
- `tenant_id` / `user_id` / `scope`：身份上下文
