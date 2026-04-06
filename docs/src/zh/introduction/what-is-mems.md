# 什么是 Mems

Mems 是一套定位工业级的 AI Agent 记忆中枢方案。它通过四层冷热解耦架构，为智能体提供具备人格一致性、低成本检索和结构化进化的记忆基座。

它解决的核心问题是：

- prompt window 太短，几轮之后就丢失上下文
- 原始对话很多，但难以沉淀成长期知识
- 当前会话、历史事件、用户偏好、长期归档经常混在一个模糊的 memory bucket 里
- 第三方系统很难接入统一、清晰的 memory API

Mems 用四层架构解决这个问题：

- `L0`：当前 session 的 live context
- `L1`：原始情景记忆和在线历史
- `L2`：蒸馏后的长期知识
- `L3`：面向长期保留的 JSONL 归档

对接入方来说，主流程只有四步：

1. 调 `GET /v1/mems/context` 获取当前上下文
2. 调 `POST /v1/mems/query` 做长期检索
3. Agent 自己生成回答
4. 调 `POST /v1/mems/write` 写回本轮对话
