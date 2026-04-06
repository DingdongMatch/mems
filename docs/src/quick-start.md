# Quick Start

## Requirements

- Python `3.12+`
- Node.js `18+`
- Docker

## 1. Install Python dependencies

```bash
uv sync
```

## 2. Configure environment variables

```bash
cp .env.example .env
```

The default local setup uses:

- SQLite
- local Redis
- local Qdrant
- `sentence-transformers` embeddings

If you want L2 distillation, configure working `OPENAI_*` values in `.env`.

## 3. Start services

```bash
docker compose up -d
python scripts/init_db.py
uv run python -m mems.main
```

## 4. Verify the API

```bash
curl http://localhost:8210/v1/mems/health
```

An HTTP 200 response means the service is up.

## 5. Run the first demo

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
      {"role": "user", "content": "Remember that I like Rust"},
      {"role": "assistant", "content": "Got it."}
    ]
  }'
```

Then query it:

```bash
curl -X POST http://localhost:8210/v1/mems/query \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "scope": "private",
    "query": "What do I like?"
  }'
```
