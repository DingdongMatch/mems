# `POST /v1/mems/query` — Recall

Recall memories across active L1 episodic records and L2 distilled knowledge. The caller is responsible for assembling its own context from results.

## Current Retrieval Signals

- L1 episodic recall
- Qdrant semantic recall
- L2 summary recall
- intent-aware ranking logic
- L2 profile, fact, and event recall via structured queries

## Current Implementation Note

Vector retrieval currently focuses on active L1 memory and L2 summaries. Profile, fact, and event records are still mainly ranked through structured database logic.

## Response Fields

- `source`: memory origin (`l1_episodic`, `l2_profile`, `l2_fact`, `l2_event`, `l2_summary`)
- `content`: memory text
- `score`: relevance score
- `source_l1_ids`: traceable L1 record ids for evidence grounding
- `session_id`: optional source session (L1 only)
