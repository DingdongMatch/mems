# Mems

> A layered, self-distilling, century-scale memory system for AI agents.

Mems is an industrial-grade memory hub solution for AI agents. Through a four-layer hot/cold-decoupled architecture, it provides a memory foundation with personality consistency, low-cost retrieval, and structured evolution.

[中文](README_zh.md)

Docs: https://dingdongmatch.github.io/mems/

## Core Design Philosophy: Why Mems

Unlike a single-layer vector database or basic RAG stack, Mems is designed to control memory entropy in long-running agents:

- **⚡ Instant working memory (L0)**: Redis-based high-speed context cache that ensures millisecond-level response within the current session.
- **🧠 Asynchronous knowledge distillation (L2)**: An automated LLM pipeline that extracts stable semantic facts from raw L1 narratives, enabling evolution from experience to cognition.
- **🔍 Evidence grounding**: L2 distilled outputs stay strongly linked to the original L1 source material, avoiding black-box knowledge and keeping every inference traceable.
- **📜 Century-scale archive (L3)**: A text-first strategy that turns ultra-long-term memory into persistent JSONL files, resisting hardware turnover and format obsolescence.
- **🛡️ Production-grade isolation**: A built-in multi-tenant permission model across Tenant/User/Agent/Session, suitable for SaaS and enterprise agent deployments.
- **🔌 Atomic integration**: An extremely compact API design where agent memory can be integrated in just three steps: `Context -> Query -> Write`.

## Architecture Snapshot

```text
Third-party Agent
  |-- GET /v1/mems/context --> L0 Redis
  |-- POST /v1/mems/query --> L1 + L2 Recall
  `-- POST /v1/mems/write --> L1 SQL + Qdrant
                                  |-- L2 Distilled Memory
                                  `-- L3 JSONL Archive
```

## Quick Start

```bash
uv sync
cp .env.example .env
docker compose up -d
python scripts/init_db.py
uv run python -m mems.main
curl http://localhost:8210/v1/mems/health
```

Docs site local dev:

```bash
cd docs
npm install
npm run dev
```

## Write And Memory Query Example

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

## Performance

Latest full evaluation based on **RAGAS** (`2026-04-05`):

| Metric | Score |
| :--- | :--- |
| **Context Recall** | **1.0000** |
| **Faithfulness** | **0.6875** |
| **Answer Relevance** | **0.6267** |

## License

Licensed under the Apache License, Version 2.0. See `LICENSE` for details.
