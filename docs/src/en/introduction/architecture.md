# Architecture Overview

```text
Third-party Agent
  |-- POST /v1/mems/write --> L0 Redis (hot memory cache)
  |                           `-- L1 SQL + Qdrant (episodic memory ledger)
  |                                |-- L2 distilled knowledge
  |                                `-- L3 JSONL archive
  `-- POST /v1/mems/query --> L1 + L2 recall
```

## Layer Roles

| Layer | Responsibility | Storage |
| --- | --- | --- |
| `L0` | hot recent-memory cache, fast recall window | Redis |
| `L1` | episodic memory ledger, replay, online evidence | SQL + Qdrant |
| `L2` | profile items, facts, events, summaries, conflict logs | SQL + partial vector indexing |
| `L3` | long-horizon cold archive | JSONL |

## Background Pipelines

- `L0 -> L1`: immediate persistence on write
- `L1 -> L2`: filter, extract, reconcile, commit
- `L1 -> L3`: scheduled archive of cold records

## Current Implementation Notes

- all public APIs are under `/v1/mems/*`
- the scheduler starts in the FastAPI app lifespan
- distillation requires an OpenAI-compatible LLM configuration
