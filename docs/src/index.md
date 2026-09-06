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
    details: "Redis-backed snapshots of recent sessions deliver millisecond recall of active context."
  - title: "L1 Episodic Memory Ledger"
    details: "SQL persists raw narratives and structured messages, with Qdrant vector copies; replayable and auditable, it is the source of truth for online memory."
  - title: "L2 Knowledge Distillation"
    details: "A background LLM pipeline distills raw narratives into profiles, facts, events, and summaries — stable, reusable semantic knowledge."
  - title: "L3 Century-Scale Archive"
    details: "A text-first JSONL archive keeps ultra-long-term memory readable, portable, and re-distillable — resilient to hardware changes and format obsolescence."
  - title: "Evidence Traceability & Versioning"
    details: "Every L2 insight keeps its L1 source and version chain, so long-term knowledge remains traceable and explainable even after re-distillation."
  - title: "L3 → L2 Re-Distillation"
    details: "The archive is not a dead end. Upgrade your model, re-distill archived memories, and evolve L2 knowledge losslessly through the version chain — the system grows with each model generation."
  - title: "Production Isolation"
    details: "Built-in hard boundaries for tenant, user, and agent, plus session and scope tags, fit multi-tenant SaaS and enterprise deployments."
  - title: "Atomic Integration"
    details: "Third-party agents plug in with just Remember and Recall — no storage plumbing to manage."
---
