# Mems

> 一个面向 AI Agent 的分层、自蒸馏、跨世纪长周期记忆系统。

Mems 是一套定位工业级的 AI Agent 记忆中枢方案。它通过四层冷热解耦架构，为智能体提供具备人格一致性、低成本检索和结构化进化的记忆基座。

[English](README.md)

文档：https://dingdongmatch.github.io/mems/

## 🏗️ 核心设计哲学：为什么选择 Mems

与传统的单层向量数据库（RAG）不同，Mems 解决了 Agent 在长期运行中的“信息熵增”问题：

  - **⚡ 瞬时工作记忆 (L0)**：基于 Redis 的极速上下文缓存，确保 Agent 在当前会话中具备毫秒级响应能力。
  - **🧠 异步知识蒸馏 (L2)**：自动化 LLM 流水线，从 L1 原始叙事中提炼**稳定的语义事实**，实现从“经历”到“认知”的进化。
  - **🔍 证据溯源 (Grounding)**：L2 提炼结果强关联 L1 原始语料，拒绝黑盒知识，确保 Agent 每一个推论都有据可查。
  - **📜 跨世纪归档 (L3)**：采用 **Text-First** 策略，将超长期记忆转化为 JSONL 持久化文件，对抗硬件更迭与格式淘汰。
  - **🛡️ 生产级隔离**：内建多租户（Tenant/User/Agent/Session）权限模型，完美适配 SaaS 与企业级 Agent 部署。
  - **🔌 原子化接入**：极致精简的 API 设计，只需 `Context -> Query -> Write` 三步即可完成 Agent 记忆赋能。

## 架构快照

```text
Third-party Agent
  |-- GET /v1/mems/context --> L0 Redis
  |-- POST /v1/mems/query --> L1 + L2 Recall
  `-- POST /v1/mems/write --> L1 SQL + Qdrant
                                  |-- L2 Distilled Memory
                                  `-- L3 JSONL Archive
```

## 快速开始

```bash
uv sync
cp .env.example .env
docker compose up -d
python scripts/init_db.py
uv run python -m mems.main
curl http://localhost:8210/v1/mems/health
```

文档站本地启动：

```bash
cd docs
npm install
npm run dev
```

## 写入及记忆查询示例

```bash
curl -X POST http://localhost:8210/v1/mems/write \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "session_id": "demo-session",
    "scope": "private",
    "messages": [
      {"role": "user", "content": "记住我喜欢 Rust"},
      {"role": "assistant", "content": "收到。"}
    ]
  }'
```

```bash
curl -X POST http://localhost:8210/v1/mems/query \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "scope": "private",
    "query": "我喜欢什么？"
  }'
```

## 📊 性能表现

基于 **RAGAS** 的最新基准测试报告显示（`2026-04-05`）：

| 指标 | 得分 |
| :--- | :--- |
| **Context Recall** | **1.0000** |
| **Faithfulness** | **0.6875** |
| **Answer Relevance** | **0.6267** |

## 开源协议

Licensed under the Apache License, Version 2.0. See `LICENSE` for details.
