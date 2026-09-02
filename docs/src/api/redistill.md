# `POST /v1/mems/redistill` — Re-distill Archived Memories

Manually re-distill archived L3 records into L2 using the current
`OPENAI_MODEL`. This is the "memory evolves with the model" loop: after a model
upgrade, archived JSONL is fed back through extraction and reconciliation, and
L2 knowledge is upgraded through the version chain (`supersedes` + conflict log).

## Request Fields

- `agent_id`: optional filter to re-distill only one agent's archived records
- `start_date` / `end_date`: optional creation-time range (inclusive)
- `min_importance`: minimum `importance_score` (0-1), default `0.0`
- `batch_size`: maximum L3 records to process, default `REDISTILL_BATCH_SIZE`

## Behavior Notes

- Reads from `storage/l3_archive/*.jsonl` and `mems_l3_archive` batch metadata.
- Deduplicates by content fingerprint + model, so records already processed with
  the current model are skipped; a model change automatically makes old records
  eligible again.
- Missing or malformed lines are logged and skipped without marking progress,
  so they can be retried later.
- Returns `processed` / `created` / `updated` / `skipped` counts.

## CLI Alternative

```bash
uv run python -m mems.redistill --agent demo-agent
```
