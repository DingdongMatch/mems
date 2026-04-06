# `POST /v1/mems/query`

Run the default long-term retrieval path and return active L1 plus L2 results.

## Current Retrieval Signals

- L1 episodic recall
- Qdrant semantic recall
- L2 summary recall
- intent-aware ranking logic

## Current Implementation Note

Vector retrieval currently focuses on active L1 memory and L2 summaries. Profile, fact, and event records are still mainly ranked through structured database logic.
