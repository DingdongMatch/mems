# What Is Mems

Mems is an industrial-grade memory hub solution for AI agents. Through a four-layer hot/cold-decoupled architecture, it provides a memory foundation with personality consistency, low-cost retrieval, and structured evolution.

It addresses a few recurring problems:

- prompt windows are short and recent context disappears quickly
- raw chat logs pile up without becoming stable long-term knowledge
- current session state, historical events, preferences, and archive data are often mixed together
- third-party systems struggle to integrate with unclear memory APIs

Mems solves this with four explicit layers:

- `L0`: live session context
- `L1`: episodic memory and online history
- `L2`: distilled long-term knowledge
- `L3`: durable JSONL archive

For integrators, the main flow is simple:

1. `GET /v1/mems/context`
2. `POST /v1/mems/query`
3. let the agent build the answer
4. `POST /v1/mems/write`
