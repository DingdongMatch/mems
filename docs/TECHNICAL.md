# Mems 分层记忆系统 - 技术文档

## 1. 项目概述

**Mems** 是一个支持多 Agent 隔离、长周期（100年级）、低成本且供应商无关的分层记忆系统。它通过"冷热分离"和"异步蒸馏"技术，将碎片化的对话转变为持久的结构化知识。

### 核心特性

- **全自动化流水线**: L0→L1→L2→L3 完全自动化
- **四层记忆架构**: L0(Redis) → L1(SQL+Vector) → L2(SQL+JSONL) → L3(JSONL归档)
- **多租户隔离**: 每个 Agent 独立存储，数据物理隔离
- **向量检索**: 基于 Qdrant 的语义搜索
- **记忆蒸馏**: L1→L2 自动提取知识三元组
- **百年归档**: 纯文本 JSONL 格式，跨时代可读

---

## 2. 技术栈

| 组件 | 技术选型 | 说明 |
|------|----------|------|
| 语言 | Python 3.12+ | 现代异步支持 |
| Web 框架 | FastAPI | 高性能、易用 |
| ORM | SQLModel | Pydantic + SQLAlchemy |
| 向量数据库 | Qdrant | 高效向量检索 |
| 缓存 | Redis | L0 工作记忆 |
| 调度器 | APScheduler | 定时任务 |
| 数据库 | SQLite (开发) / PostgreSQL (生产) | 关系型存储 |
| 包管理 | uv | 现代 Python 包管理 |

---

## 3. Embedding 模型

### 3.1 作用说明

Embedding 模型是 **L1 层（情景记忆）的核心组件**，用于实现语义搜索功能。

```
用户查询 → Embedding 模型 → 512维向量 → Qdrant 向量搜索 → 相关记忆
```

#### 主要用途

1. **搜索时** (search 接口)
   - 将用户查询转为向量 → 在 Qdrant 中做相似度搜索 → 返回最相关的 L1 记忆

2. **写入时** (l0_sync)
   - L0 数据同步到 L1 时，生成向量并存入 Qdrant

#### 数据流示意

```
搜索请求: "用户喜欢什么"
     ↓
Embedding 模型 (bge-small-zh-v1.5) → [0.12, -0.34, 0.56, ...]
     ↓
Qdrant 向量搜索 (余弦相似度) → 找到最相似的 L1 记录
     ↓
返回结果 (source: l1_episodic, score: 0.95)
```

### 3.2 模型配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `EMBEDDING_PROVIDER` | 提供商 (sentence-transformers/openai) | sentence-transformers |
| `SENTENCE_TRANSFORMERS_MODEL` | 本地模型名称 | BAAI/bge-small-zh-v1.5 |
| `OPENAI_EMBEDDING_MODEL` | OpenAI 模型 | text-embedding-3-small |

#### 本地模型 (默认)

- **模型**: `BAAI/bge-small-zh-v1.5`
- **向量维度**: 512
- **特点**: 中文优化，轻量级，无需 API 调用

#### OpenAI (可选)

如需使用 OpenAI embedding，在 `.env` 中配置:

```bash
EMBEDDING_PROVIDER=openai
OPENAI_EMBEDDING_API_KEY=your-key
```

---

## 4. 分层定义

当前代码仓库已经提供了一个可运行的参考实现。若从系统设计和后续生产演进角度看，这四层可以定义为：

| 层级 | 逻辑定义 |
|------|----------|
| L0: 瞬时层 | 正在进行的对话、当前任务状态、进行中的思考链 (CoT) |
| L1: 情景层 | 原始对话记录、最近发生的特定事件细节 |
| L2: 语义层 | 提炼后的用户画像、事实知识、行为偏好 |
| L3: 归档层 | 历史全量日志、年度总结、不再活跃的旧知识 |

---

## 5. 架构设计

### 5.1 四层记忆模型

```
┌─────────────────────────────────────────────────────────────────┐
│                     L0: 工作记忆 (Redis)                        │
│                 自动双写 L1，用户无感知                          │
│  - 当前会话 Context                                            │
│  - 活跃任务状态                                                │
│  - 临时变量                                                    │
│  - TTL 自动过期                                                │
└─────────────────────────────────────────────────────────────────┘
                    │ (实时自动双写)
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                   L1: 情景记忆 (SQLite + Qdrant + JSONL)        │
│  - 原始对话 100% 保留                                          │
│  - 向量嵌入支持语义检索                                        │
│  - 文本优先：同步写 JSONL                                      │
│  - 等待蒸馏/归档                                               │
└─────────────────────────────────────────────────────────────────┘
                    │                                    │
                    │ (阈值/定时触发)                      │ (定时触发)
                    ▼                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                   L2: 语义记忆 (SQLite + JSONL)                 │
│  - 知识三元组 (主体-关系-客体)                                  │
│  - 冲突检测 + 版本管理                                         │
│  - 溯源 L1 记录                                                │
│  - 触发条件：>100条未蒸馏 或 每天 2:00 AM                       │
└─────────────────────────────────────────────────────────────────┘
                    │
                    │ (定时触发)
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                   L3: 归档记忆 (JSONL 文件)                     │
│  - 超过 30 天数据                                              │
│  - 纯文本格式，百年可读                                        │
│  - 自包含 reader.py (纯标准库)                                 │
│  - 触发条件：每天 3:00 AM                                      │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 自动化数据流

```
用户 ──→ /l0/write ──→ L0 (Redis)
                        │
                        │ (自动同步，毫秒级)
                        ▼
                   L1 (SQL + Qdrant + JSONL)
                        │
    ┌───────────────────┴───────────────────┐
    │                                       │
    │ (阈值: >100条未蒸馏)                  │ (定时: 每天 3:00)
    ▼                                       ▼
  L2 (知识三元组)                        L3 (归档)
    │                                       │
    │                                       │
    └───────────────────┬───────────────────┘
                        │
                        ▼ (可选检索)
                    用户查询
```

---

## 6. 自动化流水线详解

### 6.1 L0 → L1 自动双写

当用户调用 `/l0/write` 时，系统自动完成：

1. 写入 Redis (L0) - 带 TTL
2. 自动同步到 L1 - SQLite + Qdrant + JSONL

**配置项**:
- `L0_AUTO_SYNC_L1=true` - 启用自动双写
- `L0_DEFAULT_TTL_SECONDS=1800` - L0 默认 TTL

### 6.2 L1 → L2 自动蒸馏

**触发条件**（满足任一即触发）:
- **阈值触发**: 未蒸馏 L1 记录 > 100 条（可配置）
- **定时触发**: 每天 2:00 AM（可配置）

**处理逻辑**:
1. 查询 `is_distilled=False` 的 L1 记录
2. 调用 LLM 提取知识三元组
3. 冲突检测 + 版本管理
4. 写入 L2，更新 L1.is_distilled=True

### 6.3 L1 → L3 自动归档

**触发条件**:
- **定时触发**: 每天 3:00 AM（可配置）

**处理逻辑**:
1. 查找超过 30 天（可配置）的 L1 记录
2. 导出为 JSONL 文件
3. 创建 L3 索引记录
4. 删除原 L1 记录

### 6.4 调度器配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `SCHEDULER_ENABLED` | true | 启用调度器 |
| `DISTILL_CRON_HOUR` | 2 | 蒸馏定时小时 |
| `DISTILL_CRON_MINUTE` | 0 | 蒸馏定时分钟 |
| `DISTILL_THRESHOLD` | 100 | 蒸馏阈值 |
| `DISTILL_BATCH_SIZE` | 50 | 蒸馏批量大小 |
| `ARCHIVE_CRON_HOUR` | 3 | 归档定时小时 |
| `ARCHIVE_CRON_MINUTE` | 0 | 归档定时分钟 |
| `ARCHIVE_DAYS` | 30 | 归档天数 |

---

## 7. 数据库 Schema

### 7.1 L1: 情景记录表

```python
class MemsL1Episodic(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)           # Agent 隔离
    session_id: str = Field(index=True)         # 会话 ID
    content: str                                 # 原始对话
    vector_id: str = Field(unique=True)         # 向量 ID
    importance_score: float = 0.0               # 重要性评分
    is_distilled: bool = Field(default=False)   # 是否已蒸馏
    metadata_json: Dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

### 7.2 L2: 语义知识表

```python
class MemsL2Semantic(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    subject: str = Field(index=True)            # 主体
    predicate: str                               # 关系
    object: str                                  # 客体
    confidence: float = 1.0                      # 置信度
    source_ids: List[int]                        # 溯源 L1
    version: int = 1                             # 版本号
    is_active: bool = Field(default=True)       # 是否活跃
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

### 7.3 L3: 归档索引表

```python
class MemsL3Archive(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    time_period: str                             # 时间段
    summary_text: str                            # 摘要
    file_path: str                               # 文件路径
    record_count: int = 0                        # 记录数
    archived_at: datetime = Field(default_factory=datetime.utcnow)
```

---

## 8. API 接口

### 8.1 L0 工作记忆（推荐入口）

**POST** `/l0/write` - 写入 L0 并自动同步到 L1
```json
{
  "agent_id": "agent_001",
  "session_id": "sess_abc",
  "messages": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！"}
  ],
  "active_plan": "学习",
  "temp_variables": {"key": "value"},
  "ttl_seconds": 1800
}
```

**GET** `/l0/read/{agent_id}/{session_id}` - 读取 L0

**DELETE** `/l0/{agent_id}/{session_id}` - 删除 L0

**POST** `/l0/commit/{agent_id}/{session_id}` - 手动提交 L0 到 L1

### 8.2 记忆摄取（直接写 L1）

**POST** `/ingest`
```json
{
  "agent_id": "agent_001",
  "session_id": "sess_abc",
  "content": "用户说：我喜欢学习 Python",
  "metadata": {"source": "chat"},
  "importance_score": 0.8
}
```

### 8.3 混合检索

**POST** `/search`
```json
{
  "agent_id": "agent_001",
  "query": "编程语言学习",
  "top_k": 5,
  "include_l2": true
}
```

### 8.4 记忆蒸馏（手动触发）

**POST** `/distill`
```json
{
  "agent_id": "agent_001",
  "batch_size": 10,
  "force": false
}
```

### 8.5 归档（手动触发）

**POST** `/archive`
```json
{
  "agent_id": "agent_001",
  "days": 30
}
```

---

## 9. 配置说明

### 环境变量 (.env)

```bash
# ===================
# 数据库
# ===================
DATABASE_URL=sqlite:///mems.db

# ===================
# Redis
# ===================
REDIS_HOST=localhost
REDIS_PORT=6379

# ===================
# Qdrant
# ===================
QDRANT_HOST=localhost
QDRANT_PORT=6333

# ===================
# Embedding Model (本地 sentence-transformers)
# ===================
EMBEDDING_PROVIDER=sentence-transformers
SENTENCE_TRANSFORMERS_MODEL=BAAI/bge-small-zh-v1.5

# ===================
# LLM 蒸馏 (OpenAI/DashScope)
# ===================
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen3.5-plus

# ===================
# 调度器 (自动化流水线)
# ===================
SCHEDULER_ENABLED=true
DISTILL_CRON_HOUR=2
DISTILL_CRON_MINUTE=0
DISTILL_THRESHOLD=100
DISTILL_BATCH_SIZE=50
ARCHIVE_CRON_HOUR=3
ARCHIVE_CRON_MINUTE=0
ARCHIVE_DAYS=30

# ===================
# L0 自动同步
# ===================
L0_AUTO_SYNC_L1=true
L0_DEFAULT_TTL_SECONDS=1800
```

---

## 10. 快速开始

### 10.1 启动服务

```bash
# 1. 安装依赖
uv sync

# 2. 启动 Docker 服务 (Redis + Qdrant)
docker compose up -d

# 3. 初始化数据库
python scripts/init_db.py

# 4. 启动 FastAPI
uv run python -m mems.main
```

### 10.2 推荐使用流程

```bash
# 1. 写入 L0（推荐，自动同步到 L1）
curl -X POST http://localhost:8000/l0/write \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "my_agent",
    "session_id": "sess_001",
    "messages": [{"role": "user", "content": "我喜欢 Python"}]
  }'

# 2. 搜索（自动查询 L1 + L2）
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "my_agent", "query": "Python"}'

# 3. 手动触发蒸馏（可选，调度器会自动触发）
curl -X POST http://localhost:8000/distill \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "my_agent", "batch_size": 50}'
```

---

## 11. 扩展指南

### 11.1 切换 Embedding 模型

```bash
EMBEDDING_PROVIDER=openai
OPENAI_EMBEDDING_API_KEY=sk-xxx
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

### 11.2 切换 LLM 蒸馏

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4o-mini
```

### 11.3 切换数据库

```bash
DATABASE_URL=postgresql://user:pass@localhost/mems
```

---

## 12. 目录结构

```
mems/
├── pyproject.toml              # 项目配置
├── docker-compose.yml          # Docker 服务
├── .env                        # 环境变量
├── README.md                   # 项目说明
├── src/mems/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口 + 调度器
│   ├── config.py               # 配置管理
│   ├── models.py               # SQLModel 定义
│   ├── database.py             # 数据库连接
│   ├── schemas.py              # Pydantic 模型
│   ├── services/
│   │   ├── scheduler.py        # 调度服务 (APScheduler)
│   │   ├── redis_service.py    # L0 服务
│   │   ├── l0_sync.py          # L0→L1 同步
│   │   ├── vector_service.py   # Qdrant 服务
│   │   ├── embedding.py        # 向量化服务
│   │   ├── distill.py          # 蒸馏服务 + 阈值检测
│   │   ├── archive.py          # 归档服务 + 自动触发
│   │   └── jsonl_utils.py      # JSONL 工具
│   └── routers/
│       ├── l0.py               # /l0 (推荐入口)
│       ├── ingest.py           # /ingest
│       ├── search.py           # /search
│       ├── distill.py          # /distill
│       └── archive.py          # /archive
├── scripts/
│   ├── init_db.py              # 数据库初始化
│   └── reader.py               # L3 读取器 (纯标准库)
└── storage/
    ├── l1_raw/                 # L1 JSONL
    ├── l2_knowledge/           # L2 JSONL
    └── l3_archive/             # L3 归档
```

---

## 13. 注意事项

1. **自动化优先**: 推荐使用 `/l0/write`，系统自动完成 L0→L1 同步
2. **文本优先**: 所有数据必须同时写入 JSONL，防止数据库损坏导致数据丢失
3. **Agent 隔离**: 每个 Agent 使用独立 Collection，实现物理隔离
4. **版本管理**: L2 知识冲突时，旧版本标记为非活跃，新版本+1
5. **LLM 配置**: 蒸馏需要 OpenAI/DashScope API，否则只标记不提取

---

## 14. 常见问题

**Q: L0 自动双写需要配置什么？**
A: 确保 `L0_AUTO_SYNC_L1=true`，无需其他配置

**Q: 蒸馏没有自动触发？**
A: 检查：1) 阈值是否达到（默认100条）；2) 调度器是否启用；3) LLM 是否可用

**Q: 归档是按什么触发的？**
A: 按时间触发（默认每天3:00），自动归档超过30天的数据

**Q: 可以手动触发吗？**
A: 可以，使用 `/distill` 和 `/archive` 接口

---

*文档版本: 0.2.0*
*更新时间: 2026-03-21*
