# Memory Recall Protocol（MRP）记忆召回协议

> **Memory Recall Protocol（MRP）定义调用方与 Mems 之间关于“如何请求记忆、Mems 返回什么、记忆具有什么语义、如何表达相关性、可信度和证据”的标准协议。**

> MRP 是**协议规范（Protocol Specification）**，而不是某个具体 API 的说明。HTTP API、SDK 或其他传输方式都只是 MRP 的实现载体。

每个协议要点标注实现状态：

- `（已实现）`：与当前代码行为一致
- `（规划中）`：属于 MRP 目标契约，当前 API 尚未提供
- `（明确不做）`：MRP 有意排除，不属于召回语义
- `（待决策）`：尚未定稿

当前 MRP 的 HTTP 传输实现是 `POST /v1/mems/query`；HTTP 层字段参考
[Query API](/zh/api/query)，身份与可见性字段的完整说明见
[Identity Model](/zh/integration/identity-model)。

## 1. 定位与边界（Positioning and Boundaries）

### 1.1 协议，而不是 API（Protocol, Not API）

API 解决“怎么调用”：

```http
POST /v1/mems/query
```

协议解决“这个调用在语义上约定了什么”：

```text
query 的语义是什么？
top_k 表示什么？
score 表示什么？
source_l1_ids 为什么存在？
confidence 与 score 有什么区别？
session_id 为什么不能作为 Recall Filter？
```

MRP 是语义契约（Semantic Contract），API 只是它的传输实现。未来不同语言、不同 Agent Runtime 都可以通过同一协议访问 Mems：

```text
Python Agent
Java Agent
Go Agent
LangGraph Agent
Claude Agent
Custom Agent Runtime
       │
       ▼
      MRP
       │
       ▼
     Mems
```

### 1.2 记忆，而不是上下文；候选，而不是决策

Mems 负责提供与当前请求相关的 **Memory Candidate**，并附上相关性、可信度与证据信号；Mems 不负责决定这些候选最终是否进入 LLM Context：

```text
Mems
  │
  │ Memory Candidates + Evidence + Confidence + Relevance
  ▼
Agent Runtime
  │
  │ decides
  ▼
Context
```

因此：

> **Mems 可以推荐记忆，但永远不拥有这些记忆是否进入 Agent Context 的最终决策权。**

（已实现：当前 `/v1/mems/query` 只返回统一结果模型中的候选记忆，是否使用由调用方自行组装。）

### 1.3 运行时状态与上下文归调用方

Agent Runtime 拥有 Runtime State。以下内容属于调用方，不属于 Mems：

```text
current_task
current_goal
current_plan
current_step
messages
tool_results
temporary_variables
```

因此 Recall Request 不接受完整 Runtime State，以下字段不得出现：

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

| Mems 负责                   | 调用方负责                        |
| --------------------------- | ------------------------------- |
| Memory Storage / Recall     | Agent Runtime State             |
| Memory Ranking              | Context Construction            |
| Evidence / Provenance       | Memory Selection                |
| Recall Constraints（如 `top_k`） | Prompt Construction          |
| Memory Lifecycle            | Context Window / Token Budget   |
| 推荐候选记忆                 | Agent Decision                  |

（已实现：请求 schema 包含 identity / `namespace` / `query` / `top_k`，服务端根据
identity 与路径段生成内部 namespace。详见
[Identity 与 Namespace](/zh/integration/identity-model)。）

### 1.4 与 Agent 无关（Agent-Agnostic）与 Memory Autonomy

Mems 不依赖具体 Agent Framework，MRP 不应要求调用方采用特定的 Agent State、Prompt Format、Planning Model、Tool Model 或 Framework。

Mems 管理 **Memory Lifecycle**，不管理 **Agent Decision Lifecycle**：

- Mems 可以提供 `relevance`、`score`、`confidence`、`evidence`、时间信息；
- Mems 不会告诉 Agent“你必须使用这条记忆”；
- 最终决策始终属于调用方。

### 1.5 时间向前原则（Time-Forward）

> **Memory evolves forward in time; historical records are not rewritten.**

已经发生的 Memory 不因后续使用情况而被降权、改写、撤回或删除。新的事实、验证结果或冲突在更晚的时间点形成新的 Memory，长期知识通过`supersedes` + `conflict log` + version chain 向前演化，原始记录始终保留。

（已实现：冲突对账时旧 Profile/Fact 标记为 `superseded` 并保留，新记录以`version + 1`、`supersedes_id` 指向旧记录。）

### 1.6 设计原则小结

MRP 的核心定位可以浓缩为四句话：

> **Mems provides memory, not context.**

> **Mems provides evidence, not truth.**

> **Mems provides candidates, not decisions.**

> **Mems owns memory lifecycle; Agent owns runtime lifecycle.**

## 2. 核心术语（Terminology）

| 术语 | 定义 |
| --- | --- |
| Mems | Memory Infrastructure，负责 Memory 的存储、召回与生命周期管理 |
| Agent Runtime | 调用 Mems 的 Agent 执行环境 |
| Recall | 根据请求从 Mems 获取相关 Memory |
| Recall Request | Agent Runtime 发起的 Memory 请求（见第 3 章） |
| Recall Response | Mems 返回的 Memory Candidate 集合（见第 4 章） |
| Memory Candidate | Mems 认为与请求相关的一条记忆结果 |
| Relevance | Memory 与当前 Query 的相关程度，综合体现在 `score` |
| Score | 本次召回内用于排序的综合分，组成随来源不同（见 4.3） |
| Confidence | Mems 对 Memory 内容可信度的判断（见 5.2） |
| Evidence | 支撑 Memory 的历史记录引用（如 `source_l1_ids`） |
| Provenance | Memory 的来源与演化链路（L2 → L1 → L3 指针） |
| Identity | 硬隔离身份边界（`tenant_id` / `user_id` / `agent_id`） |
| Namespace | Mems 根据经过验证的 identity 生成的不可变存储分区 |
| L0 / L1 / L2 / L3 | Hot / Episodic / Distilled Knowledge / Archive |

## 3. 召回请求（Recall Request）

### 3.1 当前 HTTP 形态与协议目标形态

当前实现的 HTTP 请求只包含已落地字段：

```json
{
  "query": "设计订单系统缓存方案",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "top_k": 10
}
```

MRP 的目标形态在以上字段之外增加 `memory_types` 与 `time_range`：

```json
{
  "query": "设计订单系统缓存方案",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "memory_types": ["l1_episodic", "l2_fact", "l2_summary"],
  "top_k": 10,
  "time_range": null
}
```

其中：

- `memory_types`：**（规划中）**，缺省行为是跨 L0/L1/L2 召回；
- `time_range`：**（规划中）**，仅用于显式触发 L3 归档召回；
- `session_id`：请求永远不接受（见 3.4）。

### 3.2 字段说明

| 字段 | 语义 | 状态 |
| --- | --- | --- |
| `tenant_id` / `user_id` / `agent_id` | 硬身份边界（平级字段，见 3.3） | 已实现 |
| `namespace` | 调用方业务上下文的可选通用路径段 | 已实现；缺省时使用 identity 的默认精确分区 |
| `MemoryNamespace` | 根据经过验证的 identity 与 `namespace` 生成的不可变存储分区 | 内部值；完整值不是请求字段 |
| `query` | 检索文本，必填且不能为空白 | 已实现 |
| `top_k` | 本次最多返回的候选条数（默认 5，范围 1-100） | 已实现 |
| `memory_types` | 可选来源过滤；缺省跨 L0/L1/L2 全召回 | 规划中 |
| `time_range` | 可选时间过滤；仅用于显式 L3 归档召回 | 规划中 |
| `session_id` | 不作为召回过滤字段（写入侧溯源） | 明确不做 |

`query` 用于语义 / 词法检索。服务端会对 Query 做关键词意图推断，并把结果作为
内部排序加分（`intent_bonus`，**已实现**）；该机制不暴露为请求字段。

### 3.3 身份与 Namespace（Identity & Namespace）

`tenant_id` / `user_id` / `agent_id` 是 Memory 的硬身份边界：

```text
Identity（tenant_id / user_id / agent_id）
    ↓
Hard Isolation
```

API 接受可选的 `namespace` 路径段（`string[]`），并根据它与经过验证的 identity
生成带版本的不可变内部 namespace：

```text
("v1", "tenant", <tenant_id>, "user", <user_id>, "agent", <agent_id>,
 "memories", ...<namespace>)
```

例如，`namespace: ["projects", "prj_abc123"]` 可选择一个项目分区；路径段对 Mems
是不透明值，应使用稳定标识。省略或传空列表时使用 identity 的默认分区，不会自动搜索
子路径。写入与查询必须使用相同路径。客户端不能设置完整内部 namespace。Namespace 是
存储分区标识，不是授权凭证。调用方应用负责认证调用者，
并授权 identity 与业务上下文。共享记忆还必须使用代表共享所有者的 identity。记忆类型和
检索属性仍作为记录字段，使一次召回可以跨越所选分区内的多个记忆类型。

当前实现的过滤语义：

- `agent_id` 必填，任何召回都按 agent 过滤；
- 完整 `MemoryNamespace` 包含 `tenant_id` 和 `user_id`；可选 identity 缺省时进入独立的
  空值分区，不会作为通配符；
- 多用户场景应传入目标 `user_id`（必要时还有 `tenant_id`），以访问对应身份分区；
- Mems 不预设业务上的 `Tenant → User → Agent` 层级树；namespace 是由平级身份字段和
  可选调用方上下文派生的规范存储路径，授权仍由调用方应用负责。

### 3.4 session_id 的边界

`session_id` 是写入侧的动作溯源：

- `/v1/mems/write` 接受 `session_id`，L0 快照按 session 保存，L1 主记录持久化该
  字段；
- Recall Request 不接受 `session_id`，它既不是召回过滤器，也不是隔离边界；
- Recall Response 中，L0 / L1 结果可以携带 `session_id`；L2 蒸馏知识不依赖
  `session_id`（L2 通过 `source_l1_ids` 回溯到原始 L1）。

（明确不做）

### 3.5 请求非目标（Request Non-Goals）

以下内容明确不属于 Recall Request。

**Runtime State**

```text
state
messages
system_prompt
current_plan
tools
```

（明确不做，见 1.3）

**Explicit Intent**

```json
{
  "intent": "architecture_design"
}
```

Query Intent 是 Mems 内部 Retrieval / Ranking 机制（关键词推断 + `intent_bonus`），
不是调用方必须理解的协议字段。

（明确不做）

**Context Token Budget**

```text
max_tokens
context_budget
prompt_budget
```

边界如下：

```text
top_k       → Memory Retrieval Constraint → Mems
max_tokens  → Generation / Context Budget  → Agent Runtime
```

（明确不做）

**session_id 作为召回过滤**

见 3.4。

（明确不做）

## 4. 召回响应（Recall Response）

### 4.1 目标形态

MRP 目标响应形态：

```json
{
  "query": "用户主要用什么语言开发？",
  "results": [
    {
      "id": "mem-001",
      "source": "l2_profile",
      "content": "like language Java",
      "score": 0.94,
      "confidence": 0.91,
      "source_l1_ids": [102, 231],
      "created_at": "2026-04-01T10:00:00Z",
      "tenant_id": "tenant-001",
      "user_id": "user-001"
    }
  ],
  "total": 1
}
```

其中 `id` 与 `confidence` 字段目前尚未下发（见 4.2）。当前
`/v1/mems/query` 的实际响应为：顶层 `query` / `results` / `total`；每条
`results` 项包含 `source`、`content`、`score`、`created_at`（可为空）、
`source_l1_ids`、`session_id`（可为空）以及 identity 上下文字段。

### 4.2 字段说明

| 字段 | 语义 | 状态 |
| --- | --- | --- |
| `query` | 回显本次请求的检索文本 | 已实现 |
| `results` | Memory Candidate 列表 | 已实现 |
| `id` | 稳定的 Memory Identifier（去重 / 引用 / 版本关联） | 规划中 |
| `source` | 来源类型（见下） | 已实现 |
| `content` | Memory 可读文本 | 已实现 |
| `score` | 本次召回内的综合排序分（见 4.3） | 已实现 |
| `confidence` | 内容可信度分项（Profile / Fact 当前参与排序但不单独下发；其余来源无此分项） | 规划中 |
| `source_l1_ids` | 证据链路（见 5.1） | 已实现 |
| `created_at` | 时间上下文，语义随来源不同（见 5.3） | 已实现 |
| `session_id` | 来源会话（仅 L0 / L1 有值） | 已实现 |
| `tenant_id` / `user_id` | 身份上下文 | 已实现 |
| `total` | 本次实际返回的 `results` 条数 | 已实现 |

`source` 取值与请求侧 `memory_types` 一一对应，不引入额外业务词表：

```text
l0_hot
l1_episodic
l2_profile
l2_fact
l2_event
l2_summary
```

`total` 的语义：

```text
total = len(results) <= top_k
```

`total` 不表示候选池总数、数据库命中总数或全局 Memory 总数。

### 4.3 score 的组成（排序因子）

`score` 是 Mems 用于 Recall Ranking 的综合排序分，表示：

> **该 Memory 在当前 Recall Request 下的排序价值。**

`score` 是排序语义，不是事实正确性，也不是概率。

各来源当前参与排序的因子：

| `source` | 因子 |
| --- | --- |
| `l0_hot` | 词法重叠（与 L0 快照文本） |
| `l1_episodic` | 向量相似度 + 词法重叠 + 意图加分 + 新鲜度 |
| `l2_profile` / `l2_fact` | confidence + 词法重叠（先乘验证衰减）+ 意图加分 + 新鲜度 |
| `l2_event` | importance + 词法重叠 + 意图加分 + 新鲜度 |
| `l2_summary` | 词法重叠 / 向量相似度（先乘验证衰减）+ 意图加分 + 新鲜度 |

当前实现注记（权重是内部实现细节，**不构成协议契约**，可随时调整）：

- 词法打分使用 CJK bigram + ASCII 整词；
- 各来源 base 分先归一化到 0-1，再叠加意图加分与新鲜度加分，因此最终 `score`
  可以大于 1；
- L0：`base = 0.7 × lexical_overlap`，且 `base > 0` 才返回；当前实现中 L0 没有
  意图加分、新鲜度加分，也没有 `created_at`；
- L1：`base = 0.6 × clip(vector) + 0.4 × lexical`，随后叠加意图与新鲜度；
- L2 Profile / Fact：`base = 0.7 × clip(confidence) + 0.3 × lexical`，乘以
  `verification_decay` 后叠加意图与新鲜度；
- L2 Event：`base = 0.6 × clip(importance / 10) + 0.4 × lexical`；
- L2 Summary：`base = max(lexical, 0.6 × clip(vector))`，乘以验证衰减后叠加意图
  与新鲜度；
- 同分结果按 `created_at` 倒序（确定性排序）；
- `score` 只在同一次 Recall 内部可比，跨请求、跨实现没有绝对数值含义。

### 4.4 不返回 reason（明确不做）

“为什么召回这条”由上述可解释因子组合决定，因子组合是 Mems 内部实现。Mems
不使用 LLM 为每条结果生成 `reason`——成本、延迟与不确定性都不划算，且内部因子
已经可以解释排序，不需要把它固化为每次召回的响应契约。

（明确不做）

## 5. 语义：证据、可信度与时间（Semantics）

### 5.1 Evidence 是一级概念

Evidence 是 MRP 的一级概念，尤其对 L2 Distilled Knowledge：

```text
L2 Knowledge
      │
      ▼
   Evidence（source_l1_ids）
      │
      ▼
 L1 Narrative
      │
      ▼
Original Experience
```

Evidence 回答“这条 Memory 从哪里来”，不等于“这条 Memory 一定正确”：

```text
Evidence   → Where did this come from?
Confidence → How trustworthy is this?
```

Recall 默认只返回 Evidence Reference（`source_l1_ids`），不展开原文；调用方可按
需进一步请求 L1 原文：

```text
Recall L2
   │
   ├── Need Evidence? No  → Use L2 directly
   └── Need Evidence? Yes → Expand L1（按需）
```

实现状态：

- `source_l1_ids`：**（已实现）**；
- 再蒸馏进度表中的 `l3_path` / `l3_line` 兜底证据指针：**（已实现）**；
- Evidence Expansion API（按需展开 L1 原文）：**（规划中）**。

注意：`l3_path` / `l3_line` 是再蒸馏（L3 → L2）侧的溯源指针，不通过当前
Recall Response 下发。

### 5.2 Confidence 与 Verification Decay

`confidence` 表示 Mems 对 Memory 内容可信度的判断，与 `score` 不等价。例如：

```text
Memory A: score = 0.95, confidence = 0.90
→ 与 Query 高度相关，且内容本身可信度较高

Memory B: score = 0.92, confidence = 0.40
→ 与 Query 相关，但 Memory 本身可信度较低
```

当前实现：

- L2 Profile / Fact 携带 `confidence`（0-1）与 `last_verified_at`，并参与排序；
- L2 Summary 没有独立的 `confidence` 字段，但排序时同样按 `last_verified_at`
  应用验证衰减；
- L2 Event 不携带 `confidence`，按 importance + 词法排序；
- 响应中把 `confidence` 作为独立字段下发：**（规划中）**——当前只折叠进
  `score`。

Verification Decay 的语义：

```text
长期未重新验证
    ↓
confidence 参与的基础分乘以衰减
    ↓
Recall 中的信任程度下降
```

（已实现）

### 5.3 时间语义（Temporal Context）

不同 Memory Source 的时间语义不同，响应中的时间字段必须结合 `source` 理解：

| Source | 时间含义 | 响应 `created_at` | 验证衰减依据 |
| --- | --- | --- | --- |
| L0 | 无固定创建时间，只有 `expires_at` | 无 | 无 |
| L1 | 创建 / 记录时间 | `created_at` | 无 |
| L2 Event | 事件记录时间 | `created_at` | 无 |
| L2 Summary | 创建时间，最近验证时间单独记录 | `created_at` | `last_verified_at` |
| L2 Profile / Fact | 最近验证时间 | `last_verified_at`（以 `created_at` 字段下发） | `last_verified_at` |

### 5.4 Conflict 与版本链

Conflict 主要发生在蒸馏 / 再蒸馏的知识对账阶段：

```text
Old L2: 用户偏好 Redis
      ↓
New Evidence
      ↓
New L2: 用户目前使用 PostgreSQL
```

对账结果写入 `mems_l2_conflict_log`，包括：

```text
memory_type
old_value
new_value
resolution
reason
source_l1_ids
```

（已实现）

同时通过版本链向前演化：

```text
旧记录 status → superseded（保留）
新记录 version = 旧 version + 1
新记录 supersedes_id → 旧记录 id
```

（已实现）

Recall 阶段：

- 不执行实时 Conflict Reasoning；
- 不在每次 Recall Response 中返回 `conflict` / `conflict_reason` /
  `resolution`——冲突已在蒸馏 / 再蒸馏阶段完成处理，召回期推理只会增加成本、
  延迟与不确定性。

（明确不做）

### 5.5 Feedback（明确不做）

Mems 不根据 Agent 的使用反馈修改已经发生的 Memory：

```text
Recall → Memory A → Agent ignored A
不会导致 A.score 下降、A 被改写或被删除
```

原因是 Time-Forward 原则（见 1.5）：Memory 是已经发生的历史记录；如果后续产生
新事实，应在新的时间点形成新 Memory，并通过版本链演化。因此 Recall Feedback
不作为 Memory Rewrite / Reweight 机制。

未来是否把 Feedback 用于 Retrieval Evaluation、Benchmark 或 Offline Ranking
Optimization，可以单独讨论，但不属于当前 MRP Memory Lifecycle。

（明确不做）

## 6. 检索语义与流水线（Retrieval）

### 6.1 Recall 不等于 Vector Search

Vector Search 解决“哪些向量相似”；Memory Recall 解决“哪些 Memory 对当前请求有
价值”。因此：

```text
Memory Recall
      │
      ├── Vector Retrieval
      ├── Lexical Retrieval
      ├── Temporal Filtering
      ├── Confidence / Verification Decay
      ├── Intent Bonus
      ├── Source-specific Ranking
      └── Evidence
```

Vector Search 只是 Recall 的一种底层能力。

### 6.2 当前检索 Pipeline

```text
Query
  │
  ▼
Query Understanding（关键词意图推断，内部机制，已实现）
  │
  ▼
Memory Retrieval
  │
  ├── L0 Hot：Redis 活跃快照 → 词法重叠（已实现）
  │
  ├── L1 Episodic：Qdrant 向量召回活跃记录（已实现）
  │
  ├── L2
  │      ├── Profile SQL 候选（已实现）
  │      ├── Fact SQL 候选（已实现）
  │      ├── Event SQL 候选（已实现）
  │      └── Summary Qdrant 向量召回（已实现）
  │
  └── L3：仅显式 time_range 触发（规划中）
  │
  ▼
Ranking（各来源因子组合 → Top K，内部实现）
  │
  ▼
Evidence Packaging（source_l1_ids）
  │
  ▼
Recall Result
```

说明：

- 在线召回覆盖 L0 热缓存、活跃（未归档）L1 与 L2；已归档 L1 默认排除；
- L2 结构化候选只做 SQL 候选集限制，最终相关度仍由统一排序决定；
- L0 只返回与 Query 有词法重叠的活跃快照，且不携带 `created_at`。

### 6.3 L3 与 time_range（规划中）

L3 Archive Recall 是显式行为：

- 仅当请求携带 `time_range` 时才扫描 L3 归档；
- 在线召回不足时**不自动 fallback** 到 L3，避免把归档扫描延迟引入每次召回。

（规划中）

当前 L3 侧已经实现的是归档写入与手动再蒸馏（`POST /v1/mems/redistill`），它们
属于 Memory Lifecycle，不属于在线 Recall，二者不要混淆。

### 6.4 Ranking 是内部实现

Ranking 属于 Mems 内部实现，具体权重不是 MRP Contract：

```text
Candidate Retrieval
        ↓
Source-specific Ranking
        ↓
Score
        ↓
Top K
```

Mems 可以持续优化 Ranking 而无需修改协议。协议只规定：响应中的 `score` 在同一
次 Recall 内按降序给出有意义的排序。

## 7. 错误处理与协议版本

### 7.1 错误语义

MRP 应提供稳定的错误语义。建议错误结构：

```json
{
  "error": {
    "code": "INVALID_IDENTITY",
    "message": "Invalid memory identity"
  }
}
```

建议错误类型：

```text
INVALID_REQUEST
INVALID_IDENTITY
INVALID_MEMORY_TYPE
MEMORY_ACCESS_DENIED
RECALL_TIMEOUT
UNSUPPORTED_VERSION
INTERNAL_ERROR
```

具体 HTTP Status Code 属于 API Transport Layer，不是 MRP 核心语义。当前 HTTP
实现使用常规校验（422）与异常（500）返回，结构化错误码信封尚未定稿：

**（待决策）**

### 7.2 版本管理

MRP 自身使用语义化版本：

```text
MAJOR.MINOR.PATCH
```

例如：`0.1.0`

- **MAJOR**：Breaking Change；
- **MINOR**：向后兼容的新能力；
- **PATCH**：Bug Fix / 规范澄清。

（规划中：当前 HTTP API 尚无 `protocol_version` 字段）

### 7.3 兼容性

目标形态中，调用方在请求中声明支持的 MRP Version，Mems 在响应中返回使用的
Version：

```json
{
  "protocol_version": "0.1"
}
```

Protocol Version 与 API Version 可以独立演化：

```text
MRP v0.1
   │
   ├── HTTP API v1
   ├── Python SDK
   ├── Java SDK
   └── Go SDK
```

（规划中）

## 8. 示例（Examples）

### 8.1 用户偏好召回

Request（当前已实现字段）：

```json
{
  "query": "用户主要使用什么语言开发？",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "top_k": 5
}
```

Response（当前字段形态示例，省略值为 null 的可选字段；`id` / `confidence`
尚未下发）：

```json
{
  "query": "用户主要使用什么语言开发？",
  "results": [
    {
      "source": "l2_profile",
      "content": "like language Java",
      "score": 1.18,
      "source_l1_ids": [102, 231],
      "created_at": "2026-04-01T10:00:00Z",
      "tenant_id": "tenant-001",
      "user_id": "user-001"
    }
  ],
  "total": 1
}
```

Mems 返回候选；Agent 决定是否将这些记忆加入 Context。

### 8.2 有证据支撑的知识

```text
L2 Profile：用户偏好 Java

        │
        ▼

Evidence：source_l1_ids = [102, 231]

        │
        ▼

L1 原始经历
```

如果调用方需要验证，可以进一步展开 Evidence（Expansion API，规划中）。

### 8.3 历史召回（L3，规划中）

```json
{
  "query": "2026 年上半年有哪些与订单系统相关的经历？",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "time_range": {
    "from": "2026-01-01",
    "to": "2026-06-30"
  }
}
```

该请求显式触发 L3 Archive Recall；在线召回不足时不会自动 fallback 到 L3。

**（规划中）**

## 9. 协议一致性（Conformance）

一个实现如果声明 **MRP Compatible**，至少必须满足：

### Request

- 支持 `query`；
- 支持 Identity 字段并按请求身份边界过滤；
- 使用由获准 identity 派生的服务端 namespace；
- 遵守 Runtime State Boundary（不接收运行时状态）；
- 遵守 `top_k` 语义（返回条数上限，不是 token 预算）。

### Response

- 返回 Memory Candidates；
- 返回 `source` 与 `content`；
- 返回 `score`；
- Memory 存在 Evidence 时提供 Evidence Reference（`source_l1_ids`）；
- 遵守 Identity Isolation；
- `total` 表示实际返回条数且不超过 `top_k`。

### Semantics

- 不替 Agent 做最终 Memory Selection；
- 不修改历史 Memory（Time-Forward）；
- 不将 Runtime State 作为 Memory Recall 的必要组成部分；
- 不将 Context Construction 纳入 Mems 职责；
- 不在 Recall 阶段做实时 Conflict Reasoning 或返回 Conflict Marker；
- 不在响应中返回 LLM 生成的 `reason`。

## 10. 实现状态汇总（Implementation Status）

| MRP Capability | 当前实现 | 状态 |
| --- | --- | --- |
| L0/L1/L2 分层召回 | `/v1/mems/query` | 已实现 |
| L3 召回 | 在线召回排除已归档 L1 | 规划中（仅显式 `time_range`） |
| Query Intent Inference | 关键词推断 + `intent_bonus`（内部机制） | 已实现 |
| `memory_types` 请求字段 | 无 | 规划中 |
| Evidence 链路 | `QueryResultItem.source_l1_ids` | 已实现 |
| L3 证据指针 | 再蒸馏进度表 `l3_path` / `l3_line` | 已实现 |
| Confidence（存储与排序） | Profile / Fact 的 `confidence` + `verification_decay`；Summary 也应用验证衰减 | 已实现 |
| 响应 `confidence` 独立字段 | 无（合并进 `score`） | 规划中 |
| Stable Memory `id` | 无 | 规划中 |
| Conflict Log | `mems_l2_conflict_log`（蒸馏 / 再蒸馏对账期） | 已实现 |
| Recall Conflict Marker | 无 | 明确不做 |
| LLM 生成的 `reason` | 无 | 明确不做 |
| Feedback-driven Reweighting | 无（Time-Forward） | 明确不做 |
| Runtime State 字段 | 无 | 明确不做 |
| 显式 `intent` 字段 | 无 | 明确不做 |
| `max_tokens` 等预算字段 | 无 | 明确不做 |
| `session_id` 作为 Recall Filter | Query schema 无该字段；响应仅 L0/L1 携带 | 明确不做 |
| 自动 L3 Fallback | 无 | 明确不做 |
| `created_at` 语义 | Profile / Fact 使用 `last_verified_at`；其他来源使用创建 / 记录时间 | 已实现 |
| `total` 语义 | 实际返回条数（≤ `top_k`） | 已实现 |
| 结构化错误码信封 | 无 | 待决策 |
| `protocol_version` | 无 | 规划中 |
| Evidence Expansion API | 无 | 规划中 |

## 11. 路线图（Roadmap）

### P0 — 协议完整性

- Request：`memory_types` 来源过滤、`time_range`（显式 L3 召回）；
- Response：Stable `id`、独立 `confidence` 分项（Profile / Fact）。

### P1 — 证据能力

- Evidence Expansion API（按需展开 L1 原文）；
- 完善 L3 → L1 / L3 → L2 的 Provenance。

### P2 — 检索演进

- L3 Explicit Recall 落地；
- 更丰富的 Temporal Retrieval；
- Hybrid Retrieval 与 Ranking Evaluation。

### 待决策

- 结构化错误码信封（见 7.1）；
- `protocol_version` 协商（见 7.2）。

### Future — 未来独立协议

以下能力暂不进入当前 MRP，应在独立协议成熟后再考虑：

```text
Memory Feedback Protocol
Memory Update Protocol
Memory Consolidation Protocol
Memory Forgetting Protocol
Cross-Agent Memory Protocol
Memory Subscription / Streaming
```

> 维护说明：本文的“当前实现”状态以 `src/mems/` 代码为准；修改公开 API 或
> 排序行为时，请同步更新本文、[Query API](/zh/api/query) 与
> [Identity Model](/zh/integration/identity-model)。
