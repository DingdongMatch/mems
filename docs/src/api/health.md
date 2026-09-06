# `GET /v1/mems/health`

## Endpoint

| Item | Value |
| --- | --- |
| URI | `/v1/mems/health` |
| Method | `GET` |
| Default Base URL | `http://localhost:8210` |

A lightweight liveness probe, suitable for:

- local smoke tests after startup;
- container health checks;
- reverse-proxy / orchestrator liveness probes.

This endpoint only reports whether the process is alive: it does not probe the
database, Redis, Qdrant, or the scheduler. For full dependency health use
[`/v1/mems/status`](/api/status).

## Request

None. It is a `GET` request and takes no request body.

```bash
curl http://localhost:8210/v1/mems/health
```

## Response

### HTTP 200

```json
{
  "status": "healthy",
  "version": "0.1.0"
}
```

### Field Reference

| Field | Type | Meaning |
| --- | --- | --- |
| `status` | string | Always `healthy` (returning HTTP 200 means the process is alive) |
| `version` | string | Mems service version |

## Errors

| Scenario | HTTP | Notes |
| --- | --- | --- |
| Service not started / stopped | connection failure | check the process and port |

This endpoint normally returns 200 even when dependency components are
unavailable, because it performs no component probing. In production, if you
want to route traffic only after dependencies are ready, use `/status` to
inspect each component's health.
