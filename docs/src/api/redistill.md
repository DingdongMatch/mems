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

The request body is JSON. `tenant_id`, `user_id`, and `agent_id` are required;
a missing body or an empty object (`{}`) returns HTTP 422. Re-distillation is
always bounded by the selected identity and partition.

| Field | Type | Required | Default | Constraint | Meaning |
| --- | --- | --- | --- | --- | --- |
| `tenant_id` | string | yes | — | — | tenant boundary |
| `user_id` | string | yes | — | — | user boundary |
| `agent_id` | string | yes | — | — | only re-distill this agent's archived records |
| `namespace` | array of strings | no | `[]` | at most 16 non-empty segments, each at most 128 characters | exact partition path; omitted means this identity's default partition, not all child paths |
| `start_date` | string | no | `null` | ISO-8601 | only records with `created_at >= start_date` |
| `end_date` | string | no | `null` | ISO-8601 | only records with `created_at <= end_date` |
| `min_importance` | number | no | `0.0` | 0-1 | only records with `importance_score >= min_importance`; 0 means no filter |
| `batch_size` | number | no | `REDISTILL_BATCH_SIZE` | 1-1000 | maximum eligible records attempted (including failures); the service caps it at `REDISTILL_BATCH_SIZE` (default 100) |

The canonical JSON key combining identity and namespace (including version labels and separators) must fit within **512 UTF-8 bytes**. Larger partitions return HTTP 422. This also applies to the default partition when namespace is omitted.

> `start_date` / `end_date` apply to the archived record's own `created_at`
> (the original L1 write time), not the archive-file time. Naive timestamps are
> interpreted as UTC.

### Example Request

```json
{
  "tenant_id": "acme",
  "user_id": "user-42",
  "agent_id": "demo-agent",
  "namespace": ["projects", "prj_demo"],
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
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "batch_size": 100
  }'
```

## Response

### HTTP 200

```json
{
  "success": true,
  "attempted": 37,
  "failed": 0,
  "processed": 37,
  "created": 12,
  "updated": 8,
  "skipped": 17,
  "message": "Re-distilled 37 records with gpt-4o-mini (attempted 37, failed 0, created 12, updated 8, skipped 17)"
}
```

### Field Reference

| Field | Type | Meaning |
| --- | --- | --- |
| `success` | boolean | true when no processing failed; false for source/run failures or a disabled LLM |
| `attempted` | number | eligible records attempted; failed processing also consumes the batch budget |
| `failed` | number | records that failed processing; no progress is saved, so they can be retried |
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
- profiles and single-valued facts reconcile by the original source created_at; older sources cannot overwrite newer values, while equal-time sources allow model-upgrade corrections;
- new L2 records keep the original L1 id in `source_l1_ids`; conflicts go
  through deterministic reconciliation into `mems_l2_conflict_log`, and the
  version chain moves forward (`supersedes` + `version`).

### Fault Tolerance

- a single record that fails during processing (e.g. an LLM error) is logged and
  skipped **without marking progress**, so it can be retried on a later run;
- malformed JSON, invalid UTF-8, and non-object JSON lines are logged and skipped;
- missing, unsafe, or unreadable archive files are logged and skipped;
- each successful source commits independently; later failures preserve committed progress and counts;
- completed runs return HTTP 200 with success=false and attempted / failed / processed counts for processing failures. Caught run exceptions also preserve committed counts and return a generic failure message; unhandled endpoint exceptions return HTTP 500.

### Prerequisites

- LLM_PROVIDER=openai and a working OpenAI-compatible LLM (`OPENAI_API_KEY`, etc.);
- at least one archived batch; without any archive, the endpoint returns
  `processed=0` with a notice message.

## Errors

| Scenario | HTTP | Notes |
| --- | --- | --- |
| Missing body, empty `{}`, or missing identity | `422` | `tenant_id`, `user_id`, and `agent_id` are required |
| Invalid parameter type/range | `422` | validation failed (e.g. `min_importance` out of range, malformed date) |
| Unhandled endpoint exception | `500` | generic internal-error response; diagnostic details are logged server-side |
| Processing failure within a run | `200` (`success=false`) | preserves committed counts; failed records do not save progress |
| Disabled LLM / invalid key | `200` (`success=false`) | disabled means attempted=0; failed calls consume the batch budget |

> Source failures are explicitly reported through success=false and failed; malformed lines count toward skipped.

## Notes

- re-distillation is manual; there is no scheduled job. Run it proactively after
  a model upgrade;
- online recall never triggers re-distillation automatically, and never falls
  back to L3 automatically;
- archives must exist first (default: automatic daily archiving at 03:00, or
  wait until `ARCHIVE_DAYS` elapses);
- re-distillation calls the LLM; cost and time grow roughly linearly with the
  number of records — use `batch_size` and `min_importance` to bound runs.
