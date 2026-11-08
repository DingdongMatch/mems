# 设计哲学

## 分层记忆（Layered Memory）

热缓存、原始事件、稳定知识与冷归档是不同类型的数据，不应共用一个召回面。Mems 用 L0–L3 显式分层，让每种记忆都有匹配的存储与处理策略。

## 去中心化身份模型（Decentralized Schema）

Mems 不预设唯一的上游业务身份模型。请求携带 `tenant_id`、`user_id`、`agent_id`，`session_id` 是可选的动作溯源。API 支持可选通用 `namespace` 路径表达调用方业务上下文；Mems 将它与经过验证的 identity 组合，生成不可变 `MemoryNamespace`。业务授权由调用方应用定义，并传入获准的 identity 与 namespace 上下文。

## SQL 优先的在线真相源（SQL-First Online Truth）

在线记忆以 SQL 为真相源。Qdrant 只是在线派生副本，JSONL 仅承担 L3 归档职责。SQL 先落库的次序可降低副本同步失败对主流程的破坏。

## 文本优先（Text-First）

Mems 不把长期历史锁死在向量库或专有结构里。L3 使用 JSONL 纯文本归档，保证记忆长期可读、可检查、可迁移。

## 记忆随模型进化（Model-Evolving Memory）

长期记忆应当随模型能力的提升而进化。L3 JSONL 作为持久、与模型无关的原料库保留；手动再蒸馏让归档记录重新进入当前蒸馏流水线，通过 L2 版本链把旧知识无损升级。Mems 的设计保证归档不是墓碑，而是在更强模型出现时能够重生的原料。

## 时间线原则（Time-Forward）

Mems 遵循时间线原则：已经发生的事情只向前推进，不破坏、不改写、不撤回。对过去的记忆不做基于使用反馈的降权或修改——即使后来的使用情况不理想，也只在更晚的时间点写入新的记忆，由 L2 版本链（`supersedes` + 冲突日志）向前演化，原始记录始终保留。
