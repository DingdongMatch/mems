# Mems - Layered Memory System

> 🤖 Multi-Agent Isolation | 🔄 Fully Automated Pipeline | 📚 Long-Term Memory | 🧠 LLM-Powered Distillation

A multi-agent isolated, long-term (100-year) fully automated layered memory system.

[English](README.md) | [中文](README_zh.md)

## Core Features

- **Fully Automated Pipeline**: L0→L1→L2→L3 completely automated
- **Four-Layer Memory Architecture**: L0(Redis) → L1(SQL+Vector) → L2(SQL+JSONL) → L3(JSONL Archive)
- **Multi-tenant Isolation**: Each Agent has independent storage with physical isolation
- **Vector Search**: Semantic search based on Qdrant
- **Memory Distillation**: L1→L2 automatic knowledge triplet extraction
- **Centennial Archive**: Pure text JSONL format, readable across eras

## Tech Stack

- Python 3.12+ / FastAPI / SQLModel
- Qdrant (Vector Database) / Redis (L0 Working Memory)
- APScheduler (Automation) / SQLite

## Layer Definition

The current repository already implements a runnable four-layer memory pipeline. For product design and future evolution, the layers can be defined as follows:

| Layer | Logical Definition |
|-------|--------------------|
| L0: Instant Layer | Ongoing conversation, current task state, chain-of-thought in progress |
| L1: Episodic Layer | Raw conversation records and recent event details |
| L2: Semantic Layer | Distilled user profile, factual knowledge, behavioral preferences, and rolling summaries |
| L3: Archive Layer | Full historical logs, annual summaries, and inactive knowledge |

## Quick Start

### 1. Start Services

```bash
# Install dependencies
uv sync

# Start Docker services (Redis + Qdrant)
docker compose up -d

# Initialize database
python scripts/init_db.py

# Start FastAPI
uv run python -m mems.main
```

### 2. Verify

```bash
# Health check
curl http://localhost:8000/health

# Write memory
curl -X POST http://localhost:8000/memories/write \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "test", "session_id": "s1", "messages": [{"role": "user", "content": "Test content"}]}'

# Search
curl -X POST http://localhost:8000/memories/search \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "test", "query": "test"}'

# Monitor
curl http://localhost:8000/monitor/status

# Reset development state and rebuild database
uv run python scripts/reset_dev_state.py
```

## Automation Pipeline

```
User writes memory → Auto L0 → Auto L1 → Auto L2 → Auto L3
     │
     └─→ /memories/write → L0 (Redis)
                             │
                             │ (auto-persist)
                             ▼
                           L1 (SQL + Qdrant + JSONL)
                        │
        ┌───────────────┴───────────────┐
        │                               │
       │ (threshold >100 or daily 2:00)│ (daily 3:00)
       ▼                               ▼
     L2 (Knowledge Triplets)        L3 (Archive)
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/memories/write` | POST | Write memory and let the system auto-persist to L0/L1 |
| `/memories/search` | POST | Query memory with internal episodic/profile/fact/summary retrieval |
| `/monitor/status` | GET | View service health, scheduler state, and pipeline backlog |
| `/health` | GET | Lightweight process health probe |

Archive now behaves as cold storage: old memories are exported to L3 and marked as archived, but remain searchable online.

The distillation pipeline now follows `Filter -> Extract -> Reconcile -> Commit`, and stores L2 memory as profile items, facts, events, summaries, and conflict logs.

Search now performs intent-aware ranking internally, so preference queries prefer profile memory, timeline queries prefer episodic/event memory, and summary queries prefer rolling summaries.

Rolling summaries are now vector-indexed as part of L2, and monitor status exposes stale profile/fact/summary counts based on `last_verified_at`.

## Current Progress

- Public API has been reduced to `write / search / monitor`
- Distillation now runs as `Filter -> Extract -> Reconcile -> Commit`
- L2 has been split into profile, fact, event, summary, and conflict memory types
- Archive now uses cold storage semantics and keeps online recall available
- Search now uses intent-aware routing, freshness-aware ranking, and summary vector recall
- Automated regression tests cover write, search, archive, distill, and reconciliation flows

## Roadmap

- Add automatic re-verification jobs for stale profile/fact/summary memory
- Add vector indexing for profile and fact memory, not only summaries
- Improve reconciliation with richer contradiction taxonomy and confidence merging
- Add richer rolling summaries by time window, such as weekly and monthly summaries
- Add benchmark and evaluation tooling for retrieval quality and distillation quality

## Documentation

- [Technical Documentation](docs/TECHNICAL_en.md) - Complete architecture and API reference
- [Vibe Coding Guide](docs/VIBE_CODING_en.md) - How to use AI-assisted development
- [中文文档](docs/TECHNICAL.md) - 中文技术文档
- [Vibe Coding 指南](docs/VIBE_CODING.md) - 中文开发指南
- [中文版 README](README_zh.md)

## Swagger API Docs

After starting the service:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

## Project Structure

```
mems/
├── src/mems/
│   ├── main.py              # FastAPI entry + scheduler
│   ├── config.py            # Configuration
│   ├── models.py            # SQLModel definitions
│   ├── schemas.py           # Pydantic models
│   ├── services/
│   │   ├── scheduler.py     # APScheduler
│   │   ├── redis_service.py # L0 service
│   │   ├── l0_sync.py       # L0→L1 sync
│   │   ├── vector_service.py
│   │   ├── embedding.py
│   │   ├── distill.py       # Distillation + threshold detection
│   │   └── archive.py       # Archive + auto-trigger
│   └── routers/
│       ├── memories.py      # /memories/write + /memories/search
│       └── monitor.py       # /monitor/status
├── scripts/
│   ├── init_db.py
│   └── reader.py            # L3 reader (pure stdlib)
└── storage/                 # JSONL data storage
```

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check src/
```

## Port Configuration

| Service | Port | Description |
|---------|------|-------------|
| Mems API | 8000 | FastAPI main service |
| MCP Server | 8210 | MCP protocol service (reserved) |
| Qdrant | 6333 | Vector database (external) |
| Redis | 6379 | L0 working memory (external) |

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) file for details.
