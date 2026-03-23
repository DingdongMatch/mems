# Mems 分层记忆系统

> 🤖 多 Agent 隔离 | 🔄 全自动 pipeline | 📚 长周期记忆 | 🧠 LLM 驱动蒸馏

支持多 Agent 隔离、长周期（100年级）的全自动化分层记忆系统。

[English](README.md) | [中文](README_zh.md)

## 核心特性

- **全自动化流水线**: L0→L1→L2→L3 完全自动化
- **四层记忆架构**: L0(Redis) → L1(SQL+Vector) → L2(SQL+JSONL) → L3(JSONL归档)
- **多租户隔离**: 每个 Agent 独立存储，数据物理隔离
- **向量检索**: 基于 Qdrant 的语义搜索
- **记忆蒸馏**: L1→L2 自动提取知识三元组
- **百年归档**: 纯文本 JSONL 格式，跨时代可读

## 技术栈

- Python 3.12+ / FastAPI / SQLModel
- Qdrant (向量数据库) / Redis (L0 工作记忆)
- APScheduler (自动化调度) / SQLite

## 分层定义

当前仓库已经实现了一个可运行的四层记忆流水线。若从产品设计和后续演进角度看，这四层可以进一步定义为：

| 层级 | 逻辑定义 |
|------|----------|
| L0: 瞬时层 | 正在进行的对话、当前任务的思考链 (CoT) |
| L1: 情景层 | 原始对话记录、最近发生的特定事件细节 |
| L2: 语义层 | 提炼后的用户画像、事实知识、行为偏好与滚动摘要 |
| L3: 归档层 | 历史全量日志、年度总结、不再活跃的旧知识 |

## 快速开始

### 1. 启动服务

```bash
# 安装依赖
uv sync

# 启动 Docker 服务 (Redis + Qdrant)
docker compose up -d

# 初始化数据库
python scripts/init_db.py

# 启动 FastAPI
uv run python -m mems.main
```

### 2. 验证

```bash
# 健康检查
curl http://localhost:8000/health

# 写入记忆
curl -X POST http://localhost:8000/memories/write \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "test", "session_id": "s1", "messages": [{"role": "user", "content": "测试内容"}]}'

# 搜索
curl -X POST http://localhost:8000/memories/search \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "test", "query": "测试"}'

# 监看检测
curl http://localhost:8000/monitor/status
```

## 自动化流水线

```
用户写入记忆 → 自动 L0 → 自动 L1 → 自动 L2 → 自动 L3
     │
     └─→ /memories/write → L0 (Redis)
                             │
                             │ (自动持久化)
                             ▼
                           L1 (SQL + Qdrant + JSONL)
                      │
      ┌───────────────┴───────────────┐
      │                               │
      │ (阈值 >100条 或 每天 2:00)   │ (每天 3:00)
      ▼                               ▼
    L2 (知识三元组)                L3 (归档)
```

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/memories/write` | POST | 写入记忆，由系统自动持久化到 L0/L1 |
| `/memories/search` | POST | 查询记忆，内部统一执行情景/画像/事实/摘要混合检索 |
| `/monitor/status` | GET | 查看服务健康、调度状态和流水线积压 |
| `/health` | GET | 轻量级进程健康探针 |

当前归档采用冷存模式：旧记忆会导出到 L3 并标记为已归档，但仍保留在线查询能力。

当前蒸馏流水线采用 `Filter -> Extract -> Reconcile -> Commit`，L2 会分别沉淀为画像项、事实项、事件、滚动摘要与冲突日志。

## 文档

- [技术文档](docs/TECHNICAL.md) - 完整的技术架构和 API 说明
- [Vibe Coding 指导](docs/VIBE_CODING.md) - 如何高效使用 AI 辅助开发
- [Technical Docs (EN)](docs/TECHNICAL_en.md) - English technical documentation
- [Vibe Coding Guide (EN)](docs/VIBE_CODING_en.md) - English Vibe Coding guide
- [English README](README.md)

## Swagger API 文档

启动服务后访问:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

## 项目结构

```
mems/
├── src/mems/
│   ├── main.py              # FastAPI 入口 + 调度器
│   ├── config.py            # 配置管理
│   ├── models.py            # SQLModel 定义
│   ├── schemas.py           # Pydantic 模型
│   ├── services/
│   │   ├── scheduler.py     # APScheduler 调度
│   │   ├── redis_service.py # L0 服务
│   │   ├── l0_sync.py      # L0→L1 同步
│   │   ├── vector_service.py
│   │   ├── embedding.py
│   │   ├── distill.py       # 蒸馏 + 阈值检测
│   │   └── archive.py       # 归档 + 自动触发
│   └── routers/
│       ├── memories.py      # /memories/write + /memories/search
│       └── monitor.py       # /monitor/status
├── scripts/
│   ├── init_db.py
│   └── reader.py           # L3 读取器 (纯标准库)
└── storage/                 # JSONL 数据存储
```

## 开发

```bash
# 运行测试
uv run pytest

# 代码检查
uv run ruff check src/
```

## 端口规划

| 服务 | 端口 | 说明 |
|------|------|------|
| Mems API | 8000 | FastAPI 主服务 |
| MCP Server | 8210 | MCP 协议服务 (预留) |
| Qdrant | 6333 | 向量数据库 (外部) |
| Redis | 6379 | L0 工作记忆 (外部) |

## 开源协议

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) file for details.
