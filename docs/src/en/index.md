# Mems

A layered memory infrastructure for AI agents — remembers, recalls, distills, and archives.

Mems stores and recalls memories across four layers. The caller owns runtime state.

- `L0` handles hot recent-memory cache
- `L1` stores episodic memory — the source of truth
- `L2` distills reusable long-term knowledge
- `L3` archives old memory into JSONL

## Start Here

- [Quick Start](/en/quick-start)
- [Integration Overview](/en/integration/overview)
- [Architecture Overview](/en/introduction/architecture)
- [Benchmarks](/en/quality/benchmarks)

## Core Design Philosophy: Why Mems

- **⚡ Hot memory cache (L0)** with Redis-backed fast recall
- **🧠 Asynchronous knowledge distillation (L2)** from raw L1 history to stable semantic facts
- **🔍 Evidence grounding** so distilled memory remains traceable to L1 source material
- **📜 Century-scale archive (L3)** with text-first JSONL retention
- **🛡️ Production-grade isolation** across tenant, user, agent, and session boundaries
- **🔌 Atomic integration** through `Remember -> Recall`
