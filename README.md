# Mems

> Memory infrastructure for agents that need to remember more than a single prompt window.

`Mems` is a layered memory backend for AI agents.
It helps an agent keep track of the current session, preserve raw history, distill stable long-term knowledge, and archive old records without losing traceability.

Instead of forcing every agent to reinvent memory, Mems provides a clean public API for:

- reading the active conversation context
- searching long-term memory
- writing new user/assistant turns
- observing what was stored, recalled, distilled, and archived

[English](README.md) | [中文](README_zh.md)

## Why Mems

Most agents have the same memory problems:

- they forget what happened a few turns ago unless you keep resending everything
- they store lots of raw chat logs but fail to turn them into stable knowledge
- they mix session context, preferences, facts, and archives into one blurry bucket
- they are hard to integrate from third-party systems because the memory interface is unclear

Mems addresses this by separating memory into layers and automating the pipeline between them.

## What Makes It Different

- **Built for agent integration**: third-party agents use a simple `context -> search -> turns` flow
- **Four memory layers**: each layer has a clear job instead of one overloaded memory table
- **Raw + structured memory together**: you keep original conversation records and also distill profile/fact/event/summary knowledge
- **Multi-agent isolation**: each agent keeps its own memory space
- **Traceable long-term memory**: L2 records keep evidence lineage back to L1
- **Layered recall with cold archive**: archived records leave default online recall, while L3 preserves explainable long-horizon history
- **Reference simulator included**: you can test memory behavior before integrating a real external agent

## The Simple Mental Model

Think of Mems as four shelves:

- **L0**: what the agent is talking about right now
- **L1**: what actually happened in recent conversations
- **L2**: what is worth remembering for the future
- **L3**: what should be preserved for the long haul

## Four-Layer Architecture

| Layer | What it stores | Why it exists |
|-------|----------------|---------------|
| `L0` | current session context, recent turns, temporary state | fast session recall |
| `L1` | raw episodic conversation records | full-fidelity evidence and replay |
| `L2` | profiles, facts, events, summaries, conflict logs | reusable long-term knowledge |
| `L3` | archived historical records in JSONL | cheap, durable, human-readable retention |

## How Data Flows

```text
Third-party Agent
  -> GET /memories/context   -> live context page + lazy-loaded session history
  -> POST /memories/search   -> default long-term recall (active L1 + L2)
  -> build prompt / generate answer
  -> POST /memories/turns    -> persist the new exchange

Background pipeline
  L0 -> L1                   immediate persistence
  L1 -> L2                   filter -> extract -> reconcile -> commit
  L1 -> L3                   scheduled archive
```

## Architecture Diagram

```text
                        +-------------------------+
                        |   Third-party Agent     |
                        |  or Reference Simulator |
                        +-----------+-------------+
                                    |
                +-------------------+-------------------+
                |                                       |
      GET /memories/context                  POST /memories/search
                |                                       |
                v                                       v
         +------+-------+                       +-------+------+
         |   L0 Redis   |                       | L1 + L2 Read |
         | session mem  |                       | retrieval    |
         +------+-------+                       +-------+------+
                |                                       |
                +-------------------+-------------------+
                                    |
                           agent builds prompt
                                    |
                                    v
                        POST /memories/turns or /write
                                    |
                                    v
         +-------------------------------------------------------+
         | L1 Episodic Memory: SQL + Qdrant + JSONL              |
         | raw records, replay, semantic recall, evidence layer  |
         +----------------------+--------------------------------+
                                |
                  +-------------+-------------+
                  |                           |
                  v                           v
         +--------+---------+       +---------+--------+
         | L2 Semantic Mem  |       |   L3 Archive     |
         | profile/fact/    |       |   JSONL history  |
         | event/summary    |       |   long retention |
         +------------------+       +------------------+
```

## Typical Use Cases

- give a chat agent reliable session memory and long-term recall
- preserve user preferences, identity facts, and relationship context over time
- let multiple agents share one memory platform without mixing their data
- test external-agent memory integration before connecting a real product system
- keep old records in a durable plain-text archive instead of an opaque blob

## Public API Design

The public integration path is intentionally simple:

1. `GET /memories/context` to load the live context page and lazily page older history
2. `POST /memories/search` to fetch useful long-term memory
3. let the agent generate an answer using its own prompt logic
4. `POST /memories/turns` to write the new user/assistant exchange back

That is also the main flow used by the built-in reference simulator in normal chat mode.

## Memory Identity Model

Mems now supports both single-user and multi-user agent scenarios through a shared identity model.

- `tenant_id`: optional tenant or organization boundary
- `user_id`: optional but strongly recommended user boundary for multi-user agents
- `agent_id`: the agent identity using the memory system
- `session_id`: the active conversation or thread
- `scope`: business-defined visibility tag such as `private`, `shared`, or `team:sales`

Recommended rule:

- ownership fields (`tenant_id`, `user_id`, `agent_id`, `session_id`) define hard memory boundaries
- `scope` is a soft visibility label defined by the upper business layer

Single-user apps can keep this simple by using one `user_id` and leaving `tenant_id` empty or defaulted.

## Core Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/memories/write` | `POST` | write a working-memory snapshot and auto-persist to L0/L1 |
| `/memories/turns` | `POST` | append session turns like a real third-party agent |
| `/memories/context` | `GET` | fetch the live context page and paginated history for one `session_id` |
| `/memories/search` | `POST` | run default long-term retrieval across active L1 and L2 |
| `/simulator/chat` | `POST` | run the official reference agent |
| `/simulator/chat/stream` | `POST` | stream simulator output for playground-style chat UX |
| `/simulator/playground` | `GET` | visual debugger for chat, prompt, context, and retrieval |
| `/monitor/status` | `GET` | inspect service health and memory pipeline backlog |
| `/health` | `GET` | lightweight liveness probe |

### `/memories/write` vs `/memories/turns`

- Use `/memories/write` when you want to submit a working-memory snapshot
- Use `/memories/turns` when you want to append chat turns incrementally like a third-party agent
- `/memories/turns` also supports `persist_to_l1`, `ttl_seconds`, `active_plan`, and `temp_variables`
- L0 session context is trimmed to a bounded recent window, so it behaves like a rolling chat buffer rather than an unbounded transcript

### `/memories/context` Pagination Rules

- the first request without `before_id` returns the live page for one session
- the live page prefers Redis L0 and may merge in the newest L1 records when Redis does not fully cover the latest persisted history
- pass `before_id` from the previous response's `next_before_id` to fetch older L1 history
- `limit` is the number of L1 records per page, not the final message count
- the response includes `page_type`, `has_more`, and `next_before_id` so the frontend can lazy-load older turns

### Multi-User Query Rules

- if `tenant_id`, `user_id`, or `scope` are provided, Mems uses them as retrieval and persistence filters
- the same `agent_id` can safely serve multiple users without sharing context by default
- simulator and playground now also support these fields so you can debug real multi-user integration behavior

## Why The Storage Mix Looks Like This

Mems is intentionally hybrid instead of all-in on one database.

- **Redis for L0**: cheap and fast live session context for the first page
- **SQL for L1/L2 metadata**: structured queries, lineage, status flags, reconciliation
- **Qdrant for vectors**: semantic recall where exact matching is not enough
- **JSONL for archival durability**: plain text that survives tooling changes and is easy to inspect

## Consistency Model

Mems now treats SQL as the source of truth for online memory state.

- L1 and L2 business records are committed to SQL first
- Qdrant vectors and JSONL files are treated as derived replicas
- replica sync failures do not delete the primary SQL record; they are tracked with replica status fields for later repair
- default `/memories/search` only returns active L1 records and L2 knowledge
- archived L1 records leave the default online search path after they are written to L3

Current replica state tracking includes:

- `vector_status`
- `jsonl_status`
- `archive_status`
- `last_sync_error`
- `last_sync_at`

Operational note:

- the current official async Qdrant SDK path expects a modern Qdrant server; the development setup uses Qdrant `1.15.3`

## Distillation, In Plain English

Mems does not treat every message as long-term memory.

It uses a staged pipeline:

1. **Filter** low-value chatter and ephemeral fragments
2. **Extract** profile updates, facts, events, and a rolling summary
3. **Reconcile** new candidates against existing long-term memory
4. **Commit** stable records to L2 with source lineage

Current L2 memory types:

- profile items
- facts
- events
- summaries
- conflict logs

Important runtime notes:

- distillation currently requires a working LLM provider configuration
- if the LLM is unavailable, distillation may skip records and produce no new L2 output
- scheduled distillation is currently threshold-gated in practice: the scheduled job checks whether the threshold has been reached before processing
- in auto mode, only sufficiently important undistilled L1 records are processed

## Search Behavior

Search is not just vector lookup.

Mems internally combines:

- episodic recall from L1
- semantic recall from Qdrant
- summary recall from L2
- intent-aware ranking so preference questions prefer profile memory, while timeline questions prefer episodic/event memory

Today, vector retrieval is mainly used for active L1 episodic memory and L2 summaries. Profile, fact, and event records are still ranked through structured DB logic rather than full vector retrieval.

## Simulator And Playground

Before wiring a real external agent, you can use the built-in simulator to test the exact public API integration path.

- `POST /simulator/chat` behaves like a reference third-party agent
- `POST /simulator/chat/stream` supports streaming chat output
- simulator also has lightweight built-in modes for system overview and health/status questions
- simulator may fall back to template responses if the LLM call fails, and this is exposed as `debug.fallback_used`
- `GET /simulator/playground` gives you a browser UI with:
  - bilingual switching
  - chat view
  - full prompt trace
  - context window
  - search hits
  - raw simulator response

### Streaming Event Contract

`POST /simulator/chat/stream` returns streamed events in this order:

- `meta`: context source, retrieved memories, basic debug info
- `prompt`: prompt messages when an LLM prompt is built
- `token`: incremental answer chunks
- `done`: final response payload

## Quick Start

### 1. Start the dependencies

```bash
uv sync
docker compose up -d
python scripts/init_db.py
uv run python -m mems.main
```

### 2. Try the public API

```bash
# Health check
curl http://localhost:8000/health

# Append turns like a third-party agent
curl -X POST http://localhost:8000/memories/turns \
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

# Read the live context page
curl "http://localhost:8000/memories/context?tenant_id=acme&user_id=user-42&agent_id=demo-agent&session_id=demo-session&scope=private&limit=10"

# Read an older history page
curl "http://localhost:8000/memories/context?tenant_id=acme&user_id=user-42&agent_id=demo-agent&session_id=demo-session&scope=private&limit=10&before_id=120"

# Search long-term memory
curl -X POST http://localhost:8000/memories/search \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "acme", "user_id": "user-42", "agent_id": "demo-agent", "scope": "private", "query": "What do I like?"}'

# Run the reference simulator
curl -X POST http://localhost:8000/simulator/chat \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "acme", "user_id": "user-42", "agent_id": "demo-agent", "session_id": "demo-session", "scope": "private", "message": "What do I like?"}'
```

### 3. Open the playground

```bash
open http://localhost:8000/simulator/playground
```

## Monitoring And Automation

The system already includes scheduled automation.

- `L1 -> L2` distillation is threshold-gated; the scheduled job currently performs the threshold check on schedule rather than forcing a daily distill run
- `L1 -> L3` archive runs on schedule
- `/monitor/status` shows:
  - dependency health
  - scheduler state
  - pending distill/archive counts
  - stale profile/fact/summary counts

Important current behavior:

- archived L1 memories are excluded from default online search once archive succeeds
- L1 turn persistence keeps structured message metadata, so L1 fallback replay still looks like chat
- L2 summaries are vector-indexed for better retrieval
- per-agent Qdrant collections and payload indexes are created automatically

## Tech Stack

- Python 3.12+
- FastAPI
- SQLModel
- Redis
- Qdrant
- APScheduler
- SQLite (dev) / PostgreSQL-ready design
- uv

## Project Structure

```text
mems/
├── src/mems/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── services/
│   │   ├── redis_service.py
│   │   ├── l0_sync.py
│   │   ├── vector_service.py
│   │   ├── embedding.py
│   │   ├── distill.py
│   │   └── archive.py
│   ├── routers/
│   │   ├── memories.py
│   │   ├── simulator.py
│   │   └── monitor.py
│   └── static/
│       └── simulator_playground.html
├── scripts/
└── storage/
```

## Current Status

- public API flow for external agents is available
- official simulator and browser playground are available
- Qdrant client uses the official async SDK
- automated tests cover write, search, archive, distill, reconciliation, context fallback, and simulator flow

## Roadmap

- Add automatic re-verification jobs for stale profile/fact/summary memory
- Add vector indexing for profile and fact memory, not only summaries
- Improve reconciliation with richer contradiction taxonomy and confidence merging
- Add richer rolling summaries by time window, such as weekly and monthly summaries
- Add benchmark and evaluation tooling for retrieval quality and distillation quality

## Next Planned Improvements

- Add replica repair jobs that retry failed `vector/jsonl/archive` syncs
- Add deep archive search for L3 so long-horizon memory remains recallable without polluting default search
- Add migration support for model evolution and replica state fields
- Improve monitor visibility for replica backlog and failed sync counts

## Development

```bash
uv run pytest
uv run ruff check src tests
```

## More Docs

- `README_zh.md` - Chinese main documentation
- `docs/TECHNICAL_en.md` - advanced technical notes
- `docs/TECHNICAL.md` - advanced technical notes in Chinese
- `docs/VIBE_CODING_en.md`
- `docs/VIBE_CODING.md`

## License

Licensed under the Apache License, Version 2.0. See `LICENSE` for details.
