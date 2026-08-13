# Memory Recall Protocol（记忆召回协议）

> 本文件定义调用方与 Mems 之间关于「如何请求记忆、Mems 返回什么、为什么返回、返回结果具有什么可信度和证据」的标准协议。
>
> 每个协议要点标注实现状态：`（已实现）`、`（规划中）`、`（待决策）`。

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
它们的来源是什么？可信度多少？有没有冲突？
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

---

## 2. 核心概念

### 2.1 Recall Intent（召回意图）

「我要什么记忆」≠「我的当前问题是什么」。

例如用户说「帮我设计一个缓存方案」，真正需要的可能是：用户过去的技术偏好、项目背景、历史架构决策、过去踩过的坑。因此请求应携带意图，而不是只给一个 query。

| Intent | 含义 | 主要召回目标 |
| --- | --- | --- |
| `task_support` | 当前任务需要什么历史信息 | 项目历史、架构决策、技术偏好、相关经验 |
| `user_preference` | 用户有什么偏好 | 语言、工具、交互习惯、技术偏好 |
| `historical_experience` | 过去有没有类似经历 | 过去经历、解决方案、结果、失败经验 |
| `knowledge` | 有没有已沉淀的知识 | L2 语义知识 |
| `decision_context` | 为什么之前做了某个决定 | 历史决策、决策原因、相关证据 |

实现状态：服务端基于关键词推断意图（profile / fact / event / summary）**已实现**；调用方显式传 `intent` **规划中**（显式优先、推断兜底，见 3.2）。

### 2.2 Evidence（证据）

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

### 2.3 Confidence（可信度）

L2 画像/事实自带 `confidence`（0-1）并参与排序；同时按验证时间衰减（`verification_decay`），长期未重新验证的信任度会下降。**已实现**。响应中把 confidence 作为独立字段返回**规划中**。

### 2.4 Conflict（冲突）

蒸馏对账时，新旧知识冲突写入 `mems_l2_conflict_log`（`old_value` / `new_value` / `resolution` / `reason`）。**已实现**（蒸馏期）。召回响应中返回冲突标记**规划中**。

### 2.5 Feedback（反馈）

Agent 实际使用了哪些记忆，是召回质量最重要的信号。反馈闭环见第 6 节。**未实现**。

---

## 3. 召回请求（Recall Request）

### 3.1 目标形态

```json
{
  "query": "设计订单系统缓存方案",
  "intent": {
    "type": "task_support",
    "purpose": "architecture_design"
  },
  "scope": {
    "tenant_id": "tenant-001",
    "user_id": "user-001",
    "agent_id": "agent-001"
  },
  "constraints": {
    "top_k": 10,
    "time_range": null
  }
}
```

### 3.2 字段说明

| 字段 | 说明 | 状态 |
| --- | --- | --- |
| `tenant_id` / `user_id` / `agent_id` / `scope` | 硬身份边界 + 软可见性标签 | 已实现 |
| `query` | 检索文本（必填） | 已实现 |
| `intent` | 显式召回意图；缺省时服务端关键词推断兜底 | 规划中 |
| `memory_types` | 只召回指定业务类型的记忆（映射见 3.3） | 规划中 |
| `top_k` | 返回条数（默认 5，范围 1-100） | 已实现 |
| `time_range` | 时间范围过滤；可用于触发已归档 L3 召回 | 规划中 |

### 3.3 memory_types 映射

业务视角的类型需要映射到存储来源：

| memory_types | 存储来源 |
| --- | --- |
| `user_preference` | `l2_profile` + 相关 `l1_episodic` |
| `historical_experience` | `l1_episodic` + `l2_event` |
| `project_context` | `l2_fact` + `l2_summary` |
| `semantic_knowledge` | `l2_fact` / `l2_event` / `l2_summary` |

### 3.4 明确不做什么

- 不接收运行时状态（见 1.3）。
- **不接受 `max_tokens` 这类上下文预算字段**：token 预算属于调用方，Mems 不参与 Context Builder。

---

## 4. 召回响应（Recall Response）

### 4.1 目标形态

```json
{
  "query": "用户主要用什么语言开发？",
  "memories": [
    {
      "id": "mem-001",
      "type": "semantic",
      "content": "用户主要使用 Java",
      "score": 0.94,
      "confidence": 0.91,
      "recency": 0.72,
      "source": "l2_profile",
      "evidence": ["l1-102", "l1-231"],
      "conflict": false,
      "reason": {
        "matched_intents": ["user_preference", "architecture_design"],
        "lexical_overlap": 0.55,
        "verification_age_days": 12
      }
    }
  ]
}
```

### 4.2 字段说明

| 字段 | 说明 | 状态 |
| --- | --- | --- |
| `id` | 记忆标识 | 规划中 |
| `type` / `source` | 来源类型（`l1_episodic` / `l2_profile` / `l2_fact` / `l2_event` / `l2_summary`） | source 已实现 |
| `content` | 记忆文本 | 已实现 |
| `score` | 混合召回排序分 | 已实现 |
| `confidence` / `recency` | 可信度与新鲜度分项（当前合并进 score） | 规划中 |
| `evidence` / `source_l1_ids` | 证据链路 | 已实现 |
| `conflict` | 该记忆是否存在活跃冲突记录 | 规划中 |
| `reason` | 结构化召回理由 | 规划中 |
| `created_at` / identity 字段 | 创建时间与身份上下文 | 已实现 |

### 4.3 reason 用结构化因子，不用 LLM 生成

「为什么召回」应由可解释的排序因子组成：`matched_intents`、`lexical_overlap`、向量相似度、新鲜度、验证衰减。不用 LLM 生成解释——成本、延迟、不确定性都不划算。

### 4.4 Mems 不排序「绝对正确」

返回分数与理由，由调用方决定是否使用。见 1.2。

---

## 5. 检索流水线

Recall 不是简单的 vector search：

```text
query → embedding → vector search → top K
```

MRP 的流水线：

```text
Recall Intent
   ↓
Query Understanding
   ↓
Memory Retrieval
   ├── L0（已实现）
   ├── L1（已实现）
   ├── L2（已实现）
   └── L3（规划中）
   ↓
Ranking
   ↓
Conflict Detection（蒸馏期已实现；召回期仅标记，规划中）
   ↓
Evidence Resolution
   ↓
Recall Result
```

说明：

- 当前在线召回覆盖 L0 热缓存、活跃 L1、L2，默认排除已归档 L1。
- **L3 召回规划中**：通过显式 `time_range` 或在线召回不足时 fallback 触发，默认不搜归档。
- **Conflict Detection 发生在蒸馏期**（对账阶段写入 `conflict_log`）；召回期不做实时冲突推理，只返回「是否存在活跃冲突」的标记，避免把推理成本引入每次召回。

---

## 6. 反馈回路（Feedback Loop）

### 6.1 为什么需要

Mems 返回 A、B、C，Agent 最终只用了 B。Mems 需要知道「B 有用、A/C 未被使用」，才能持续优化召回相关性，形成记忆检索的自我进化闭环。

### 6.2 反馈协议（规划中）

```text
POST /v1/mems/feedback
```

```json
{
  "recall_id": "recall-001",
  "agent_id": "agent-001",
  "usages": [
    { "memory_id": "mem-001", "usage": "used" },
    { "memory_id": "mem-002", "usage": "redundant" },
    { "memory_id": "mem-003", "usage": "not_used" }
  ]
}
```

分档语义：

| usage | 含义 |
| --- | --- |
| `used` | 实际进入上下文或被回答引用 → 正向信号 |
| `redundant` | 与已有信息重复，未造成新增价值 → 中性信号 |
| `not_used` | 未使用 → 弱负向信号 |

### 6.3 偏置与对策

「未使用」不等于「不相关」：可能是上下文装不下、信息重复、或调用方策略过滤。直接降 relevance 会产生偏置。

对策：

- 反馈分档降低误判（`redundant` 与 `not_used` 分开）。
- 用隐式信号补充：后续 query 是否实际引用了该记忆内容。
- 多次 `not_used` 才衰减，单次不下调。

### 6.4 闭环

```text
              Agent
                │ Recall Request
                ▼
              Mems
                │ Retrieve → Rank → Ground
                ▼
          Recall Response
                │
                ▼
          Agent Uses Memory
                │
                ▼
              Feedback
                │
                ▼
          Mems learns（规划中）
```

---

## 7. 与现有实现的对照

| MRP 要点 | 现有实现 | 状态 |
| --- | --- | --- |
| 分层召回 L0/L1/L2 | `/v1/mems/query` 跨三层检索 | 已实现 |
| 分层召回 L3 | 在线召回排除已归档 L1 | 规划中 |
| Intent 推断 | `ranking.py` 关键词意图 + `intent_bonus` | 已实现 |
| 显式 `intent` 传参 | 无 | 规划中 |
| Evidence 链路 | `QueryResultItem.source_l1_ids`；再蒸馏 `l3_path` / `l3_line` | 已实现 |
| Confidence | L2 `confidence` + `verification_decay` | 已实现 |
| 响应分项 confidence / recency | 合并进 `score` | 规划中 |
| Conflict 记录 | `mems_l2_conflict_log`（蒸馏对账期） | 已实现 |
| 响应冲突标记 | 无 | 规划中 |
| 结构化 reason | 无（排序因子已存在） | 规划中 |
| Feedback | 无 | 规划中 |
| 不接收运行时状态 | 现有 Request 无 runtime 字段 | 已实现 |

---

## 8. 演进路线

### P0（近期）

- Request：可选 `intent`（显式优先、推断兜底）、`memory_types` 过滤、`time_range`。
- Response：`reason` 结构化字段、`conflict` 标记、`confidence` / `recency` 分项。

### P1（中期）

- `POST /v1/mems/feedback` 最小反馈回路。
- Evidence 展开接口（按需取 L1 原文，而非每次全量返回）。

### 明确不做

- LLM 生成 reason。
- 召回期实时冲突推理。
- `max_tokens` 等上下文预算字段。
