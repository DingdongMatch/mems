# Mems

A layered, self-distilling, century-scale memory system for AI agents.

Mems is an industrial-grade memory hub solution for AI agents. Through a four-layer hot/cold-decoupled architecture, it provides a memory foundation with personality consistency, low-cost retrieval, and structured evolution.

- `L0` handles live session working memory
- `L1` stores episodic history and active online recall data
- `L2` distills reusable long-term knowledge
- `L3` archives old memory into JSONL

## Start Here

- [Quick Start](/en/quick-start)
- [Integration Overview](/en/integration/overview)
- [Architecture Overview](/en/introduction/architecture)
- [Benchmarks](/en/quality/benchmarks)

## Core Design Philosophy: Why Mems

- **⚡ Instant working memory (L0)** with Redis-backed live context
- **🧠 Asynchronous knowledge distillation (L2)** from raw L1 history to stable semantic facts
- **🔍 Evidence grounding** so distilled memory remains traceable to L1 source material
- **📜 Century-scale archive (L3)** with text-first JSONL retention
- **🛡️ Production-grade isolation** across tenant, user, agent, and session boundaries
- **🔌 Atomic integration** through `Context -> Query -> Write`
