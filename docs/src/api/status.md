# `GET /v1/mems/status`

Inspect dependency health and built-in pipeline status.

## Returned Information

- health for database, Redis, Qdrant, and scheduler
- pending distill and archive counts
- recent failure count
- whether the L0 memory output pipeline is enabled
- profile, fact, and summary counts
- conflict count
- stale memory counts
