# Architecture Overview

![Mems Architecture](/architecture.svg)

*L0 hot cache → L1 episodic ledger → L2 distilled knowledge → L3 cold archive,
plus a default-off L0 output stream for consumers that bring their own code.*

## Four-Layer Design

| Layer | Responsibility | Storage | Key Entities |
| --- | --- | --- | --- |
| `L0` | hot recent-memory cache, fast recall window; never manages runtime state | Redis | `mems:l0:*` snapshots (TTL), `short_term_buffer`, optional stream `mems:l0:events` |
| `L1` | episodic memory ledger: online source of truth, replay and audit | SQL + Qdrant | `mems_l1_episodic`; Qdrant collection `agent_{agent_id}` (derived vector replica) |
| `L2` | distilled long-term knowledge with lineage and versioning | SQL (+ vector index for summaries) | `mems_l2_profile_item`, `mems_l2_fact`, `mems_l2_event`, `mems_l2_summary`, `mems_l2_conflict_log` |
| `L3` | century-scale cold archive, format-portable | JSONL files | `storage/l3_archive/*.jsonl`, `mems_l3_archive` batch metadata |

## Data Flow

- **Remember** (`POST /v1/mems/write`): write the L0 snapshot (TTL) → `sync_l0_to_l1`
  commits `mems_l1_episodic` and best-effort upserts the Qdrant vector replica.
  When `L0_PIPELINE_ENABLED=true`, a `write`/`append` event is also published to
  the Redis Stream.
- **Recall** (`POST /v1/mems/query`): read active L0 snapshots across sessions,
  gather L1/L2 SQL candidates, combine with Qdrant vector search, then rank with
  a normalized base (0-1) plus intent weighting and freshness; lexical overlap
  uses CJK bigrams and ASCII words. Archived L1 is excluded by default.
- **Distill** (`L1 → L2`): threshold-triggered. After each persisted write, a
  background task checks pending records; when un-distilled reaches
  `DISTILL_THRESHOLD` (default 100), LLM extraction and reconciliation create or
  update profiles, facts, events, summaries and conflict logs, then sync summary
  vectors. Records with `importance_score >= DISTILL_HIGH_IMPORTANCE_THRESHOLD`
  (default 0.8) are distilled immediately (batch=1) instead of waiting for the
  threshold. Batches are ordered by importance descending, then creation time.
  No scheduled distill job.
- **Archive** (`L1 → L3`): APScheduler daily at 03:00 moves records older than
  `ARCHIVE_DAYS` into an atomic JSONL batch, records metadata, and marks L1 archived.
- **L0 Output Pipeline** (default off): consumers use `XREAD` / `XREADGROUP` with
  their own code. Publication is best-effort and never blocks `/write`.
  See [L0 Output Pipeline](../integration/l0-pipeline).

## Components

- **API layer**: FastAPI router under `/v1/mems/*`; dependencies injected via
  `Session`, `RedisService`, `VectorService`, `EmbeddingService`.
- **Services**: `redis_service` (L0), `l0_sync` (L0→L1), `vector_service`
  (Qdrant async SDK), `embedding` (sentence-transformers or OpenAI), `llm_client`
  (OpenAI-compatible), `distill`, `archive`, `scheduler` (APScheduler singleton),
  `jsonl_utils`.
- **Isolation**: hard boundaries `tenant_id` / `user_id` / `agent_id`; soft tag
  `scope`; `session_id` is optional provenance. Do not rely on `agent_id` alone
  for multi-user isolation.
- **Failure tolerance**: vector replica sync and stream publication are
  best-effort; distillation and archive run in the background with status columns
  (`vector_status`, `archive_status`, `is_distilled`, `is_archived`).

## Deployment Notes

- Ports: Mems API `8210`, Qdrant `6333`, Redis `6379`.
- Tests run fully faked (`FakeRedisService`, `FakeVectorService`,
  `FakeEmbeddingService`) — no external services required.
- Distillation needs an OpenAI-compatible LLM configuration; without credentials
  it is skipped.
