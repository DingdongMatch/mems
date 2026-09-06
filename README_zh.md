# Mems

[![Tests](https://github.com/DingdongMatch/mems/actions/workflows/tests.yml/badge.svg)](https://github.com/DingdongMatch/mems/actions/workflows/tests.yml)
[![Lint](https://github.com/DingdongMatch/mems/actions/workflows/lint.yml/badge.svg)](https://github.com/DingdongMatch/mems/actions/workflows/lint.yml)
[![Type Check](https://github.com/DingdongMatch/mems/actions/workflows/typecheck.yml/badge.svg)](https://github.com/DingdongMatch/mems/actions/workflows/typecheck.yml)

> 一个面向 AI Agent 的分层、自蒸馏、跨世纪长周期记忆系统。

Mems 是一套定位工业级的 AI Agent 记忆基础设施。它通过四层冷热解耦架构，为智能体提供具备人格一致性、低成本检索和结构化进化的记忆基座。

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
  |-- POST /v1/mems/write --> L0 Redis（热记忆）
  |                           |-- L0 输出管道（默认关闭）--> Redis Stream 供用户代码消费
  |                           `-- L1 SQL + Qdrant（情景账本）
  |                                |-- L2 蒸馏知识（画像/事实/事件/摘要）
  |                                `-- L3 JSONL 归档
  |-- POST /v1/mems/query --> L0 + L1 + L2 混合召回
  `-- GET /v1/mems/status | /v1/mems/health --> 健康与流水线状态
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

## 5 分钟演示

服务启动后（见快速开始），跑一遍完整的记忆闭环：

```bash
uv run python scripts/demo.py
```

脚本会写入一条记忆、召回它、查看流水线状态，并触发一次再蒸馏（在产生归档记录前为 no-op）。

## 当前状态

Mems 目前处于 **v0.1 早期阶段**。核心流水线——写入、召回、蒸馏、归档、再蒸馏——可用且有
单元测试覆盖，但项目**面向单机部署**：

- 默认存储为 PostgreSQL
- 归档调度器为进程内实现；Outbox worker 默认在进程内运行，也可通过
  `python -m mems.worker` 独立部署；暂不完全支持多副本部署
- 基准数据来自自建场景，而非公开基准

欢迎反馈、issue 与贡献。

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

## L0 记忆输出管道

Mems 可以把每一次 L0 记忆写入作为事件发布到 Redis Stream，让你用自己的代码
消费记忆（镜像、看板、下游工作流）。管道**默认关闭**：

```bash
L0_PIPELINE_ENABLED=true        # 默认：false
L0_PIPELINE_STREAM=mems:l0:events
L0_PIPELINE_MAXLEN=10000
```

使用任意 Redis 客户端消费：

```bash
redis-cli XREAD COUNT 10 STREAMS mems:l0:events 0
```

每条事件包含 `event_type`（`write`/`append`）、身份字段、`content` 快照以及
JSON 编码的 `messages` 缓冲区。发布是尽力而为的：流失败不会阻断
`POST /v1/mems/write`。完整格式与消费组示例见
[L0 输出管道](docs/src/zh/integration/l0-pipeline.md)。

## L3 → L2 再蒸馏：随模型进化的记忆

Mems 的亮点：**归档不是终点**。当底层大模型变得更强时，你可以再蒸馏归档记忆，
让长期知识升级为当前模型能提炼出的结论：

```bash
curl -X POST http://localhost:8210/v1/mems/redistill
# 或
uv run python -m mems.redistill --agent demo-agent
```

归档 JSONL 完整保留原始记忆：再蒸馏从 L3 读取，用当前 `OPENAI_MODEL` 重新执行
提取 + 对账，并通过版本链（`supersedes` + 冲突日志）无损升级 L2。记录按
「内容指纹 + 模型名」去重，模型升级后旧数据自动进入待重蒸队列。闭环故事见
[什么是 Mems](docs/src/zh/introduction/what-is-mems.md)。

## 📊 核心 KPI 与基准

Mems 用**检索层 KPI**衡量自身质量——记忆必须找得准、找得快、找得安全；
RAGAS 端到端指标仅作参考，受外部答案模型影响，不用于考核 Mems 本身。

### 核心 KPI（Mems 自身，2026-08-06）

| KPI | 当前值 | 含义 |
| :--- | :--- | :--- |
| L1 recall@k | 1.0000（43/43） | 期望的情景证据必被召回 |
| L2 recall@k | 1.0000（43/43） | 蒸馏知识必被召回 |
| MRR（L1/L2） | ≈0.72 | 证据平均排名约第 1.4 位 |
| 查询延迟 | 20–50 ms | 在线召回保持毫秒级 |
| 卫生闸门 | 全部通过 | L0 命中 / 归档排除 / 身份隔离 |

### 端到端参考（RAGAS，2026-08-06）

| 指标 | 得分 |
| :--- | :--- |
| **Context Recall** | **1.0000** |
| **Faithfulness** | **0.7188 ± 0.3184** |
| **Answer Relevance** | **0.6495 ± 0.1851** |

43/43 用例全部通过。faithfulness / answer relevance 随外部答案模型与评判模型
变化，作为回归参考而非核心 KPI。

## 开源协议

Licensed under the Apache License, Version 2.0. See `LICENSE` for details.
