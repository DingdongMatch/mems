# Roadmap

## Current Capability: L3 → L2 Re-Distillation

Manual re-distillation (`POST /v1/mems/redistill` or `python -m mems.redistill`)
is a current capability, not a future item: archived JSONL is re-distilled with
the current `OPENAI_MODEL`, deduplicated by content fingerprint + model, and
upgraded through the L2 version chain.

## Planned: L1 Deletion Strategy

Today Mems never deletes L1 rows — the lifecycle is archive (mark `is_archived`).
If online L1 deletion is added later, it must stay compatible with the
re-distillation loop:

- **Only delete archived L1**: allow deletion only for records already archived
  (`is_archived=True`), or archive first, so L3 always keeps a re-distillable
  copy. Deleting before archiving loses the memory irreversibly and breaks the
  loop.
- **Cascade Qdrant cleanup**: deleting an L1 row must also delete its vector
  replica (`delete_points`) to avoid orphaned points that waste storage and
  produce dead candidates.
- **Redirect L2 evidence**: `source_l1_ids` pointing to a deleted L1 row become
  dangling references; the re-distill progress table already records
  `l3_path/l3_line` as a fallback evidence pointer, and new L2 lineage should
  prefer the L3 pointer when the L1 row is gone.

## Other Ideas

- Distributed distillation lock (Redis `SETNX`) so multi-replica deployments do
  not re-distill the same batch concurrently.
- L0 output pipeline consumption metrics (`XLEN` / consumer lag) in `/status`.
- Recall score decomposition (`explain`) for tuning the hybrid ranker.
