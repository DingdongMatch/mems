# Identity Model

Mems 当前公开 API 支持以下 identity 字段：

- `tenant_id`
- `user_id`
- `agent_id`
- `session_id`
- `scope`

推荐原则：

- `tenant_id / user_id / agent_id / session_id` 是硬隔离边界
- `scope` 是上层业务自定义的软可见性标签
- 多用户场景不要只依赖 `agent_id`

## 推荐用法

- 单用户应用：可以固定一个 `user_id`，`tenant_id` 留空或默认
- 多用户应用：每次请求都显式传 `user_id`
- 多 Agent 系统：不同 Agent 使用不同 `agent_id`
- 有共享空间的业务：用 `scope` 控制 `private`、`shared`、`team:*` 之类的可见性
