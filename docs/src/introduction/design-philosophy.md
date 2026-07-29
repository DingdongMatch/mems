# Design Philosophy

## Text-First

Long-horizon history should remain readable and portable. Mems uses JSONL for L3 archive output instead of locking everything into one vector store or proprietary format.

## Decentralized Schema

Mems does not assume one fixed upstream identity model. `tenant_id`, `user_id`, `agent_id`, `session_id`, and `scope` together define memory isolation and visibility.

## Memory, Not Runtime

Mems stores memories, not agent runtime state. It does not manage session context, task goals, active plans, or temporary variables. The caller owns its own runtime.

## Layered Memory

Hot cache, raw events, stable knowledge, and cold archive are not the same kind of data. Mems keeps them in explicit layers instead of collapsing them into one giant memory bucket.

## SQL-First Online Truth

Current online memory is committed to SQL first. Qdrant is treated as a derived online replica, while JSONL is reserved for L3 archive output.
