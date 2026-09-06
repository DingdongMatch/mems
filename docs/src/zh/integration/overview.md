# 接入总览（Integration Overview）

Mems 是面向 AI Agent 的记忆基础设施，通过 L0-L3 分层提供记忆的存储、召回、
蒸馏与归档。调用方自行负责会话管理、prompt 组装、任务状态与最终决策；Mems
只处理「值得记住的内容」以及「如何按需召回它们」。

本文档回答三个问题：

1. 接入 Mems 前需要准备什么；
2. 推荐按照什么顺序调用；
3. 写入与召回后，系统内部发生了什么、结果字段如何理解。

端点级字段参考见 [Query](/zh/api/query)、[Write](/zh/api/write) 与
[Redistill](/zh/api/redistill)；身份字段规范见
[Identity Model](/zh/integration/identity-model)；召回语义见
[记忆召回协议（MRP）](/zh/introduction/memory-recall-protocol)。

## 1. 依赖与启动

### 1.1 需要的外部服务

| 服务 | 用途 | 默认地址 |
| --- | --- | --- |
| PostgreSQL | L1/L2 主记录、Outbox、归档元数据的 SQL 真相源 | `localhost:5432` |
| Redis | L0 热记忆缓存（可选 L0 输出管道） | `localhost:6379` |
| Qdrant | L1 情景与 L2 摘要的向量副本 | `localhost:6333` |
| OpenAI-compatible LLM | L1 → L2 蒸馏、L3 → L2 再蒸馏（配置后可工作） | 按 `.env` |
| Embedding | 查询与写入的向量化（sentence-transformers 或 OpenAI） | 按 `.env` |

本地开发可直接启动依赖：

```bash
docker compose up -d
```

### 1.2 启动 Mems

```bash
cp .env.example .env
# 按需修改 .env（LLM/Embedding 等）
uv sync
python scripts/init_db.py
uv run python -m mems.main
```

默认监听 `http://localhost:8210`（`APP_PORT` 可改）。启动后可做两层验证：

```bash
# 轻量存活检查（不依赖任何外部组件）
curl http://localhost:8210/v1/mems/health

# 完整依赖健康 + 流水线状态
curl http://localhost:8210/v1/mems/status
```

FastAPI 自动生成 OpenAPI 文档，浏览器打开：

```text
http://localhost:8210/docs
```

### 1.3 运行模式

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `OUTBOX_WORKER_ENABLED` | `true` | 在 API 进程内运行 Outbox worker（向量同步、蒸馏、归档消费） |
| `SCHEDULER_ENABLED` | `true` | 启用调度器（每天 3:00 触发归档） |
| `L0_PIPELINE_ENABLED` | `false` | 是否把每次 L0 写入发布到 Redis Stream（见 [L0 输出管道](/zh/integration/l0-pipeline)） |

高吞吐部署可以把 Outbox worker 拆成独立进程：

```bash
uv run python -m mems.worker
```

## 2. 公开接口一览

所有请求体与响应体均为 JSON。当前没有内置鉴权；请在上层网关或反向代理处
处理认证与租户校验。

| Endpoint | Method | 用途 | 详细文档 |
| --- | --- | --- | --- |
| `/v1/mems/write` | `POST` | 写入记忆（L0 缓存 + L1 主记录） | [Write](/zh/api/write) |
| `/v1/mems/query` | `POST` | 跨 L0/L1/L2 召回相关记忆 | [Query](/zh/api/query) |
| `/v1/mems/redistill` | `POST` | 手动把 L3 归档再蒸馏到 L2 | [Redistill](/zh/api/redistill) |
| `/v1/mems/status` | `GET` | 依赖健康与流水线指标 | [Status](/zh/api/status) |
| `/v1/mems/health` | `GET` | 轻量存活探针 | [Health](/zh/api/health) |

核心工作流只有两个动作：`Remember`（写）与 `Recall`（查）。

```text
Agent Runtime
   │
   ├── 1. POST /v1/mems/query（需要记忆时）
   │         │
   │         ▼
   │   组装 Context → LLM → 生成回答
   │
   └── 2. POST /v1/mems/write（发现值得记住的内容时）
             │
             ▼
        Mems：L0 缓存 + L1 持久化 + 异步蒸馏/向量同步
```

先查再记、先记再查都可以，Mems 不规定时序。

## 3. 接入前的身份设计

所有业务字段都是**平级字符串**，通过请求传入：

```text
tenant_id / user_id / agent_id：硬隔离边界（agent_id 必填）
scope：软可见性标签（可空）
session_id：写入侧动作溯源（可空，不作为隔离）
```

推荐用法：

| 场景 | 推荐身份 |
| --- | --- |
| 单用户 Agent | `tenant_id` 可留空，固定一个 `user_id`，`scope="private"` |
| 多用户 SaaS | 每次请求显式传 `tenant_id` + `user_id`，不要只靠 `agent_id` |
| 多 Agent 系统 | 不同 Agent 使用不同 `agent_id`，user 维度仍按需传入 |
| 有共享/团队空间 | 用 `scope` 表达 `private`、`shared`、`team:*` |

重要实现语义：当前过滤是「给出即相等过滤、省略即不按该维度收窄」。也就是说，
一个只带 `agent_id` 的 Query 会看到该 agent 下所有 tenant/user/scope 的记忆；
多用户场景必须把 `user_id`（必要时还有 `tenant_id`）传给 write 与 query 两端，
且两侧保持一致，否则会写入与召回范围不一致。

`session_id` 只用于回答「这条记忆来自哪次会话」。query 请求不接受
`session_id`；L0/L1 的结果项会携带它，L2 知识通过 `source_l1_ids` 回溯原文。

## 4. 最小接入示例

### 4.1 写入一段记忆

```bash
curl -X POST http://localhost:8210/v1/mems/write \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "session_id": "session-001",
    "scope": "private",
    "content": "用户喜欢 Rust，并且偏好简洁的回答。",
    "messages": [
      {"role": "user", "content": "记住我喜欢 Rust。"},
      {"role": "assistant", "content": "已记住。"}
    ],
    "ttl_seconds": 1800,
    "importance_score": 0.8
  }'
```

成功返回示例：

```json
{
  "success": true,
  "tenant_id": "acme",
  "user_id": "user-42",
  "agent_id": "demo-agent",
  "session_id": "session-001",
  "scope": "private",
  "importance_score": 0.8,
  "content": "用户喜欢 Rust，并且偏好简洁的回答。",
  "messages": [
    {"role": "user", "content": "记住我喜欢 Rust。"},
    {"role": "assistant", "content": "已记住。"}
  ],
  "appended_count": 2,
  "persisted_to_l1": true,
  "l1_id": 101,
  "message": "Memory remembered and persisted to L1"
}
```

`content` 与 `messages` 至少提供一个。建议做法：

- 只传 `content`：直接记录一条值得记住的事实/结论；
- 只传 `messages`：Mems 会把消息按 `角色: 内容` 折叠成记忆文本；
- 两者都传：`content` 作为检索与蒸馏用的记忆文本，`messages` 作为可回放、
  可审计的结构化原文。

### 4.2 召回相关记忆

```bash
curl -X POST http://localhost:8210/v1/mems/query \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "scope": "private",
    "query": "用户喜欢什么语言？",
    "top_k": 5
  }'
```

成功返回示例：

```json
{
  "query": "用户喜欢什么语言？",
  "results": [
    {
      "source": "l1_episodic",
      "content": "用户喜欢 Rust，并且偏好简洁的回答。",
      "score": 1.14,
      "source_l1_ids": [101],
      "session_id": "session-001",
      "created_at": "2026-09-06T01:00:00Z",
      "tenant_id": "acme",
      "user_id": "user-42",
      "scope": "private"
    },
    {
      "source": "l0_hot",
      "content": "用户喜欢 Rust，并且偏好简洁的回答。",
      "score": 0.7,
      "source_l1_ids": [],
      "session_id": "session-001",
      "tenant_id": "acme",
      "user_id": "user-42",
      "scope": "private"
    }
  ],
  "total": 2
}
```

调用方拿到结果后：

1. 把 `content` 当作「可读记忆文本」；
2. 需要溯源时用 `source_l1_ids` 展开原文；
3. 自行决定哪些结果进入 Context、如何排序展示；
4. 不要把 `score` 当作正确性概率，也不要把 Mems 当 Context 管理器。

### 4.3 Python 调用示例

```python
import httpx

BASE = "http://localhost:8210"
IDENTITY = {
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "scope": "private",
}

def remember(content: str, importance: float = 0.5) -> dict:
    resp = httpx.post(
        f"{BASE}/v1/mems/write",
        json={**IDENTITY, "content": content, "importance_score": importance},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()

def recall(query: str, top_k: int = 5) -> list[dict]:
    resp = httpx.post(
        f"{BASE}/v1/mems/query",
        json={**IDENTITY, "query": query, "top_k": top_k},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["results"]
```

（示例使用了 `httpx`，请按你的运行环境安装相应 HTTP 客户端。）

## 5. 写入之后发生了什么

一次 `POST /v1/mems/write` 不是「只存一条记录」，而是进入一条最终一致流水线：

```text
Write Request
      │
      ├── L0：按 (identity + session) 保存/合并 Redis 热快照（TTL 内）
      │
      ├── L1：在 SQL 落一条 Episodic 主记录（同一事务内）
      │         ├── enqueue vector_sync（异步写 Qdrant 向量副本）
      │         └── enqueue 蒸馏触发事件
      │
      └── Outbox Worker 异步消费：
              ├── vector_sync → Qdrant
              └── 蒸馏（见下）
```

要点：

- **L0 是热缓存**：同一会话后续写入会合并短期缓冲区，并保留剩余 TTL；
  查询时每个 agent 最多取最近的活动快照（默认 10 个，见
  `L0_RECALL_MAX_SNAPSHOTS`）。
- **L1 是 SQL 真相源**：Qdrant 只是向量副本；副本失败不影响 L1 主记录，
  会由 Outbox 重试。
- **蒸馏不是同步的**：普通写入只 enqueue 事件；当待蒸馏记录达到
  `DISTILL_THRESHOLD`（默认 100）才按 agent 展开蒸馏。
- **高价值即时蒸馏**：`importance_score >= 0.8`（
  `DISTILL_HIGH_IMPORTANCE_THRESHOLD`）的记录写入后会立即触发 batch=1 的定向
  蒸馏，不等全局阈值。
- **LLM 未配置时**：蒸馏会被静默跳过（当前实现不产生日志，也不经 API 返回
  提示），写入本身不受影响。
- **归档是每天 3:00 自动执行**：把超过 `ARCHIVE_DAYS`（默认 30 天）的活跃 L1
  记录写入 JSONL（L3）并标记归档；归档后的 L1 不再参与在线召回。

如果只是做「近期对话速记 + 关键词召回」，不配置 LLM 也能工作；L2 蒸馏、
长期画像/事实/摘要需要配置可用的 OpenAI-compatible `OPENAI_*`。

## 6. 召回结果的分层语义

`source` 告诉调用方这条记忆来自哪一层，应使用不同语义：

| `source` | 含义 | 何时可信 | 时间字段 |
| --- | --- | --- | --- |
| `l0_hot` | 近期热快照（本会话/近 TTL 内） | 最新鲜，但只覆盖活跃期 | 无 `created_at` |
| `l1_episodic` | 原始情景记录 | 原文，适合回放/审计 | `created_at` |
| `l2_profile` | 稳定的用户画像断言 | 经过蒸馏，带 confidence | `last_verified_at` |
| `l2_fact` | 稳定的实体/关系事实 | 经过蒸馏，带 confidence | `last_verified_at` |
| `l2_event` | 提炼出的重要事件 | 有 importance 排序 | `created_at` |
| `l2_summary` | 滚动长期摘要 | 综合性强，需警惕过时 | `created_at` |

证据与可信度是两件事：

```text
source_l1_ids → 这条 L2 知识来自哪些 L1 原文（证据）
confidence     → 这条内容本身可信度（当前合并进 score，未独立返回）
```

需要向用户展示「依据」时，用 `source_l1_ids` 回溯 L1；不要把 L2 当成不可
证伪的结论。

## 7. 什么时候使用 redistill

`POST /v1/mems/redistill` 与在线召回无关，它解决「模型升级后，历史知识如何
跟着升级」：

```text
L3 JSONL（原始归档，与模型无关）
      │  手动触发
      ▼
提取 + 确定性对账
      │
      ▼
升级/新建 L2（当前 OPENAI_MODEL）
      │
      ├── 按 (内容指纹, model) 去重
      └── 冲突写入 conflict log，版本链向前
```

建议场景：

- 升级了 `OPENAI_MODEL` 后，对历史归档做一次再蒸馏；
- 希望旧对话沉淀出画像/事实/摘要时，手动补蒸馏；
- 需要按 agent、时间范围或最低 importance 过滤时。

详见 [Redistill API](/zh/api/redistill)。

## 8. 可观测性与排障

### 8.1 健康检查

```text
GET /v1/mems/health → 进程存活（静态返回）
GET /v1/mems/status → 数据库/Redis/Qdrant/调度器健康 + 流水线指标
```

容器与负载均衡建议：

- liveness probe 用 `/v1/mems/health`；
- readiness 或人工排障用 `/v1/mems/status`。

### 8.2 常见排障信号

| 现象 | 排查点 |
| --- | --- |
| write 成功但 query 没有新内容 | Outbox worker 未消费向量同步；检查 `OUTBOX_WORKER_ENABLED` 与 `pending_distill`/status |
| L2 一直不产生 | LLM 未配置或 `OPENAI_API_KEY` 无效；待蒸馏未达阈值 |
| 跨用户看到记忆 | 请求漏传 `user_id/tenant_id`（过滤语义见第 3 节） |
| 查不到刚写入的内容 | L0 快照 TTL 过期或词法无重叠；L1 向量副本同步滞后 |
| redistill 结果不符预期 | 已用当前模型处理过（去重跳过）；查看 `mems_redistill_progress` |

### 8.3 日志

应用日志输出在 `src/mems/` 各模块中：Outbox 重试、向量同步失败、蒸馏失败、
再蒸馏跳过均有 `logger.warning/error` 记录，按 `LOG_LEVEL` 控制。

## 9. 接入检查清单

上线前逐项确认：

- [ ] `.env` 中数据库/Redis/Qdrant 指向正确，`uv run python -m mems.main` 正常启动；
- [ ] 需要蒸馏时已配置 `OPENAI_*`（`/status` 不检查 LLM；可用一次真实写入后
      观察是否产生 L2 来验证）；
- [ ] write 与 query 两端使用同一套 `tenant_id/user_id/agent_id/scope`；
- [ ] 多用户场景每条请求都显式传 `user_id`，未依赖 `agent_id` 隔离；
- [ ] 理解 `content`/`messages` 的区别并选择适合的写入方式；
- [ ] 消费 query 结果时处理了 `source_l1_ids`、`created_at` 与 `session_id`
      的可空语义；
- [ ] liveness 探针指向 `/health`，业务监控指向 `/status`；
- [ ] 生产环境把 Outbox worker 视为关键消费者（进程内或独立 worker 至少一个
      在运行）。

## 10. 下一步

- 逐端点参数：读 [Query](/zh/api/query)、[Write](/zh/api/write)；
- 消费记忆事件：读 [L0 输出管道](/zh/integration/l0-pipeline)；
- 协议语义：读 [Memory Recall Protocol](/zh/introduction/memory-recall-protocol)。
