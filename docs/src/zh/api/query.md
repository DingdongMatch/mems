# Query — 记忆召回

## Endpoint

| 项 | 值 |
| --- | --- |
| URI | `/v1/mems/query` |
| 方法 | `POST` |
| Content-Type | `application/json` |
| 默认 Base URL | `http://localhost:8210` |

跨 L0 热快照、活跃 L1 情景记录与 L2 蒸馏知识召回记忆。Mems 返回的是
**Memory Candidate**；是否进入 Context、如何排序展示由调用方决定，Mems
不管理会话上下文。

## 请求参数

请求体为 JSON，字段如下：

| 字段 | 类型 | 必填 | 默认 | 约束 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `agent_id` | string | 是 | — | 非空 | Agent 边界；所有召回都按该字段过滤 |
| `query` | string | 是 | — | 非空且不能为纯空白 | 检索文本，用于语义 / 词法检索 |
| `tenant_id` | string | 否 | `null` | — | 租户硬边界；给出时按相等过滤 |
| `user_id` | string | 否 | `null` | — | 用户硬边界；多用户场景必须显式传入 |
| `namespace` | string[] | 否 | `[]` | 最多 16 段，每段非空且不超过 128 字符 | 通用业务上下文的精确存储分区 |
| `top_k` | number | 否 | `5` | 1-100 | 本次最多返回的候选条数 |

`namespace` 必须与写入时的路径一致，只选择一个精确分区，不隐含通配或子路径检索。
调用方应用应先授权对应业务上下文。当前请求也**不接受**以下字段（语义见
[记忆召回协议](/zh/introduction/memory-recall-protocol)）：

- `session_id`：写入侧动作溯源，不作为召回过滤/隔离字段；
- `memory_types`：来源过滤，规划中；当前总是跨 L0/L1/L2 召回；
- `time_range`：L3 归档召回的时间范围，规划中；
- `state` / `messages` / `system_prompt` / `current_plan` / `tools` 等运行时状态：
  明确不做；
- `intent`、`max_tokens` 等：明确不做。

### 请求示例

```json
{
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "query": "用户主要用什么语言开发？",
  "top_k": 5
}
```

```bash
curl -X POST http://localhost:8210/v1/mems/query \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-001",
    "user_id": "user-001",
    "agent_id": "agent-001",
    "query": "用户主要用什么语言开发？",
    "top_k": 5
  }'
```

## 响应

### HTTP 200

```json
{
  "query": "用户主要用什么语言开发？",
  "results": [
    {
      "source": "l2_profile",
      "content": "like language Java",
      "score": 1.18,
      "source_l1_ids": [102, 231],
      "created_at": "2026-04-01T10:00:00Z",
      "tenant_id": "tenant-001",
      "user_id": "user-001"
    },
    {
      "source": "l1_episodic",
      "content": "user: 我之前用 Java 写过订单系统\nassistant: 已记住",
      "score": 1.02,
      "source_l1_ids": [102],
      "session_id": "session-007",
      "created_at": "2026-03-20T08:00:00Z",
      "tenant_id": "tenant-001",
      "user_id": "user-001"
    }
  ],
  "total": 2
}
```

### 顶层字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `query` | string | 回显本次请求的检索文本 |
| `results` | array | 排序后的 Memory Candidate 列表，按 `score` 降序 |
| `total` | number | 本次实际返回的条数，等于 `results.length`，恒 ≤ `top_k`；不代表候选池或数据库命中总数 |

### results 项字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `source` | string | 来源类型：`l0_hot` / `l1_episodic` / `l2_profile` / `l2_fact` / `l2_event` / `l2_summary` |
| `content` | string | 可读记忆文本 |
| `score` | number | 综合排序分，组成随来源不同；只用于本次召回内排序 |
| `created_at` | string \| null | 时间上下文；L0 无此值，L2 Profile/Fact 实际为 `last_verified_at`（详见下文） |
| `source_l1_ids` | number[] | 证据链路：本条记忆对应的 L1 记录 id（L2 结果用于溯源，L1 结果通常为其自身 id） |
| `session_id` | string \| null | 来源会话；仅 L0/L1 结果有值 |
| `tenant_id` | string \| null | 身份上下文 |
| `user_id` | string \| null | 身份上下文 |

服务端根据经过验证的 identity 和可选 `namespace` 路径段生成不可变
`MemoryNamespace`。完整内部 namespace 不作为查询参数。调用方应用应在请求前授权 identity
和业务上下文；Mems 使用它们限定存储查询。

> `id`（稳定记忆标识）与 `confidence`（可信度分项）属于 MRP 目标字段，当前
> 尚未返回。

### 各来源的 content 与时间语义

| source | content 是什么 | created_at 含义 |
| --- | --- | --- |
| `l0_hot` | 该会话活跃快照的记忆文本 | 无（不返回） |
| `l1_episodic` | 原始记忆文本（由消息折叠而成或写入的 content） | 记录创建时间 |
| `l2_profile` | 画像文本（category/key/value 拼接） | `last_verified_at`（最近验证时间） |
| `l2_fact` | 事实文本（subject/predicate/object 拼接） | `last_verified_at` |
| `l2_event` | 事件文本 | 事件记录时间 |
| `l2_summary` | 摘要内容 | 摘要创建时间 |

## 检索行为

### 当前召回范围

```text
L0：Redis 活跃快照 → 词法重叠
L1：Qdrant 向量召回活跃（未归档）L1 记录
L2：Profile/Fact/Event 走 SQL 候选 + 词法/因子排序；
     Summary 走 Qdrant 向量召回
```

行为细节：

- 已归档 L1（L3）默认不参与在线召回；归档召回需显式 `time_range`（规划中）；
- 向量搜索与 L2 SQL 候选都有内部候选上限，最终统一排序后截取 `top_k`；
- L0 快照按会话保存，查询会看到该 agent 各活跃会话的快照（有词法重叠才会
  返回）；
- 多用户场景应传入目标 `user_id`（必要时还有 `tenant_id`）以定位对应身份分区；缺省值
  指向独立的空值分区，不会作为通配符（见 [Identity Model](/zh/integration/identity-model)）。

### score 的组成

`score` 是排序分，不是概率、不是事实正确性：

| source | 因子 |
| --- | --- |
| `l0_hot` | 词法重叠 |
| `l1_episodic` | 向量相似度 + 词法重叠 + 意图加分 + 新鲜度 |
| `l2_profile` / `l2_fact` | confidence + 词法重叠（先乘验证衰减）+ 意图加分 + 新鲜度 |
| `l2_event` | importance + 词法重叠 + 意图加分 + 新鲜度 |
| `l2_summary` | 词法重叠 / 向量相似度（先乘验证衰减）+ 意图加分 + 新鲜度 |

各来源 base 归一化到 0-1 后叠加意图/新鲜度加分，因此最终值可能大于 1。权重是
内部实现，可能调整。详见
[MRP 4.3](/zh/introduction/memory-recall-protocol)。

## 错误

| 场景 | HTTP | 说明 |
| --- | --- | --- |
| 缺少 `agent_id` / `query` | `422` | FastAPI 校验失败，响应体为 `detail` 数组 |
| `query` 为空白字符串 | `422` | 服务端 strip 后拒绝 |
| `top_k` 超出 1-100 | `422` | 校验失败 |
| 内部服务异常 | `500` | `detail` 为异常信息（embedding、数据库等硬依赖失败时触发） |

> 说明：向量服务不可用时，召回服务会记录 warning 并以空向量结果继续走
> SQL/词法召回（L1 与 L2 Summary 会缺失，但请求本身不失败）；embedding 或
> 数据库等硬依赖失败才会返回 500。

## 注意

- Query 返回的是候选，Context 组装、prompt 构建与最终 Memory Selection 由调用方完成；
- 需要对召回结果做证据展示时，使用 `source_l1_ids` 追溯 L1 原文；
- 同一条内容可能同时出现在 L0（热快照）与 L1（主记录），调用方可按需去重；
- 当前无鉴权头；请在网关层完成认证。
