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
  |-- POST /v1/mems/write --> L0 Redis (hot memory)
  |                           |-- L0 output pipeline (off by default) --> Redis Stream for your own code
  |                           `-- L1 SQL + Qdrant (episodic ledger)
  |                                |-- L2 distilled knowledge (profiles/facts/events/summaries)
  |                                `-- L3 JSONL archive
  |-- POST /v1/mems/query --> L0 + L1 + L2 hybrid recall
  `-- GET /v1/mems/status | /v1/mems/health --> health and pipeline metrics
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

## L0 Memory Output Pipeline

Mems can publish every L0 memory write as an event to a Redis Stream, so you can
consume memories with your own code (mirroring, dashboards, downstream
workflows). The pipeline is **disabled by default**:

```bash
L0_PIPELINE_ENABLED=true        # default: false
L0_PIPELINE_STREAM=mems:l0:events
L0_PIPELINE_MAXLEN=10000
```

Consume with any Redis client:

```bash
redis-cli XREAD COUNT 10 STREAMS mems:l0:events 0
```

Each event carries `event_type` (`write`/`append`), the identity fields, the
`content` snapshot, and a JSON-encoded `messages` buffer. Publication is
best-effort: a stream failure never blocks `POST /v1/mems/write`. See
[L0 Output Pipeline](docs/src/integration/l0-pipeline.md) for the full format
and consumer-group examples.

## L3 → L2 Re-Distillation: Memory That Evolves With The Model

Mems' highlight: the **archive is not a dead end**. When the underlying LLM gets
stronger, you can re-distill archived memories so long-term knowledge upgrades
to what the current model can extract:

```bash
curl -X POST http://localhost:8210/v1/mems/redistill
# or
uv run python -m mems.redistill --agent demo-agent
```

Archived JSONL keeps the full raw memory, so re-distillation reads from L3,
re-runs extraction + reconciliation with the current `OPENAI_MODEL`, and upgrades
L2 non-destructively through the version chain (`supersedes` + conflict log).
Records are deduplicated by content fingerprint + model, so a model upgrade
automatically queues old data for re-distillation. See
[What Is Mems](docs/src/introduction/what-is-mems.md) for the closed-loop story.

## Performance

Latest full evaluation based on **RAGAS** (`2026-04-05`):

| Metric | Score |
| :--- | :--- |
| **Context Recall** | **1.0000** |
| **Faithfulness** | **0.6875** |
| **Answer Relevance** | **0.6267** |

## License

Licensed under the Apache License, Version 2.0. See `LICENSE` for details.
