# Write — 写入记忆

## Endpoint

| 项 | 值 |
| --- | --- |
| URI | `/v1/mems/write` |
| 方法 | `POST` |
| Content-Type | `application/json` |
| 默认 Base URL | `http://localhost:8210` |

把一条记忆写入 Mems：同步写 L0 热缓存，并在同一事务内持久化 L1 情景主记录、
enqueue 向量同步与蒸馏触发事件。Mems 只接收「记忆内容」与身份字段，不接收
运行时状态（会话上下文、任务目标、活动计划等由调用方管理）。

## 请求参数

请求体为 JSON：

| 字段 | 类型 | 必填 | 默认 | 约束 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `agent_id` | string | 是 | — | 非空 | Agent 边界 |
| `content` | string | 条件 | `null` | 非空 | 记忆文本；与 `messages` 至少提供一个 |
| `messages` | array | 条件 | `[]` | 元素见下 | 结构化来源消息；与 `content` 至少提供一个 |
| `tenant_id` | string | 否 | `null` | — | 租户硬边界 |
| `user_id` | string | 否 | `null` | — | 用户硬边界；多用户场景建议显式传入 |
| `session_id` | string | 否 | `null` | — | 动作溯源（不作为隔离边界） |
| `scope` | string | 否 | `null` | — | 软可见性标签 |
| `ttl_seconds` | number | 否 | `1800` | 1-604800 | L0 热快照 TTL（秒）；L1 主记录不受此值影响 |
| `importance_score` | number | 否 | `0.5` | 0-1 | 重要度，用于蒸馏优先级 |

`messages` 元素格式：

| 字段 | 类型 | 必填 | 约束 | 含义 |
| --- | --- | --- | --- | --- |
| `role` | string | 是 | 1-32 字符 | 消息角色（如 `user` / `assistant` / `system`） |
| `content` | string | 是 | 非空 | 消息内容 |

### content 与 messages 的取舍

```text
只传 content     → 把 content 作为记忆文本（检索/蒸馏用）
只传 messages    → Mems 把消息折叠成记忆文本（role: content，按行连接）
两者都传         → content 作为记忆文本，messages 作为结构化原文保留
```

> 折叠示例：`[{"role": "user", "content": "我喜欢 Rust"}, ...]` 会生成
> `user: 我喜欢 Rust` 这样的记忆文本。折叠是只读的，原始消息始终保留在
> `messages` / L1 `messages_json` 中。

### 请求示例

```json
{
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "session_id": "session-007",
  "scope": "private",
  "content": "用户喜欢 Rust 并偏好简洁回答。",
  "messages": [
    {"role": "user", "content": "记住我喜欢 Rust。"},
    {"role": "assistant", "content": "好的，我会保持简洁。"}
  ],
  "ttl_seconds": 1800,
  "importance_score": 0.8
}
```

```bash
curl -X POST http://localhost:8210/v1/mems/write \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-001",
    "user_id": "user-001",
    "agent_id": "agent-001",
    "session_id": "session-007",
    "scope": "private",
    "content": "用户喜欢 Rust 并偏好简洁回答。",
    "importance_score": 0.8
  }'
```

## 响应

### HTTP 200

```json
{
  "success": true,
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "session_id": "session-007",
  "scope": "private",
  "importance_score": 0.8,
  "content": "用户喜欢 Rust 并偏好简洁回答。",
  "messages": [
    {"role": "user", "content": "记住我喜欢 Rust。"},
    {"role": "assistant", "content": "好的，我会保持简洁。"}
  ],
  "appended_count": 2,
  "persisted_to_l1": true,
  "l1_id": 101,
  "message": "Memory remembered and persisted to L1"
}
```

### 字段含义

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `success` | boolean | 请求是否处理成功 |
| `tenant_id` / `user_id` / `agent_id` / `session_id` / `scope` | string \| null | 回显身份字段 |
| `importance_score` | number | 回显重要度 |
| `content` | string | 实际写入的记忆文本（content 或由 messages 折叠生成） |
| `messages` | array | 本次请求携带的结构化消息（不是合并后的完整缓冲区） |
| `appended_count` | number | 本次请求追加的消息条数 |
| `persisted_to_l1` | boolean | L1 主记录是否持久化成功 |
| `l1_id` | number \| null | 新 L1 记录的 id |
| `message` | string | 人类可读的结果说明 |

## 写入后的行为

### 1. L0 热快照

- key 由 identity + `session_id` 组成；
- 同一 session 已存在快照时**追加**消息，短期缓冲区超过 20 条时丢弃最旧消息
  （`DEFAULT_CONTEXT_WINDOW`）；
- 追加时保留原快照剩余 TTL，新会话按 `ttl_seconds` 设置过期时间；
- 快照的 `content`（记忆文本）以本次请求的 content 或新消息折叠结果为准；
  短期缓冲区会追加新消息并裁剪最旧消息。

### 2. L1 主记录

- 每次调用在 SQL 落一条新的 Episodic 记录（`mems_l1_episodic`），不是覆盖
  旧记录；
- 同事务 enqueue `vector_sync`（异步写 Qdrant 向量副本）与蒸馏触发事件；
- L1 记录没有 TTL；过期由归档策略处理（默认 30 天进入 L3 JSONL）。

### 3. 蒸馏触发

```text
importance_score >= 0.8（DISTILL_HIGH_IMPORTANCE_THRESHOLD）
      → 写入后立即 enqueue batch=1 定向蒸馏

否则
      → enqueue distill_due；待蒸馏记录达到 DISTILL_THRESHOLD（默认 100）
        时按 agent 展开蒸馏
```

蒸馏是异步的：`POST /v1/mems/write` 返回时 L2 尚未产生，需要等待 Outbox
worker 消费。LLM 未配置时蒸馏会被静默跳过（当前实现既不记录日志，也不通过
API 返回提示），不影响本次写入。

## 错误

| 场景 | HTTP | 说明 |
| --- | --- | --- |
| `content` 与 `messages` 都缺失/为空 | `422` | 模型校验失败 |
| 缺少 `agent_id` | `422` | 模型校验失败 |
| `messages` 元素缺 `role`/`content` | `422` | 模型校验失败 |
| `ttl_seconds` 超出 1-604800 | `422` | 模型校验失败 |
| `importance_score` 超出 0-1 | `422` | 模型校验失败 |
| 内部服务异常（数据库/Redis 等） | `500` | `detail` 为异常信息 |

> Redis 不可用时，L0 写入与整体请求都会失败（当前实现不会静默降级跳过 L0）。

## 注意

- 不要把运行时状态（`current_plan`、`tool_results`、完整对话记录等）原样写入；
  只写值得长期/短期记住的内容；
- `importance_score` 建议结合业务语义设置：0.8+ 用于明确的长期偏好/关键事实，
  普通 0.5 左右用于常规记忆；
- 高频 append 同一 session 时，`messages` 会累积到短期缓冲区并裁剪最旧消息；
  需要完整审计原文时，应在自己的业务侧保留原始 transcript；
- L0 只是热缓存：即使 TTL 过期，L1 主记录仍在（除非已归档且未来策略清理）。
