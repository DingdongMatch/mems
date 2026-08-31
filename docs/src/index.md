---
layout: home

hero:
  name: "Mems"
  text: "Layered memory for AI agents"
  tagline: "Remember, recall, distill, archive, re-distill — a self-evolving memory infrastructure for AI agents."
  actions:
    - theme: brand
      text: Quick Start
      link: /quick-start
    - theme: alt
      text: 中文文档
      link: /zh/

features:
  - title: "L0 Hot Memory Cache"
    details: "Redis-backed fast recall for recent memories, without owning runtime state."
  - title: "L2 Knowledge Distillation"
    details: "An automated LLM pipeline turns raw L1 narratives into reusable semantic knowledge."
  - title: "Grounded Memory"
    details: "Distilled outputs stay linked to L1 evidence so long-term memory remains traceable."
  - title: "L3 Archive"
    details: "A text-first JSONL archive keeps ultra-long-term memory readable, portable, and durable."
  - title: "L3 → L2 Re-Distillation"
    details: "The archive is not a dead end. When your model improves, re-distill archived memories and upgrade long-term knowledge through the version chain."
  - title: "Production Isolation"
    details: "Tenant, user, agent, and session boundaries support SaaS and enterprise deployment."
  - title: "Atomic Integration"
    details: "Third-party agents integrate with one compact flow: Remember, then Recall."
---

## Start Here

- [Quick Start](/quick-start)
- [Integration Overview](/integration/overview)
- [Architecture Overview](/introduction/architecture)
- [Benchmarks](/quality/benchmarks)

## Core Design Philosophy: Why Mems

- **⚡ Hot memory cache (L0)** with Redis-backed fast recall
- **🧠 Asynchronous knowledge distillation (L2)** from raw L1 history to stable semantic facts
- **🔍 Evidence grounding** so distilled memory remains traceable to L1 source material
- **📜 Century-scale archive (L3)** with text-first JSONL retention
- **🛡️ Production-grade isolation** across tenant, user, agent, and session boundaries
- **🔌 Atomic integration** through `Remember -> Recall`
