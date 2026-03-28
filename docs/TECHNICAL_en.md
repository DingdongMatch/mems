# Technical Notes

The main project story, public API flow, layered-memory explanation, and quick-start guide now live in `README.md`.

This file is intentionally reduced to advanced implementation notes that are useful when you want to understand or extend the internals.

## Read This After README If You Need

- embedding model choices and vector dimension assumptions
- scheduler behavior and automation triggers
- SQLModel schema details for L1/L2/L3
- lower-level implementation notes for distillation, archive, and monitoring

## Current Advanced Notes

### Embedding

- Default local model: `BAAI/bge-small-zh-v1.5`
- Optional provider: OpenAI embeddings
- Embeddings are used primarily for:
  - L1 episodic semantic recall
  - L2 summary vector recall

### Distillation Pipeline

Current long-term memory pipeline:

1. Filter low-signal or ephemeral L1 fragments
2. Extract profile/fact/event/summary candidates
3. Reconcile against existing L2 context
4. Commit profile items, facts, events, summaries, and conflict logs

### Storage Roles

- Redis: short-lived session context
- SQL: source of truth for online metadata, lineage, replica states, and reconciliation
- Qdrant: derived vector replica for semantic retrieval
- JSONL: derived durability/archive replica for long-horizon retention

### Replica Consistency

- online writes commit SQL first
- vector and JSONL syncs are tracked as replica state, not as the primary commit boundary
- active L1 participates in default search; archived L1 does not
- L3 remains the long-horizon archive layer and is planned to re-enter recall via deep archive search

### Scheduler Defaults

- Distill threshold: `100`
- Distill cron: `02:00`
- Archive cron: `03:00`
- Archive after: `30` days

### Key Internal Modules

- `src/mems/services/redis_service.py`
- `src/mems/services/l0_sync.py`
- `src/mems/services/vector_service.py`
- `src/mems/services/distill.py`
- `src/mems/services/archive.py`
- `src/mems/routers/memories.py`
- `src/mems/routers/simulator.py`
- `src/mems/routers/monitor.py`

## Suggested Reading Order

1. `README.md`
2. `README_zh.md` if you want the Chinese overview
3. `AGENTS.md` for repo-specific development guidance
4. this file for implementation-level notes
