# What Is Mems

Mems is memory infrastructure for AI agents. It stores and recalls memories, not runtime state. Through a four-layer hot/cold-decoupled architecture, it provides a memory foundation with low-cost retrieval, structured evolution, and century-scale retention.

It addresses a few recurring problems:

- long-running agents accumulate noise without extracting signal
- raw chat logs pile up without becoming stable long-term knowledge
- historical events, preferences, and archive data are often mixed together in one bucket
- third-party systems struggle to integrate with unclear memory APIs

Mems solves this with four explicit layers:

- `L0`: hot memory cache for fast recall
- `L1`: episodic memory — the source of truth
- `L2`: distilled long-term knowledge
- `L3`: durable JSONL archive

For integrators, the API is simple:

1. `POST /v1/mems/write` — Remember
2. `POST /v1/mems/query` — Recall

Mems does not manage runtime state, session context, or task goals — those stay with the caller.
