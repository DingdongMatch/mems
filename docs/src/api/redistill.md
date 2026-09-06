# `POST /v1/mems/redistill` — Re-distill Archived Memories

## Endpoint

| Item | Value |
| --- | --- |
| URI | `/v1/mems/redistill` |
| Method | `POST` |
| Content-Type | `application/json` |
| Default Base URL | `http://localhost:8210` |

Manually re-distill archived L3 records (JSONL) into L2: with the current
`OPENAI_MODEL`, archived originals go through "LLM structured extraction +
deterministic reconciliation + L2 version-chain upgrade" again — memory evolves
with the model.

Re-distillation is a **manual maintenance action** and is unrelated to online
recall; `/v1/mems/query` behavior does not change just because a re-distillation
ran, unless the L2 records themselves changed.

## Request Parameters

The request body is JSON; every field is optional (an empty request = full
re-distillation):

| Field | Type | Required | Default | Constraint | Meaning |
| --- | --- | --- | --- | --- | --- |
| `agent_id` | string | no | `null` | — | only re-distill this agent's archived records |
| `start_date` | string | no | `null` | ISO-8601 | only records with `created_at >= start_date` |
| `end_date` | string | no | `null` | ISO-8601 | only records with `created_at <= end_date` |
| `min_importance` | number | no | `0.0` | 0-1 | only records with `importance_score >= min_importance`; 0 means no filter |
| `batch_size` | number | no | `100` | 1-1000 | maximum L3 records processed in this run (HTTP-level bound); omitted uses `REDISTILL_BATCH_SIZE` |

> `start_date` / `end_date` apply to the archived record's own `created_at`
> (the original L1 write time), not the archive-file time. Naive timestamps are
> interpreted as UTC.

### Example Request

```json
{
  "agent_id": "demo-agent",
  "start_date": "2026-01-01T00:00:00Z",
  "end_date": "2026-06-30T23:59:59Z",
  "min_importance": 0.3,
  "batch_size": 100
}
```

```bash
curl -X POST http://localhost:8210/v1/mems/redistill \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "demo-agent",
    "batch_size": 100
  }'
```

## Response

### HTTP 200

```json
{
  "success": true,
  "processed": 37,
  "created": 12,
  "updated": 8,
  "skipped": 17,
  "message": "Re-distilled 37 records with gpt-4o-mini (created 12, updated 8, skipped 17)"
}
```

### Field Reference

| Field | Type | Meaning |
| --- | --- | --- |
| `success` | boolean | whether the run succeeded; when `false`, `message` explains why |
| `processed` | number | records actually re-distilled and successfully processed in this run (excludes `skipped`) |
| `created` | number | L2 records created (Profile/Fact/Event/Summary) |
| `updated` | number | existing L2 records updated/enhanced |
| `skipped` | number | records skipped (already processed with the current model, or missing `content`) |
| `message` | string | result summary or error message |

## Behavior

### Data Source

- archived JSONL lives under `ARCHIVE_STORAGE_PATH` (default `storage/l3_archive/`);
- batch metadata is read from the `mems_l3_archive` table;
- each line carries the original L1 identity, `content`, `messages`,
  `created_at`, `importance_score`, etc.

### Deduplication and Version Chain

- deduplicated by "content fingerprint + model"; unique key `(content_hash, model)`;
- records already processed with the current `OPENAI_MODEL` are skipped; after a
  model change, old records become eligible again automatically;
- progress is written to `mems_redistill_progress`, including `l3_path` /
  `l3_line` fallback evidence pointers;
- new L2 records keep the original L1 id in `source_l1_ids`; conflicts go
  through deterministic reconciliation into `mems_l2_conflict_log`, and the
  version chain moves forward (`supersedes` + `version`).

### Fault Tolerance

- a single record that fails during processing (e.g. an LLM error) is logged and
  skipped **without marking progress**, so it can be retried on a later run;
- a syntactically invalid JSON line (one that cannot be parsed) makes the whole
  run fail with `success=false` — it is not skipped per line;
- the run commits once at the end; an overall exception returns `success=false`
  with a message.

### Prerequisites

- a working OpenAI-compatible LLM (`OPENAI_API_KEY`, etc.);
- at least one archived batch; without any archive, the endpoint returns
  `processed=0` with a notice message.

## Errors

| Scenario | HTTP | Notes |
| --- | --- | --- |
| Invalid parameter type/range | `422` | validation failed (e.g. `min_importance` out of range, malformed date) |
| Overall run failure (archive file unreadable, invalid JSON, etc.) | `500` | `detail` carries the message |
| LLM not configured / invalid key | `200` (`success=true`, `processed=0`) | every record fails and is logged, but the overall request still returns 200; check your `OPENAI_*` configuration first |

> A single per-record failure does not turn the request into 500; only an
> overall run failure does.

## CLI Alternative

```bash
# Full re-distillation
uv run python -m mems.redistill

# Filter by agent / time / importance / batch size
uv run python -m mems.redistill \
  --agent demo-agent \
  --start 2026-01-01T00:00:00Z \
  --end 2026-06-30T23:59:59Z \
  --min-importance 0.3 \
  --batch 100
```

CLI argument mapping:

| CLI | request field |
| --- | --- |
| `--agent` | `agent_id` |
| `--start` | `start_date` |
| `--end` | `end_date` |
| `--min-importance` | `min_importance` |
| `--batch` | `batch_size` |

The CLI prints the same result structure as JSON.

## Notes

- re-distillation is manual; there is no scheduled job. Run it proactively after
  a model upgrade;
- online recall never triggers re-distillation automatically, and never falls
  back to L3 automatically;
- archives must exist first (default: automatic daily archiving at 03:00, or
  wait until `ARCHIVE_DAYS` elapses);
- re-distillation calls the LLM; cost and time grow roughly linearly with the
  number of records — use `batch_size` and `min_importance` to bound runs.
