# Integration Overview

The recommended Mems integration flow for third-party agents is:

1. `GET /v1/mems/context`
2. `POST /v1/mems/query`
3. let the agent build the answer
4. `POST /v1/mems/write`

This split keeps responsibilities clear:

- `context` reads the current live session page and paginated history
- `query` handles long-term recall
- `write` stores the new turn back into the system

## Public Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/v1/mems/write` | `POST` | append turns and persist to L0/L1 |
| `/v1/mems/query` | `POST` | search active L1 and L2 |
| `/v1/mems/context` | `GET` | fetch live context and older pages |
| `/v1/mems/status` | `GET` | inspect dependency and pipeline health |
| `/v1/mems/health` | `GET` | lightweight liveness probe |
