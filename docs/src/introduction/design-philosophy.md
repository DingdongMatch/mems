# Design Philosophy

## Layered Memory

Hot cache, raw events, stable knowledge, and cold archives are different kinds of data and should not share one recall surface. Mems models them as explicit L0–L3 layers, each with its own storage and processing strategy.

## Decentralized Schema

Mems does not assume one fixed upstream identity model. Requests carry `tenant_id`, `user_id`, and `agent_id`; `session_id` is optional provenance. The API accepts an optional generic `namespace` path for caller business context; Mems combines it with validated identity into an immutable `MemoryNamespace`. The caller application defines business authorization and supplies authorized identity and namespace context.

## SQL-First Online Truth

Online memory is committed to SQL as the source of truth. Qdrant holds a derived online replica, and JSONL is reserved for L3 archive output. Writing SQL first keeps replica-sync failures from disrupting the primary flow.

## Text-First

Long-horizon history should not be locked into a vector store or proprietary format. L3 archives use JSONL to keep memory readable, inspectable, and portable over the long term.

## Model-Evolving Memory

Long-term memory should improve as models improve. L3 JSONL is kept as a durable, model-agnostic feedstock; manual re-distillation sends archived records back through the current distillation pipeline, and the L2 version chain upgrades old knowledge non-destructively. The archive is not a tombstone but a source that can be reborn under a newer, stronger model.

## Time-Forward

Mems is time-forward: what has happened only moves forward — it is never rewritten, degraded, or retracted. Past memories are not down-weighted or edited based on usage feedback. If later evidence contradicts them, write a new memory at a later point in time and let the L2 version chain (`supersedes` + conflict log) evolve forward — the original record always remains.
