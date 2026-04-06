# 快速开始

## 环境要求

- Python `3.12+`
- Node.js `18+`
- Docker

## 1. 安装 Python 依赖

```bash
uv sync
```

## 2. 配置环境变量

```bash
cp .env.example .env
```

默认开发配置包含：

- SQLite 数据库
- 本地 Redis
- 本地 Qdrant
- `sentence-transformers` embedding

如果你要启用 L2 蒸馏，需要在 `.env` 中配置可用的 `OPENAI_*` 参数。

## 3. 启动依赖服务

```bash
docker compose up -d
python scripts/init_db.py
uv run python -m mems.main
```

## 4. 验证服务

```bash
curl http://localhost:8210/v1/mems/health
```

返回 HTTP 200 即表示 API 已正常启动。

## 5. 跑通第一个 Demo

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

然后查询：

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
