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
- `L3`: durable JSONL archive — also the feedstock for re-distillation

For integrators, the API is simple:

1. `POST /v1/mems/write` — Remember
2. `POST /v1/mems/query` — Recall
3. `POST /v1/mems/redistill` — evolve archived memories with a newer model

Mems does not manage runtime state, session context, or task goals — those stay with the caller.

## The Evolution Loop: L3 → L2 Re-Distillation

Archives are not a dead end. L3 JSONL keeps the full raw memory, so it can be
**re-distilled whenever the underlying model gets better**. Trigger a manual
re-distillation after a model upgrade; archived records flow back into the
distillation pipeline, and the L2 version chain (`supersedes` + conflict log)
non-destructively upgrades old knowledge to what the current model can extract.
The system therefore evolves together with the model — memory is written once
and can be reborn, layer by layer, as the model improves.
