# 接入总览

Mems 是记忆基础设施 — 它存储和召回记忆，不管理运行时状态。调用方自行负责会话管理、prompt 组装和任务状态。

核心流程：

1. `POST /v1/mems/query` — 召回相关记忆
2. 调用方自行组装上下文并生成回答
3. `POST /v1/mems/write` — 将新的记忆内容写入系统

先记再查或先查再记都可以，由调用方决定。

## 核心公开接口

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/v1/mems/write` | `POST` | 将记忆内容写入 L0/L1 |
| `/v1/mems/query` | `POST` | 跨 L0、L1、L2 召回相关记忆 |
| `/v1/mems/redistill` | `POST` | 将归档 L3 记录再蒸馏到 L2 |
| `/v1/mems/status` | `GET` | 查看依赖健康与流水线状态 |
| `/v1/mems/health` | `GET` | 轻量健康检查 |
