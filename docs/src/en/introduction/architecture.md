# Architecture Overview

```text
Third-party Agent
  |-- GET /v1/mems/context --> L0 Redis (live session memory)
  |-- POST /v1/mems/query --> L1 + L2 recall
  `-- POST /v1/mems/write --> L1 SQL + Qdrant (episodic memory)
                                  |-- L2 distilled knowledge
                                  `-- L3 JSONL archive
```

## Layer Roles

| Layer | Responsibility | Storage |
| --- | --- | --- |
| `L0` | live session context, recent message window, temporary state | Redis |
| `L1` | episodic history, replay, online evidence | SQL + Qdrant |
| `L2` | profile items, facts, events, summaries, conflict logs | SQL + partial vector indexing |
| `L3` | long-horizon cold archive | JSONL |

## Background Pipelines

- `L0 -> L1`: immediate persistence on write
- `L1 -> L2`: filter, extract, reconcile, commit
- `L1 -> L3`: scheduled archive

## Current Implementation Notes

- public APIs live under `/v1/mems/*`
- the scheduler starts inside the FastAPI app lifespan
- distillation currently depends on an OpenAI-compatible LLM configuration
- the development setup uses `Qdrant 1.15.3`
