# Memory Recall Protocol（记忆召回协议）

> 本文件定义调用方与 Mems 之间关于「如何请求记忆、Mems 返回什么、为什么返回、返回结果具有什么可信度和证据」的标准协议。
>
> 每个协议要点标注实现状态：`（已实现）`、`（规划中）`、`（明确不做）`、`（待决策）`。

---

## 1. 定位与边界

### 1.1 协议，而不是 API

API 回答「怎么调用」：

```text
POST /v1/mems/query
```

协议回答「语义上约定了什么」：

```text
我要找什么？为什么找？当前是什么场景？
你返回哪些记忆？为什么认为它们相关？
它们的来源是什么？可信度多少？
```

MRP 是后者的规范。API 只是 MRP 的一种传输载体，未来任何语言的 Agent（Python、Java、Go、LangGraph、Claude Agent 等）都可以通过同一协议访问 Mems。

### 1.2 Mems 只提供记忆，不替 Agent 决策

Mems 可以给出 `relevance = 0.91`，但永远不说「这条记忆你必须使用」：

```text
Mems → 记忆候选 + 证据 + 可信度 → Agent → 决定是否进入上下文
```

原则：**Mems may recommend memories, but never owns the decision of whether those memories enter the Agent's context.**（已实现，与项目「不管理运行时状态」定位一致）

### 1.3 不接收运行时状态

Mems 不管理会话上下文、任务目标、活动计划、临时变量。以下字段**不允许**出现在 Recall Request 中：

```json
{
  "state": "...",
  "messages": "...",
  "system_prompt": "...",
  "current_plan": "...",
  "tools": "..."
}
```

职责边界：

| Mems 负责 | 调用方负责 |
| --- | --- |
| 存储与召回记忆 | Agent 运行时状态 |
| 返回记忆、证据、可信度 | Context Builder 组装 Prompt |
| 推荐候选记忆 | 决定使用哪些记忆 |
| 检索约束（如 `top_k`） | 上下文窗口与 token 预算 |

### 1.4 时间线原则（Time-Forward）

Mems 遵循时间线原则：已经发生的事情只向前推进，不破坏、不改写、不撤回。
对过去的记忆不做基于使用反馈的降权或修改——即使后来的使用情况不理想，也只在
更晚的时间点写入新的记忆，由 L2 版本链（`supersedes` + 冲突日志）向前演化，
原始记录始终保留。

---

## 2. 核心概念

### 2.1 Evidence（证据）

Evidence 是 MRP 的一级公民。L2 的结论必须能回溯到 L1 原始经历：

```text
Knowledge（L2）
   ↓
Evidence（source_l1_ids）
   ↓
Original Experience（L1 原文）
```

调用方可以按需决定证据深度：

```text
需要证据？ → No → 直接使用 L2
           → Yes → 回溯 L1 原文
```

实现状态：`source_l1_ids` 证据链路**已实现**；再蒸馏记录含 `l3_path` / `l3_line` 兜底指针**已实现**；「按需展开 L1 原文」的接口与归档 L1 的证据 fallback **规划中**。

### 2.2 Confidence（可信度）

L2 画像/事实自带 `confidence`（0-1）并参与排序；同时按验证时间衰减（`verification_decay`），长期未重新验证的信任度会下降。**已实现**。响应中把 confidence 作为独立字段返回**规划中**（只拆分 confidence，不拆分 recency——新鲜度已并入 score）。

### 2.3 Conflict（冲突）

蒸馏对账时，新旧知识冲突写入 `mems_l2_conflict_log`（`old_value` / `new_value` / `resolution` / `reason`），作为版本链与审计记录。**已实现**（蒸馏期）。召回响应不返回冲突标记**（明确不做）**：不把实时冲突推理或标记成本引入每次召回。

### 2.4 Feedback（反馈）

Agent 实际使用了哪些记忆确实是召回质量信号，但 Mems 遵循时间线原则（见 1.4）：
不因使用反馈降权、改写或撤回已发生的记忆。召回质量依靠可解释的排序因子保证，
是否使用候选记忆由调用方决定（见 1.2）。**明确不做。**

---

## 3. 召回请求（Recall Request）

### 3.1 目标形态

与当前 API 保持一致的身份边界：平级字段，`scope` 是软可见性标签字符串。

```json
{
  "query": "设计订单系统缓存方案",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "scope": "private",
  "memory_types": ["l1_episodic", "l2_fact", "l2_summary"],
  "top_k": 10,
  "time_range": null
}
```

### 3.2 字段说明

| 字段 | 说明 | 状态 |
| --- | --- | --- |
| `tenant_id` / `user_id` / `agent_id` | 硬身份边界（平级字段） | 已实现 |
| `scope` | 软可见性标签（字符串） | 已实现 |
| `query` | 检索文本（必填） | 已实现 |
| `memory_types` | 可选，限定召回来源（取值见 3.3）；缺省跨 L0/L1/L2 全召回 | 规划中 |
| `top_k` | 返回条数（默认 5，范围 1-100） | 已实现 |
| `time_range` | 可选时间过滤；仅用于显式触发 L3 归档召回 | 规划中 |

服务端会对 `query` 做关键词意图推断并作为排序加分（`intent_bonus`，**已实现**）。
这是内部机制，不暴露为请求字段；需要限定召回范围时使用 `memory_types`。

### 3.3 memory_types 映射

取值与响应 `source` 一致，不引入额外业务词表：

| memory_types | 存储来源 |
| --- | --- |
| `l0_hot` | L0 热缓存快照 |
| `l1_episodic` | L1 情景记忆 |
| `l2_profile` / `l2_fact` / `l2_event` / `l2_summary` | L2 蒸馏知识 |

### 3.4 明确不做什么

- 不接收运行时状态（见 1.3）。
- 不接受显式 `intent` 字段：意图推断是服务端内部排序机制，协议不暴露。
- **不接受 `max_tokens` 这类上下文预算字段**。边界：`top_k` 是召回约束（候选集大小），由 Mems 负责；`max_tokens` 是生成预算，由调用方负责。

---

## 4. 召回响应（Recall Response）

### 4.1 目标形态

```json
{
  "query": "用户主要用什么语言开发？",
  "results": [
    {
      "id": "mem-001",
      "source": "l2_profile",
      "content": "用户主要使用 Java",
      "score": 0.94,
      "confidence": 0.91,
      "source_l1_ids": [102, 231],
      "created_at": "2026-04-01T10:00:00Z",
      "tenant_id": "tenant-001",
      "user_id": "user-001",
      "scope": "private"
    }
  ],
  "total": 1
}
```

### 4.2 字段说明

| 字段 | 说明 | 状态 |
| --- | --- | --- |
| `id` | 稳定记忆标识（用于去重与引用；当前响应无此字段） | 规划中 |
| `source` | 来源类型（`l0_hot` / `l1_episodic` / `l2_profile` / `l2_fact` / `l2_event` / `l2_summary`） | 已实现 |
| `content` | 记忆文本 | 已实现 |
| `score` | 混合召回排序分（含词法/向量相似度、意图加分、新鲜度、验证衰减） | 已实现 |
| `confidence` | L2 可信度分项（profile/fact 有值，其余为 null；当前合并进 score） | 规划中 |
| `source_l1_ids` | 证据链路 | 已实现 |
| `created_at` / identity 字段 | 创建时间与身份上下文 | 已实现 |

### 4.3 排序因子可解释，但不下发 reason

「为什么召回」由内部可解释的排序因子组成：词法重叠、向量相似度、意图加分、
新鲜度、验证衰减。不用 LLM 生成解释——成本、延迟、不确定性都不划算；
也不把 `reason` 作为响应契约——内部机制可解释即可，不为每次召回承担契约成本。

### 4.4 Mems 不排序「绝对正确」

返回分数与证据，由调用方决定是否使用。见 1.2。

---

## 5. 检索流水线

Recall 不是简单的 vector search：

```text
query → embedding → vector search → top K
```

MRP 的流水线：

```text
query
   ↓
Query Understanding（关键词意图加分，内部机制，已实现）
   ↓
Memory Retrieval
   ├── L0（已实现）
   ├── L1（已实现）
   ├── L2（已实现）
   └── L3（仅显式 time_range 触发，规划中）
   ↓
Ranking
   ↓
Evidence Resolution
   ↓
Recall Result
```

说明：

- 当前在线召回覆盖 L0 热缓存、活跃 L1、L2，默认排除已归档 L1。
- **L3 召回规划中**：仅通过显式 `time_range` 触发，默认不搜归档；不做「在线召回不足时自动 fallback」，避免把归档扫描延迟引入每次召回。
- **Conflict 只发生在蒸馏期**（对账阶段写入 `conflict_log`）；召回期不做实时冲突推理，也不返回冲突标记。

---

## 6. 与现有实现的对照

| MRP 要点 | 现有实现 | 状态 |
| --- | --- | --- |
| 分层召回 L0/L1/L2 | `/v1/mems/query` 跨三层检索 | 已实现 |
| 分层召回 L3 | 在线召回排除已归档 L1 | 规划中（仅显式 `time_range`） |
| 意图加分 | `ranking.py` 关键词意图 + `intent_bonus`（内部机制） | 已实现 |
| 显式 `memory_types` 传参 | 无 | 规划中 |
| Evidence 链路 | `QueryResultItem.source_l1_ids`；再蒸馏 `l3_path` / `l3_line` | 已实现 |
| Confidence | L2 `confidence` + `verification_decay` | 已实现 |
| 响应 `confidence` 分项 | 合并进 `score` | 规划中 |
| 响应 `id` | 无稳定记忆标识 | 规划中 |
| Conflict 记录 | `mems_l2_conflict_log`（蒸馏对账期） | 已实现 |
| 响应冲突标记 | 无 | 明确不做 |
| 结构化 reason | 无（排序因子仅内部可解释） | 明确不做 |
| Feedback | 无（时间线原则，见 1.4） | 明确不做 |
| 不接收运行时状态 | 现有 Request 无 runtime 字段 | 已实现 |

---

## 7. 演进路线

### P0（近期）

- Request：可选 `memory_types` 过滤、`time_range`（显式 L3 召回）。
- Response：`id` 稳定记忆标识、`confidence` 分项（profile/fact）。

### P1（中期）

- Evidence 展开接口（按需取 L1 原文，而非每次全量返回）。

### 明确不做

- LLM 生成 reason。
- 召回期实时冲突推理与冲突标记。
- `max_tokens` 等上下文预算字段。
- 基于使用反馈的记忆降权 / 改写（时间线原则，见 1.4）。
- 显式 `intent` 请求字段（服务端关键词推断为内部机制）。
- 在线召回不足时的 L3 自动 fallback（L3 仅按显式 `time_range` 触发）。
