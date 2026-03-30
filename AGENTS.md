# Mems 开发指南

> 本文件用于 AI 辅助开发，帮助理解项目结构和开发规范。

---

## 1. 项目概述

**Mems** 是一个支持多 Agent 隔离、长周期（100年级）的分层记忆系统，面向第三方 Agent 提供清晰的公开 memory API。

### 核心架构

```
 L0 (Redis) → L1 (SQL+Qdrant) → L2 (画像/事实/事件/摘要/冲突日志) → L3 (归档)
     ↑
  用户入口
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
├── main.py              # FastAPI 入口 + lifespan + 调度器初始化
├── config.py            # pydantic-settings 配置（从 .env 读取）
├── models.py            # SQLModel 数据库模型
├── schemas/            # Pydantic 请求/响应模型
├── database.py          # SQLModel engine + init_db()
├── dependencies.py      # FastAPI 依赖注入
│
├── services/
│   ├── scheduler.py     # APScheduler 调度服务（单例）
│   ├── redis_service.py # Redis L0 服务（单例）
│   ├── l0_sync.py      # L0→L1 同步函数
│   ├── vector_service.py # Qdrant 官方 Async SDK 封装
│   ├── embedding.py     # Embedding 抽象层（策略模式）
│   ├── llm_client.py    # OpenAI-compatible LLM 客户端
│   ├── distill.py       # L1→L2 蒸馏服务
│   ├── archive.py      # L1→L3 归档服务
│   └── jsonl_utils.py  # JSONL 读写工具
│
├── static/
│   └── simulator_playground.html # Simulator 可视化调试页
│
└── routers/
    ├── memories.py     # /memories/write + /memories/turns + /memories/context + /memories/search
    ├── simulator.py    # /simulator/chat + /simulator/chat/stream + /simulator/playground
    └── monitor.py      # /monitor/status
```

---

## 3. 开发规范

### 3.1 新增 API 路由

1. 在 `routers/` 创建新文件
2. 注册到 `main.py` 的 `app.include_router()`
3. 使用依赖注入获取 `session` 和 `redis`

```python
# routers/example.py
from fastapi import APIRouter, Depends
from sqlmodel import Session

from mems.database import get_session

router = APIRouter(prefix="/example", tags=["Example"])

@router.post("")
async def create_example(session: Session = Depends(get_session)):
    ...
```

### 3.1.1 当前公开 API 约定

- `POST /memories/write`: 写入工作记忆快照
- `POST /memories/turns`: 按 turn 追加会话消息，适合第三方 Agent 接入
- `GET /memories/context`: 获取当前 `session_id` 的 live 首页，并可通过 `before_id` 分页读取更早的 L1 历史
- `POST /memories/search`: 做长期语义检索
- `POST /simulator/chat`: 官方参考 Agent，只通过公开 API 模拟第三方接入
- `POST /simulator/chat/stream`: 流式返回 simulator 输出
- `GET /simulator/playground`: 浏览器调试页，展示聊天、prompt、context、search trace

### 3.1.2 Memory Identity Model

当前公开 memory API 已支持以下 identity 字段：

- `tenant_id`: 可选租户 / 组织边界
- `user_id`: 多用户 Agent 场景下建议显式传入
- `agent_id`: Agent 边界
- `session_id`: 会话边界
- `scope`: 上层业务自定义的可见性标签

推荐原则：

- `tenant_id / user_id / agent_id / session_id` 是硬边界
- `scope` 是软可见性标签，由上层业务定义
- 默认不要只靠 `agent_id` 做多用户隔离

### 3.2 新增数据库模型

在 `models.py` 中添加 SQLModel 类：

```python
class MyModel(SQLModel, table=True):
    __tablename__ = "my_model"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
```

### 3.3 新增服务

服务放在 `services/` 目录：

- **同步函数**: 直接实现
- **异步函数**: 使用 `async def`
- **单例服务**: 使用模块级单例（如 `redis_service`）

```python
# services/example.py
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

---

## 4. 核心模块说明

### 4.1 数据库连接

```python
# 获取 session
session: Session = Depends(get_session)
```

### 4.2 Redis 服务

```python
# 获取 redis 服务
redis: RedisService = Depends(get_redis_service)
await redis.write(tenant_id=..., user_id=..., agent_id=..., session_id=..., ...)
```

### 4.3 Embedding 服务

```python
# 获取 embedding 服务
embedding_service = await get_embedding_service()
vectors = await embedding_service.embed(["text"])
```

### 4.4 向量服务

```python
# 获取向量服务
vector_service = await get_vector_service()
await vector_service.upsert("collection_name", points)
results = await vector_service.search("collection_name", query_vector, filters={...})
```

---

## 5. 自动化流水线

### 5.1 L0→L1 自动双写

用户调用 `/memories/write` 或 `/memories/turns` 时，可自动同步到 L1。

### 5.2 蒸馏任务

- **阈值触发**: 未蒸馏 L1 > 100 条
- **定时检查**: 每天 2:00 AM 执行阈值检查
- **运行前提**: 当前蒸馏依赖可用的 OpenAI-compatible LLM 配置

```python
# services/distill.py
async def trigger_distill_automatically(agent_id: str = None) -> Dict:
    ...
```

### 5.3 归档任务

- **定时触发**: 每天 3:00 AM

```python
# services/archive.py
async def trigger_archive_automatically(agent_id: str = None) -> Dict:
    ...
```

---

## 6. 调度器使用

### 6.1 注册定时任务

```python
from mems.services.scheduler import scheduler_service

# 在 main.py lifespan 中注册
scheduler_service.add_distill_job(func, hour=2, minute=0)
scheduler_service.add_archive_job(func, hour=3, minute=0)
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
try:
    session.add(record)
    session.commit()
except Exception:
    session.rollback()
    raise
```

### 7.4 第三方 Agent 接入时序

推荐统一按照下面时序设计和调试：

1. `GET /memories/context` 获取 live 首页，必要时继续带 `before_id` 向前翻历史
2. `POST /memories/search`
3. 第三方 Agent 组装 prompt 并生成回答
4. `POST /memories/turns`

`/simulator/chat` 就是这条时序的官方参考实现，不能绕过公开 API 直接调用内部 service。

---

## 8. 测试

```bash
# 运行测试
uv run pytest

# 代码检查
uv run ruff check src/

# 类型检查
uv run mypy src/
```

---

## 9. 启动服务

```bash
# 开发模式
uv run python -m mems.main

# 或
uvicorn src.mems.main:app --reload --port 8000
```

---

## 10. 环境变量

详见 `.env.example`，核心配置：

| 配置 | 说明 |
|------|------|
| `DATABASE_URL` | 数据库连接 |
| `REDIS_HOST/PORT` | Redis 连接 |
| `QDRANT_URL` / `QDRANT_HOST/PORT` | Qdrant 连接 |
| `EMBEDDING_PROVIDER` | embedding 模型 (sentence-transformers/openai) |
| `OPENAI_*` | OpenAI-compatible LLM / embedding 配置 |
| `SCHEDULER_ENABLED` | 启用调度器 |

---

## 11. 端口规划

| 服务 | 端口 | 说明 |
|------|------|------|
| Mems API | 8000 | FastAPI 主服务 |
| MCP Server | 8210 | MCP 协议服务 (预留) |
| Qdrant | 6333 | 向量数据库 |
| Redis | 6379 | L0 工作记忆 |

---

## 12. 开源协议

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) file for details.

---

*最后更新: 2026-03-23*
