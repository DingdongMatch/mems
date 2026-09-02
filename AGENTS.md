# Mems 开发指南

> 本文件用于 AI 辅助开发，帮助理解项目结构和开发规范。

---

## 1. 项目概述

**Mems** 是面向 AI Agent 的记忆基础设施。通过四层冷热解耦架构，提供低成本检索、结构化进化和跨世纪保存的记忆基座。

Mems 的核心理念：

```text
Mems 存储和召回记忆，不管理运行时状态
Mems 记忆随模型进化：L3 归档可再蒸馏，模型升级后无损升级 L2
```

会话上下文、任务目标、活动计划、临时变量由调用方自行管理。

### 核心架构

```
 用户入口 → L0 (Redis 热缓存) → L1 (SQL + Qdrant 记忆账本)
                                     ├→ L2 (画像/事实/事件/摘要/冲突日志)
                                     └→ L3 (JSONL 归档)
        └── L3 → L2 再蒸馏（手动，模型升级后触发，版本链升级）
```

### 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI |
| ORM | SQLModel |
| 向量数据库 | Qdrant |
| 缓存 | Redis |
| 调度器 | APScheduler |
| 包管理 | uv |

---

## 2. 项目结构

```
src/mems/
├── main.py               # FastAPI 入口 + lifespan（含进程内 Outbox worker）
├── config.py             # pydantic-settings 配置（从 .env 读取）
├── redistill.py          # CLI 入口 python -m mems.redistill
├── worker.py             # 独立 Outbox worker 入口 python -m mems.worker
│
├── domain/               # 领域层：实体/聚合/值对象/领域服务与端口（不依赖基础设施）
│   ├── identity.py       # MemoryIdentity / TimeRange / Importance 值对象
│   ├── memory.py         # L0 WorkingMemory / L1 EpisodicMemory 聚合
│   ├── knowledge.py      # L2 Profile/Fact/Event/Summary/ConflictLog 实体
│   ├── archive.py        # L3 ArchiveBatch 聚合
│   ├── outbox.py         # OutboxEvent 值对象与事件类型
│   ├── events.py         # L0 管道集成事件
│   ├── ports.py          # 仓储/外部服务/UoW/HealthCheck/Outbox 端口（Protocol）
│   └── services/         # 蒸馏策略、排序、对账等纯领域服务
│
├── application/          # 应用服务与用例编排（只依赖 domain）
│   ├── commands.py       # 命令/结果/读模型
│   ├── write_service.py  # RememberMemory 用例
│   ├── query_service.py  # RecallMemories 编排器
│   ├── status_service.py # 状态与监控
│   ├── distill_service.py# L1→L2 蒸馏
│   ├── archive_service.py# L1→L3 归档
│   ├── redistill_service.py # 再蒸馏纯函数辅助
│   ├── vector_sync_service.py # vector_sync Outbox 处理器
│   └── retrievers.py     # 按来源召回检索器
│
├── infrastructure/       # 基础设施实现
│   ├── external/         # Redis/Qdrant/Embedding/LLM/调度器/时钟/JSONL 适配器
│   └── persistence/      # SQLModel 模型、mapper、仓储、UoW、健康检查
│
└── interfaces/           # 接口与组合根
    ├── api/              # FastAPI 路由、请求 schema、依赖注入
    └── bootstrap.py      # 后台服务装配、触发器、OutboxWorker

docs/
├── package.json          # VitePress 文档站脚本
└── src/
    ├── .vitepress/       # 文档站配置与主题
    ├── *.md              # 英文文档（root locale）
    └── zh/               # 中文文档
```

---

## 3. 开发规范

### 3.1 新增 API 路由

1. 在 `interfaces/api/` 创建新路由文件
2. 注册到 `main.py` 的 `app.include_router()`
3. 使用依赖注入获取 session、仓储、端口与服务

```python
# interfaces/api/example.py
from fastapi import APIRouter, Depends
from sqlmodel import Session

from mems.infrastructure.persistence.database import get_session

router = APIRouter(prefix="/example", tags=["Example"])

@router.post("")
async def create_example(session: Session = Depends(get_session)):
    ...
```

### 3.1.1 当前公开 API 约定

- `POST /v1/mems/write`: 写入记忆到 L0/L1 — 只接收 `content` / `messages` / identity 字段，不接收运行时状态
- `POST /v1/mems/query`: 跨 L0/L1/L2 混合召回——L0 活跃会话快照 + L1/L2 向量与
  SQL 候选；词法打分使用 CJK bigram + ASCII 整词，各来源 base 归一化到 0-1 后
  叠加意图与新鲜度加分，同分按创建时间倒序
- `POST /v1/mems/redistill`: 手动触发 L3→L2 再蒸馏，用当前 `OPENAI_MODEL`
  升级 L2；按「内容指纹 + 模型名」去重，模型更换后旧数据自动进入待重蒸队列
- `GET /v1/mems/status`: 查看系统与流水线状态
- `GET /v1/mems/health`: 轻量健康检查

推荐对外接入流与文案统一遵循：

- `Remember -> Recall`
- 对外表述优先与 `README.md`、`README_zh.md`、Swagger、文档站保持一致
- 如果修改公开 API、首页文案或系统定位，需同步检查 README、`src/mems/main.py` 的 OpenAPI 描述，以及 `docs/src/` 文档站内容
- 如果新增或修改 API 模型字段，需检查 `AGENTS.md` 中的模型说明

### 3.1.2 Memory Identity Model

当前公开 memory API 支持以下 identity 字段：

- `tenant_id`: 可选租户 / 组织边界
- `user_id`: 多用户 Agent 场景下建议显式传入
- `agent_id`: Agent 边界
- `session_id`: 可选会话来源标识
- `scope`: 上层业务自定义的可见性标签

推荐原则：

- `tenant_id / user_id / agent_id` 是硬边界
- `session_id` 是可选的动作溯源标识，Mems 不依赖它做上下文管理
- `scope` 是软可见性标签，由上层业务定义
- 默认不要只靠 `agent_id` 做多用户隔离

### 3.1.3 核心模型说明

**MemsWriteRequest**
```python
class MemsWriteRequest(BaseModel):
    tenant_id: str | None
    user_id: str | None
    agent_id: str
    session_id: str | None
    scope: str | None
    content: str | None       # 直接记忆文本
    messages: list[MemsMessage]  # 来源消息（可选）
    ttl_seconds: int
    importance_score: float = 0.5  # 记忆重要度（0-1），用于蒸馏优先级
```

规则：
- `content` 和 `messages` 至少提供一个
- 如果同时提供，`content` 作为记忆文本用于检索/蒸馏，`messages` 作为结构化原文保留
- `importance_score` 达到 `DISTILL_HIGH_IMPORTANCE_THRESHOLD`（默认 0.8）时，
  写入后立即对该 agent 做 batch=1 的定向蒸馏；否则走全局阈值检查
- 没有 `active_plan`、`temp_variables`、`metadata` 字段

**MemsL1Episodic**（数据库表）
```python
class MemsL1Episodic(SQLModel, table=True):
    tenant_id / user_id / agent_id / session_id / scope
    content: str                     # 记忆文本（用于检索和蒸馏）
    messages_json: list[dict]        # 结构化来源消息（用于回放和审计）
    vector_id: str                   # 向量副本 id
    importance_score: float
    is_distilled / is_archived
    vector_status / archive_status
    created_at
```

**MemsL0Working**（Redis 快照）
```python
class MemsL0Working(BaseModel):
    tenant_id / user_id / agent_id / session_id / scope
    content: str | None
    short_term_buffer: list[dict]
    expires_at: datetime
```

**QueryResultItem**
```python
class QueryResultItem(BaseModel):
    source: str        # l0_hot / l1_episodic / l2_profile / l2_fact / l2_event / l2_summary
    content: str
    score: float
    created_at: datetime | None
    source_l1_ids: list[int]
    session_id: str | None
    tenant_id / user_id / scope
```

### 3.2 新增数据库模型

在 `infrastructure/persistence/models.py` 中添加 SQLModel 类，并在
`infrastructure/persistence/mappers.py` 中补充领域对象与 ORM 的映射：

```python
class MyModel(SQLModel, table=True):
    __tablename__ = "my_model"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
```

### 3.3 新增服务

应用用例服务放在 `application/`，只通过构造器注入领域端口；基础设施适配器
放在 `infrastructure/external/` 或 `infrastructure/persistence/`：

- **同步函数**: 直接实现
- **异步函数**: 使用 `async def`
- **单例服务**: 使用模块级单例（如 `redis_service`）

```python
# application/example_service.py
class ExampleService:
    def __init__(self):
        self._cache = {}

    async def process(self, data):
        ...

example_service = ExampleService()

async def get_example_service() -> ExampleService:
    return example_service
```

### 3.4 配置管理

配置通过 `config.py` 的 `Settings` 类管理：

```python
# config.py
class Settings(BaseSettings):
    MY_CONFIG: str = "default"

settings = Settings()
```

使用时导入：
```python
from mems.config import settings
```

### 3.5 状态端点注意事项

状态端点 `GET /v1/mems/status` 使用 FastAPI 依赖注入获取 Redis、VectorService 和 Session。
新增的健康检查项也应当通过 `Depends` 注入，而不是直接调用模块单例（否则测试替身无法覆盖）。

---

## 4. 核心模块说明

### 4.1 数据库连接

```python
# 获取 session
session: Session = Depends(get_session)
```

应用服务通过 `UnitOfWork` 端口统一 commit/rollback，仓储不再各自提交事务；
健康检查走 `HealthCheckPort`（`SqlDatabaseHealthCheck`）。

### 4.2 Redis 服务

```python
# 获取 redis 服务
redis: RedisService = Depends(get_redis_service)  # 测试环境自动注入 FakeRedisService
l0 = await redis.write(tenant_id=..., user_id=..., agent_id=..., session_id=..., messages=..., content=...)
l0 = await redis.read(agent_id=..., session_id=..., tenant_id=..., user_id=..., scope=...)
```

L0 Redis key 统一复用 `MemoryIdentity.redis_key()`，避免适配器重复实现 key 规则。

### 4.3 Embedding 服务

```python
embedding_service = await get_embedding_service()
vectors = await embedding_service.embed(["text"])
```

### 4.4 向量服务

```python
vector_service = await get_vector_service()  # 使用 Depends 注入以保证测试覆盖
await vector_service.upsert_episodic("collection_name", memory, vector)
await vector_service.upsert_summary("collection_name", summary, vector)
results = await vector_service.search("collection_name", query_vector, filters={...})
```

payload 构造下沉到向量适配器，应用层只传领域对象，不感知 Qdrant 数据形状。

### 4.5 Outbox 与 Worker

- `domain/outbox.py` 定义 `OutboxEvent` 与 `OutboxEventType`
  （`vector_sync` / `archive_agent` / `distill_agent` / `distill_due`）。
- `mems_outbox` 表 + `OutboxRepository` 端口承载事务性副作用；写入 L1 时把
  `vector_sync` 与蒸馏触发事件与主记录在同一事务提交。
- `interfaces/bootstrap.py` 中的 `OutboxWorker` 原子 claim 事件（`pending`→
  `claimed`，带 owner 与租约超时），按事件类型分发给处理器，成功 `done`、
  失败指数退避后回到 `pending` 重试，实现最终一致与分布式互斥。
- 默认在 `main.py` lifespan 内启动进程内 worker；`python -m mems.worker`
  可作为独立 worker 水平扩展。

---

## 5. 自动化流水线

### 5.1 L0→L1 自动双写

用户调用 `/v1/mems/write` 时，L0 缓存同步写，L1 主记录与 `vector_sync` 以及
蒸馏触发事件在同一事务提交；向量副本由 Outbox worker 异步最终一致。

### 5.2 蒸馏任务

- **阈值触发**: 普通写入 enqueue `distill_due` 事件（按 `aggregate_id=global`
  去重），worker 检查未蒸馏记录；达到 `DISTILL_THRESHOLD`（默认 100）后展开
  为按 agent 的 `distill_agent` 事件
- **高价值即时**: `importance_score >= DISTILL_HIGH_IMPORTANCE_THRESHOLD`
  （默认 0.8）的记录写入后 enqueue `distill_agent`（batch=
  `DISTILL_IMMEDIATE_BATCH_SIZE`，默认 1），不等待全局阈值
- **无定时任务**: 蒸馏不依赖 cron，由写入路径 enqueue、Outbox worker 消费；
  并发互斥由 Outbox 状态机 claim 承担，不依赖进程内布尔锁
- **批内排序**: 非 force 蒸馏按 `importance_score DESC, created_at ASC` 取批，
  保证高价值优先处理
- **运行前提**: 当前蒸馏依赖可用的 OpenAI-compatible LLM 配置

```python
# application/distill_service.py
class DistillApplicationService:
    async def distill(self, agent_id, batch_size, force, min_importance) -> DistillResponse:
        ...
```

### 5.3 归档任务

- **定时触发**: 每天 3:00 AM，调度器 enqueue `archive_agent` 事件；worker 执行
  JSONL 文件写入与 `mark_archived`。

```python
# interfaces/bootstrap.py
async def trigger_archive_automatically(agent_id: str = None) -> Dict:
    ...
```

### 5.4 再蒸馏任务（手动）

- **触发方式**: `POST /v1/mems/redistill` 或 CLI `python -m mems.redistill`
- **输入输出**: 读取 L3 JSONL 归档，复用 `DistillApplicationService.process_source`
  走提取 + 确定性对账 + 版本链升级 L2；`source_id` 保留原始 L1 id，并记录
  `l3_path/l3_line` 兜底证据指针
- **对账收敛**: LLM 只做结构化提取，`ReconciliationService` 是唯一确定性对账
  入口，不再有 LLM 对账与代码对账的双重逻辑
- **去重**: 表 `mems_redistill_progress(content_hash, model)`，唯一键
  `(content_hash, model)`，`model` = 运行时 `settings.OPENAI_MODEL`
- **过滤**: 支持 agent / created_at 时间范围 / min_importance，单次上限
  `REDISTILL_BATCH_SIZE`（默认 100）
- **容错**: LLM 失败或损坏行跳过并记日志、不标记进度，下次可重试

---

## 6. 调度器使用

### 6.1 注册定时任务

当前调度器只负责归档任务（蒸馏为阈值触发，无定时任务）：

```python
from mems.infrastructure.external.scheduler import scheduler_service
from mems.interfaces.bootstrap import trigger_archive_automatically

# 在 main.py lifespan 中注册
scheduler_service.add_archive_job(trigger_archive_automatically, hour=3, minute=0)
scheduler_service.start()
```

### 6.2 任务函数签名

```python
async def my_job(agent_id: str = None) -> Dict[str, Any]:
    """任务函数必须返回 Dict"""
    return {"status": "ok"}
```

---

## 7. 常见模式

### 7.1 批量处理

```python
async def batch_process(items: List, batch_size: int = 50):
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        await process_batch(batch)
```

### 7.2 错误处理

```python
try:
    result = await service.operation()
except Exception as e:
    logger.error(f"Operation failed: {e}")
    raise HTTPException(status_code=500, detail=str(e))
```

### 7.3 事务处理

```python
uow: UnitOfWork = ...  # 通过构造器注入
try:
    repository.add(record)
    uow.commit()
except Exception:
    uow.rollback()
    raise
```

### 7.4 第三方 Agent 接入时序

Mems 不管理会话上下文。推荐时序：

1. `POST /v1/mems/query` — 召回相关记忆
2. 调用方自行组装 prompt 并生成回答
3. `POST /v1/mems/write` — 将值得记住的内容写入系统

先记再查或先查再记都可以，由调用方决定。

---

## 8. 测试

```bash
# 运行测试（不需要外部服务，全 Fake 注入）
uv run pytest

# 代码检查
uv run ruff check src/

# 类型检查
uv run mypy src/

# 文档站构建检查
cd docs && npm run build
```

测试环境使用 `tests/conftest.py` 中的 Fake store/vector/embedding/LLM，不需要
运行 Docker 服务。测试默认关闭 Outbox worker（`OUTBOX_WORKER_ENABLED=False`），
需要验证最终一致时手动 `worker.run_once()` 排空。新增端点时也需要使用
`Depends` 注入以保证测试覆盖。

---

## 9. 启动服务

```bash
# 开发模式
uv run python -m mems.main

# 或
uvicorn mems.main:app --reload --port 8210
```

---

## 10. 环境变量

详见 `.env.example`，核心配置：

| 配置 | 说明 |
|------|------|
| `DATABASE_URL` | PostgreSQL 连接（`postgresql+psycopg`） |
| `REDIS_HOST/PORT` | Redis 连接 |
| `QDRANT_URL` / `QDRANT_HOST/PORT` | Qdrant 连接 |
| `EMBEDDING_PROVIDER` | embedding 模型 (sentence-transformers/openai) |
| `OPENAI_*` | OpenAI-compatible LLM / embedding 配置 |
| `SCHEDULER_ENABLED` | 启用调度器 |
| `OUTBOX_WORKER_ENABLED` | 是否在 lifespan 内启动进程内 Outbox worker |
| `WORKER_POLL_SECONDS` | 独立 worker 轮询间隔 |
| `REDISTILL_BATCH_SIZE` | 每次手动再蒸馏最多处理的 L3 记录数 |

---

## 11. 端口规划

| 服务 | 端口 | 说明 |
|------|------|------|
| Mems API | 8210 | FastAPI 主服务 |
| MCP Server | 8210 | MCP 协议服务 (预留) |
| Qdrant | 6333 | 向量数据库 |
| Redis | 6379 | L0 热记忆缓存 |

---

## 12. 开源协议

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) file for details.

---

*最后更新: 2026-09-02*
