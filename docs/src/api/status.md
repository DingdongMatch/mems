# `GET /v1/mems/status`

## Endpoint

| Item | Value |
| --- | --- |
| URI | `/v1/mems/status` |
| Method | `GET` |
| Default Base URL | `http://localhost:8210` |

Returns four kinds of information:

1. overall health status;
2. per-component health checks (database / redis / qdrant / scheduler);
3. memory-pipeline metrics (pending distillation, pending archive, L2 counts,
   conflicts, stale knowledge);
4. service version and timestamp.

## Request

None. It is a `GET` request and takes no request body.

```bash
curl http://localhost:8210/v1/mems/status
```

## Response

### HTTP 200

```json
{
  "status": "degraded",
  "version": "0.1.0",
  "timestamp": "2026-09-06T08:00:00Z",
  "checks": {
    "database": {"status": "healthy", "detail": null},
    "redis": {"status": "healthy", "detail": null},
    "qdrant": {"status": "healthy", "detail": null},
    "scheduler": {"status": "degraded", "detail": "scheduler stopped"}
  },
  "pipeline": {
    "pending_distill": 3,
    "pending_archive": 12,
    "recent_failures": 0,
    "l0_pipeline_enabled": false,
    "profile_items": 15,
    "fact_items": 28,
    "summary_items": 4,
    "conflict_count": 2,
    "stale_profile_items": 1,
    "stale_fact_items": 3,
    "stale_summary_items": 0
  }
}
```

### Top-Level Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `status` | string | Overall status: `healthy` / `degraded` / `unhealthy` (computation rules below) |
| `version` | string | Mems service version |
| `timestamp` | string | Status sampling time (UTC ISO-8601) |
| `checks` | object | Component health checks, keyed by `database` / `redis` / `qdrant` / `scheduler` |
| `pipeline` | object | Memory-pipeline metrics |

### checks

Each check item has this shape:

| Field | Type | Meaning |
| --- | --- | --- |
| `status` | string | `healthy` / `degraded` / `unhealthy` |
| `detail` | string \| null | Reason when unhealthy; `null` when healthy |

| key | What is checked |
| --- | --- |
| `database` | SQL connection and a basic query |
| `redis` | L0 Redis ping |
| `qdrant` | Qdrant collection listing |
| `scheduler` | Whether the scheduler is running and the archive job is registered (stopped or missing job → `degraded`) |

Overall status computation:

```text
any check unhealthy        → overall unhealthy
otherwise any check degraded → overall degraded
otherwise                  → healthy
```

### pipeline

| Field | Type | Meaning |
| --- | --- | --- |
| `pending_distill` | number | L1 records not yet distilled |
| `pending_archive` | number | L1 records older than `ARCHIVE_DAYS` (default 30 days) that are not yet archived |
| `recent_failures` | number | Recent failure count (always 0 in the current version; reserved for outbox failure statistics) |
| `l0_pipeline_enabled` | boolean | Whether the L0 memory output pipeline is enabled |
| `profile_items` | number | Active L2 Profile count |
| `fact_items` | number | Active L2 Fact count |
| `summary_items` | number | L2 Summary count |
| `conflict_count` | number | Total conflict-log entries (`mems_l2_conflict_log`) |
| `stale_profile_items` | number | Active Profiles not re-verified for more than 365 days |
| `stale_fact_items` | number | Active Facts not re-verified for more than 365 days |
| `stale_summary_items` | number | Summaries not re-verified for more than 365 days |

Notes:

- `pending_archive` treats a record as expired when `created_at < now - ARCHIVE_DAYS`;
- the `stale_*` threshold is fixed at 365 days (`STALE_MEMORY_DAYS`), matching
  the verification-decay effect on ranking;
- `summary_items` counts all Summaries (including those still waiting for
  vector sync), not only active items.

## Usage

- Health probing: use `GET /v1/mems/status` for pre-integration checks and
  manual troubleshooting;
- when the scheduler is unavailable, archiving will not run automatically;
  check `SCHEDULER_ENABLED` and the process state;
- a continuously growing `pending_distill` means the outbox worker is not
  consuming events or the LLM is not configured;
- this endpoint aggregates health checks through dependency injection, so tests
  can substitute fakes.

## Errors

| Scenario | HTTP | Notes |
| --- | --- | --- |
| Service unreachable | connection failure | check the process and port |
| Internal exception | `500` | an uncaught error while aggregating status |

Component-level exceptions do not make the whole endpoint return 500: the
corresponding check is marked `unhealthy` with a `detail`, and the overall
`status` is downgraded according to the rules above.
