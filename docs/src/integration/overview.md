# Integration Overview

Mems is memory infrastructure for AI agents: through L0–L3 layers it provides
memory storage, recall, distillation, and archiving. The caller owns session
management, prompt assembly, task state, and final decisions; Mems only handles
"content worth remembering" and "how to recall it on demand".

This document answers three questions:

1. what you need to prepare before integrating Mems;
2. the recommended call order;
3. what happens internally after a write/recall, and how to interpret the
   result fields.

Endpoint-level field references: [Query](/api/query), [Write](/api/write), and
[Redistill](/api/redistill); identity-field semantics: [Identity Model](/integration/identity-model);
recall semantics: [Memory Recall Protocol (MRP)](/introduction/memory-recall-protocol).

## 1. Dependencies and Startup

### 1.1 Required External Services

| Service | Purpose | Default address |
| --- | --- | --- |
| PostgreSQL | SQL source of truth for L1/L2 records, outbox, and archive metadata | `localhost:5432` |
| Redis | L0 hot memory cache (optional L0 output pipeline) | `localhost:6379` |
| Qdrant | vector replicas for L1 episodic and L2 summaries | `localhost:6333` |
| OpenAI-compatible LLM | L1 → L2 distillation and L3 → L2 re-distillation (works once configured) | per `.env` |
| Embedding | vectorization for queries and writes (sentence-transformers or OpenAI) | per `.env` |

For local development, start the dependencies directly:

```bash
docker compose up -d
```

### 1.2 Start Mems

```bash
cp .env.example .env
# edit .env as needed (LLM / Embedding, etc.)
uv sync
python scripts/init_db.py
uv run python -m mems.main
```

It listens on `http://localhost:8210` by default (`APP_PORT` can be changed).
After startup, verify on two levels:

```bash
# lightweight liveness check (does not depend on any external component)
curl http://localhost:8210/v1/mems/health

# full dependency health + pipeline status
curl http://localhost:8210/v1/mems/status
```

FastAPI auto-generates OpenAPI docs; open in a browser:

```text
http://localhost:8210/docs
```

### 1.3 Run Modes

| Config | Default | Meaning |
| --- | --- | --- |
| `OUTBOX_WORKER_ENABLED` | `true` | run the outbox worker inside the API process (vector sync, distillation, archive consumption) |
| `SCHEDULER_ENABLED` | `true` | enable the scheduler (triggers archiving at 03:00 daily) |
| `L0_PIPELINE_ENABLED` | `false` | whether every L0 write is published to a Redis Stream (see [L0 Output Pipeline](/integration/l0-pipeline)) |

For high-throughput deployments, split the outbox worker into a standalone
process:

```bash
uv run python -m mems.worker
```

## 2. Public Endpoints

All request and response bodies are JSON. There is no built-in authentication
today; enforce authentication and tenant validation at the gateway /
reverse-proxy layer.

| Endpoint | Method | Purpose | Details |
| --- | --- | --- | --- |
| `/v1/mems/write` | `POST` | remember a memory (L0 cache + L1 master record) | [Write](/api/write) |
| `/v1/mems/query` | `POST` | recall relevant memories across L0/L1/L2 | [Query](/api/query) |
| `/v1/mems/redistill` | `POST` | manually re-distill archived L3 into L2 | [Redistill](/api/redistill) |
| `/v1/mems/status` | `GET` | dependency health and pipeline metrics | [Status](/api/status) |
| `/v1/mems/health` | `GET` | lightweight liveness probe | [Health](/api/health) |

The core workflow has only two actions: `Remember` (write) and `Recall` (query).

```text
Agent Runtime
   |
   |-- 1. POST /v1/mems/query (when memories are needed)
   |         |
   |         v
   |    assemble Context -> LLM -> generate answer
   |
   `-- 2. POST /v1/mems/write (when something worth remembering appears)
             |
             v
        Mems: L0 cache + L1 persistence + async distillation/vector sync
```

Recall first or remember first — Mems does not prescribe an order.

## 3. Identity Design Before Integration

Requests carry identity as **flat string fields**:

```text
tenant_id / user_id / agent_id: identity boundaries (agent_id required)
session_id: write-side action provenance (nullable, not a long-term boundary)
```

The API accepts an optional generic `namespace` path (`string[]`) for
business context, such as `["projects", "prj_abc123"]`. Mems combines it with
validated identity to derive an immutable, versioned internal
`MemoryNamespace`. The
caller application authorizes both identity and business context; Mems applies
them consistently to storage queries. The path selects a partition and grants
no permissions.

Recommended usage:

| Scenario | Recommended identity |
| --- | --- |
| Single-user agent | use one fixed `user_id`; leave `tenant_id` empty when no tenant boundary is needed |
| Multi-user SaaS | pass `tenant_id` + `user_id` explicitly on every request; do not rely on `agent_id` alone |
| Multi-agent system | use a different `agent_id` per agent; still pass the user dimension as needed |
| Shared / team spaces | authorize the shared owner in the caller application and use its identity |

The canonical namespace includes optional identity values. Omitting one selects
its dedicated null partition; it does not wildcard across values. In
multi-user scenarios, derive `tenant_id` and `user_id` from trusted caller
context and use the same identity for `write` and `query`.

`session_id` only answers "which session did this memory come from". Query
requests do not accept `session_id`; L0/L1 result items may carry it, and L2
knowledge traces back through `source_l1_ids`.

## 4. Minimal Integration Example

### 4.1 Write a Memory

```bash
curl -X POST http://localhost:8210/v1/mems/write \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "session_id": "session-001",
    "content": "The user likes Rust and prefers concise answers.",
    "messages": [
      {"role": "user", "content": "Remember that I like Rust."},
      {"role": "assistant", "content": "Got it."}
    ],
    "ttl_seconds": 1800,
    "importance_score": 0.8
  }'
```

Example success response:

```json
{
  "success": true,
  "tenant_id": "acme",
  "user_id": "user-42",
  "agent_id": "demo-agent",
  "session_id": "session-001",
  "importance_score": 0.8,
  "content": "The user likes Rust and prefers concise answers.",
  "messages": [
    {"role": "user", "content": "Remember that I like Rust."},
    {"role": "assistant", "content": "Got it."}
  ],
  "appended_count": 2,
  "persisted_to_l1": true,
  "l1_id": 101,
  "message": "Memory remembered and persisted to L1"
}
```

At least one of `content` / `messages` must be provided. Recommended practice:

- `content` only: record a fact/conclusion worth remembering directly;
- `messages` only: Mems folds the messages into memory text as `role: content`,
  one per line;
- both: `content` becomes the memory text used for retrieval and distillation,
  while `messages` stays as replayable, auditable structured source.

### 4.2 Recall Related Memories

```bash
curl -X POST http://localhost:8210/v1/mems/query \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "query": "What language does the user like?",
    "top_k": 5
  }'
```

Example success response:

```json
{
  "query": "What language does the user like?",
  "results": [
    {
      "source": "l1_episodic",
      "content": "The user likes Rust and prefers concise answers.",
      "score": 1.14,
      "source_l1_ids": [101],
      "session_id": "session-001",
      "created_at": "2026-09-06T01:00:00Z",
      "tenant_id": "acme",
      "user_id": "user-42"
    },
    {
      "source": "l0_hot",
      "content": "The user likes Rust and prefers concise answers.",
      "score": 0.7,
      "source_l1_ids": [],
      "session_id": "session-001",
      "tenant_id": "acme",
      "user_id": "user-42"
    }
  ],
  "total": 2
}
```

After getting the results, the caller:

1. treats `content` as "readable memory text";
2. expands originals through `source_l1_ids` when evidence is needed;
3. decides which results enter the context and how to present/rank them;
4. does not treat `score` as a correctness probability, and does not use Mems as
   a context manager.

### 4.3 Python Example

```python
import httpx

BASE = "http://localhost:8210"
IDENTITY = {
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent"
}

def remember(content: str, importance: float = 0.5) -> dict:
    resp = httpx.post(
        f"{BASE}/v1/mems/write",
        json={**IDENTITY, "content": content, "importance_score": importance},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()

def recall(query: str, top_k: int = 5) -> list[dict]:
    resp = httpx.post(
        f"{BASE}/v1/mems/query",
        json={**IDENTITY, "query": query, "top_k": top_k},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["results"]
```

(The example uses `httpx`; install the HTTP client of your choice for your
runtime.)

## 5. What Happens After a Write

A single `POST /v1/mems/write` is not just "storing one record": it enters an
eventually-consistent pipeline:

```text
Write Request
      |
      |-- L0: save/merge a Redis hot snapshot per (identity + session), within TTL
      |
      |-- L1: insert one Episodic master record in SQL (same transaction)
      |         |-- enqueue vector_sync (async Qdrant vector replica)
      |         `-- enqueue distillation-trigger event
      |
      `-- Outbox Worker consumes asynchronously:
              |-- vector_sync -> Qdrant
              `-- distillation (see below)
```

Key points:

- **L0 is a hot cache**: later writes to the same session merge the short-term
  buffer and keep the remaining TTL; a query reads at most the recent active
  snapshots per agent (default 10, see `L0_RECALL_MAX_SNAPSHOTS`).
- **L1 is the SQL source of truth**: Qdrant is only a derived vector replica;
  replica failures do not affect the L1 master record and are retried by the
  outbox.
- **Distillation is not synchronous**: ordinary writes only enqueue an event;
  per-agent distillation expands once pending records reach
  `DISTILL_THRESHOLD` (default 100).
- **High-value immediate distillation**: records with
  `importance_score >= 0.8` (`DISTILL_HIGH_IMPORTANCE_THRESHOLD`) trigger a
  batch=1 targeted distillation right after the write, without waiting for the
  global threshold.
- **When the LLM is not configured**: distillation is silently skipped (the
  current implementation emits no log and no API-visible message); the write
  itself is unaffected.
- **Archiving runs automatically at 03:00 daily**: active L1 records older than
  `ARCHIVE_DAYS` (default 30 days) are written to JSONL (L3) and marked
  archived; archived L1 no longer participates in online recall.

If you only need "recent conversation notes + keyword recall", Mems works
without an LLM; L2 distillation and long-term profiles/facts/summaries require
working OpenAI-compatible `OPENAI_*` configuration.

## 6. Layered Semantics of Recall Results

`source` tells the caller which layer a memory came from, so each should be
interpreted differently:

| `source` | Meaning | When to trust | Time field |
| --- | --- | --- | --- |
| `l0_hot` | recent hot snapshot (this session / recent TTL window) | freshest, but covers only the active period | no `created_at` |
| `l1_episodic` | original episodic record | verbatim; good for replay/audit | `created_at` |
| `l2_profile` | stable user-profile assertion | distilled, carries confidence | `last_verified_at` |
| `l2_fact` | stable entity/relation fact | distilled, carries confidence | `last_verified_at` |
| `l2_event` | extracted important event | ranked by importance | `created_at` |
| `l2_summary` | rolling long-term summary | comprehensive; watch for staleness | `created_at` |

Evidence and trustworthiness are two different things:

```text
source_l1_ids -> which L1 originals back this L2 knowledge (evidence)
confidence    -> how trustworthy this content itself is (currently folded into score, not returned separately)
```

When presenting "sources" to end users, trace L1 originals with
`source_l1_ids`; do not treat L2 as unfalsifiable conclusions.

## 7. When to Use redistill

`POST /v1/mems/redistill` is unrelated to online recall; it solves "how does
historical knowledge follow a model upgrade":

```text
L3 JSONL (original archive, model-agnostic)
      |  manual trigger
      v
extraction + deterministic reconciliation
      |
      v
upgrade/create L2 (current OPENAI_MODEL)
      |
      |-- deduplicate by (content fingerprint, model)
      `-- conflicts go to the conflict log; the version chain moves forward
```

Recommended scenarios:

- after upgrading `OPENAI_MODEL`, re-distill historical archives once;
- when you want old conversations to yield profiles/facts/summaries, run a
  manual backfill;
- when you need to filter by agent, time range, or minimum importance.

See the [Redistill API](/api/redistill) for details.

## 8. Observability and Troubleshooting

### 8.1 Health Checks

```text
GET /v1/mems/health -> process liveness (static response)
GET /v1/mems/status -> database/Redis/Qdrant/scheduler health + pipeline metrics
```

Container and load-balancer recommendations:

- use `/v1/mems/health` for the liveness probe;
- use `/v1/mems/status` for readiness checks or manual troubleshooting.

### 8.2 Common Troubleshooting Signals

| Symptom | What to check |
| --- | --- |
| write succeeds but query returns nothing new | outbox worker is not consuming vector sync; check `OUTBOX_WORKER_ENABLED` and `pending_distill` / status |
| L2 never appears | LLM not configured or `OPENAI_API_KEY` invalid; pending records below the distill threshold |
| users see each other's memories | writes were stored under the same identity or the caller supplied the wrong identity/namespace |
| freshly written content is not found | L0 snapshot TTL expired or no lexical overlap; L1 vector replica lagging |
| redistill result is unexpected | already processed with the current model (deduplicated skip); inspect `mems_redistill_progress` |

### 8.3 Logs

The API, application, and worker emit JSON logs. `LOG_LEVEL` controls the level.
API requests get a generated `request_id`, also returned in `X-Request-ID`;
background job logs include the Outbox event ID, type, result, and duration.
Logs omit raw memories, prompts, API keys, and exception message text.

LLM inputs, memory text, API keys, and exception message text are not included in
application logs. The distillation path uses the OpenAI SDK directly; no external
LLM tracing service is configured.

## 9. Pre-Launch Checklist

Confirm each item before going live:

- [ ] `.env` points at the right database/Redis/Qdrant, and
      `uv run python -m mems.main` starts normally;
- [ ] `OPENAI_*` is configured when distillation is needed (`/status` does not
      check the LLM; verify with a real write and observe whether L2 appears);
- [ ] `write` and `query` use the same `tenant_id/user_id/agent_id` identity;
- [ ] multi-user scenarios pass `user_id` explicitly on every request and do not
      rely on `agent_id` isolation;
- [ ] caller authorization is complete before requests reach Mems; planned
      namespace segments come from trusted business context and are consistent
      between write and query;
- [ ] you understand the `content`/`messages` difference and chose a fitting
      write style;
- [ ] query-result consumers handle nullable `source_l1_ids`, `created_at`, and
      `session_id`;
- [ ] the liveness probe points at `/health`, business monitoring at `/status`;
- [ ] in production, at least one outbox worker is running (in-process or
      standalone).

## 10. Next Steps

- per-endpoint parameters: read [Query](/api/query) and [Write](/api/write);
- consume memory events: read [L0 Output Pipeline](/integration/l0-pipeline);
- protocol semantics: read [Memory Recall Protocol](/introduction/memory-recall-protocol).
