---
layout: home

hero:
  name: "Mems"
  text: "面向 AI Agent 的分层记忆系统"
  tagline: "写入、召回、蒸馏、归档、再蒸馏 — 随模型能力持续进化的 Agent 记忆基础设施。"
  actions:
    - theme: brand
      text: 快速开始
      link: /zh/quick-start
    - theme: alt
      text: English Docs
      link: /

features:
  - title: "L0 热记忆缓存"
    details: "基于 Redis 的近期记忆缓存，毫秒级快速召回，不接管运行时状态。"
  - title: "L2 异步知识蒸馏"
    details: "通过自动化 LLM 流水线，把原始 L1 叙事沉淀为稳定的语义知识。"
  - title: "证据溯源"
    details: "L2 提炼结果与 L1 原始语料保持关联，保证长期记忆可追溯、可解释。"
  - title: "L3 跨世纪归档"
    details: "采用 Text-First 策略，把超长期记忆沉淀为可读、可迁移的 JSONL 文件。"
  - title: "L3 → L2 再蒸馏"
    details: "归档不是终点。模型升级后，手动触发再蒸馏，让旧记忆通过版本链升级为当前模型能力下的长期知识，系统随模型持续进化。"
  - title: "生产级隔离"
    details: "内建 Tenant、User、Agent、Session 边界，适配 SaaS 与企业级部署。"
  - title: "原子化接入"
    details: "第三方 Agent 只需 Remember 和 Recall 两步即可接入。"
---
