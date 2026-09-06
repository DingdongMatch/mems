# Identity Model

Mems 使用平级字符串字段描述记忆的归属与可见性，不预设业务层级、不内置账号
体系。以下字段在 `/v1/mems/write` 与 `/v1/mems/query` 两侧同时使用。

## 字段总览

| 字段 | 类型 | 角色 | 边界类型 |
| --- | --- | --- | --- |
| `tenant_id` | string | 租户 / 组织 | 硬边界（可选，给出即过滤） |
| `user_id` | string | 用户 | 硬边界（可选，给出即过滤） |
| `agent_id` | string | Agent | 硬边界（必填） |
| `scope` | string | 业务自定义可见性标签 | 软标签（可选，给出即过滤） |
| `session_id` | string | 写入侧动作溯源 | 不是隔离边界（query 不接受） |

硬边界与软标签的差异：

```text
tenant_id / user_id / agent_id
    ↓
硬隔离：决定“能访问哪些记忆”

scope
    ↓
软可见性：在身份范围内进一步收窄（private / shared / team:* ...）
```

## 过滤语义（重要）

当前实现的过滤是「相等过滤」：

```text
字段给出 → 只返回该字段等于该值的记忆
字段省略 → 不按该维度过滤
```

因此：

- 只传 `agent_id` 的 Query 会看到该 agent 名下所有 tenant/user/scope 的记忆；
- scope 只能在已给出的身份字段范围内收窄，不能扩大可见范围；
- `agent_id` 是唯一必填维度，但多用户场景**不能**只靠它隔离。

推荐的多用户组合：

```json
{
  "tenant_id": "tenant-001",
  "user_id": "user-42",
  "agent_id": "support-agent",
  "scope": "private"
}
```

write 与 query 必须使用一致的身份：写入时漏传 `user_id`，查询时再带
`user_id`，将无法召回该条记忆；反过来，两侧都漏传则可能互相看到对方数据。

## session_id 的定位

- `POST /v1/mems/write` 接受 `session_id`，用于回答「这条记忆来自哪次会话」；
- L0 快照按 `(identity + session_id)` 组织，同一会话的写入会合并；
- L1 主记录持久化 `session_id`；
- `POST /v1/mems/query` **不接受** `session_id`，它不能作为召回过滤器或隔离
  边界；
- query 响应中 L0/L1 结果会携带 `session_id`，L2 蒸馏知识不依赖它。

## 推荐用法

| 场景 | 建议 |
| --- | --- |
| 单用户本地 Agent | 固定一个 `user_id`（如 `local-user`），`tenant_id` 留空，`scope="private"` |
| 多用户 SaaS | 每次请求显式传 `tenant_id` + `user_id` |
| 多 Agent 系统 | 不同 Agent 用不同 `agent_id`；user 隔离仍按需传 `user_id` |
| 共享 / 团队空间 | 用 `scope` 表达 `private`、`shared`、`team:*`，配合身份边界使用 |

命名约定建议：

- `tenant_id`：企业/组织稳定 id（如 `acme`、`tenant_001`）；
- `user_id`：业务用户稳定 id（不要放 PII，如邮箱可直接用作 id 时要谨慎）；
- `agent_id`：部署在用户侧的 Agent 实例 id；
- `scope`：小写字符串，推荐 `private` / `shared` / `team:<team-id>` 风格。

## 与存储层的关系

```text
L0（Redis key）   ：mems:l0:{tenant}:{user}:{scope}:{agent}:{session}
L1（SQL 列）      ：tenant_id / user_id / agent_id / session_id / scope
L2（SQL 列）      ：tenant_id / user_id / agent_id / scope（无 session_id）
Qdrant payload    ：与 L1/L2 一致，用于向量过滤
L3（JSONL 字段）  ：保留完整身份字段
```

query 时 SQL 与 Qdrant 都按请求中的非空身份字段做相等过滤。

## 检查清单

- [ ] 所有调用点使用同一套身份字段名；
- [ ] 多用户服务在 write/query 两端都传 `user_id`；
- [ ] 测试用例覆盖「只传 agent_id 能看到什么」，确认隔离符合预期；
- [ ] 不把 `session_id` 当作隔离维度使用；
- [ ] 使用 `scope` 控制共享与私有空间时，同时保留身份边界。

更多语义说明见 [Memory Recall Protocol](/zh/introduction/memory-recall-protocol)
与 [接入总览](/zh/integration/overview)。
