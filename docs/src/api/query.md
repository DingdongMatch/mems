# `POST /v1/mems/query` — Recall

Recall memories across L0 hot snapshots, active L1 episodic records, and L2
distilled knowledge. The caller is responsible for assembling its own context
from the results.

## Current Retrieval Signals

- L0 hot-memory snapshots (lexical overlap against the snapshot content)
- L1 episodic recall via Qdrant vector search
- L2 summary recall via Qdrant vector search
- L2 profile, fact, and event recall via structured database queries
- intent-aware ranking, freshness, and verification-decay bonuses

## Current Implementation Note

L1 episodic records and L2 summaries are recalled through Qdrant vector search.
Profile, fact, and event records are ranked through structured database logic.
L0 snapshots are scored by lexical overlap against the current snapshot content.

## Response Fields

- `source`: memory origin (`l0_hot`, `l1_episodic`, `l2_profile`, `l2_fact`, `l2_event`, `l2_summary`)
- `content`: memory text
- `score`: relevance score
- `created_at`: creation time for the source record (absent for L0)
- `source_l1_ids`: traceable L1 record ids for evidence grounding
- `session_id`: optional source session (L0/L1 only)
- `tenant_id` / `user_id` / `scope`: identity context
