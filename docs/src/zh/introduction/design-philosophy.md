# 设计哲学

## Text-First

Mems 不把长期历史完全锁死在向量库或专有结构里。L3 使用 JSONL 做归档，保证长期可读、可检查、可迁移。

## Decentralized Schema

Mems 不假设只有一种固定业务身份。`tenant_id`、`user_id`、`agent_id`、`session_id`、`scope` 共同定义隔离与可见性边界，便于支持单用户、多用户、多 Agent 场景。

## 记忆而非运行时

Mems 存储记忆，不管理 Agent 运行时状态。它不维护会话上下文、任务目标、当前计划或临时变量。调用方自行管理运行时。

## Layered Memory

热缓存、原始事件、稳定知识、冷归档本来就不是一类数据，不应该混成一个 recall 面。Mems 用 L0-L3 显式分层，让不同类型的记忆用不同的存储和处理策略。

## SQL-First Online Truth

当前在线记忆以 SQL 为真相源。Qdrant 是在线派生副本，JSONL 只承担 L3 归档职责。这样可以降低副本同步失败对主流程的破坏。
