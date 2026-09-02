# Integration Overview

Mems is memory infrastructure — it stores and recalls memories, not runtime state.
The caller owns session management, prompt assembly, and task state.

The core flow:

1. `POST /v1/mems/query` — recall relevant memories
2. the caller builds the answer with its own context
3. `POST /v1/mems/write` — remember the new memory content

Remember first, or recall first — the order is up to the caller.

## Public Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/v1/mems/write` | `POST` | remember memory content into L0/L1 |
| `/v1/mems/query` | `POST` | recall relevant memories across L0, L1, and L2 |
| `/v1/mems/redistill` | `POST` | re-distill archived L3 records into L2 |
| `/v1/mems/status` | `GET` | inspect dependency and pipeline health |
| `/v1/mems/health` | `GET` | lightweight liveness probe |
