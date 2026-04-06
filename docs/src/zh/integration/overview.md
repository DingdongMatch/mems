# 接入总览

Mems 对第三方 Agent 的推荐接入时序是：

1. `GET /v1/mems/context`
2. `POST /v1/mems/query`
3. Agent 组装 prompt 并生成回答
4. `POST /v1/mems/write`

这样拆分的原因是：

- `context` 负责当前 session 的 live 页面和历史分页
- `query` 负责长期记忆召回
- `write` 负责把本轮新消息写回系统

## 核心公开接口

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/v1/mems/write` | `POST` | 追加 session turn 并持久化到 L0/L1 |
| `/v1/mems/query` | `POST` | 检索活跃 L1 与 L2 |
| `/v1/mems/context` | `GET` | 获取当前 live 页面与更早历史 |
| `/v1/mems/status` | `GET` | 查看依赖健康与流水线状态 |
| `/v1/mems/health` | `GET` | 轻量健康检查 |
