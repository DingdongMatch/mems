---
layout: home

hero:
  name: "Mems"
  text: "面向 AI Agent 的分层记忆系统"
  tagline: "一套支持自蒸馏、长期召回与跨世纪归档的工业级记忆中枢。"
  actions:
    - theme: brand
      text: 快速开始
      link: /zh/quick-start
    - theme: alt
      text: English Docs
      link: /

features:
  - title: "L0 瞬时工作记忆"
    details: "基于 Redis 的高速上下文缓存，保证当前 session 具备毫秒级响应能力。"
  - title: "L2 异步知识蒸馏"
    details: "通过自动化 LLM 流水线，把原始 L1 叙事沉淀为稳定的语义知识。"
  - title: "证据溯源"
    details: "L2 提炼结果与 L1 原始语料保持关联，保证长期记忆可追溯、可解释。"
  - title: "L3 跨世纪归档"
    details: "采用 Text-First 策略，把超长期记忆沉淀为可读、可迁移的 JSONL 文件。"
  - title: "生产级隔离"
    details: "内建 Tenant、User、Agent、Session 边界，适配 SaaS 与企业级部署。"
  - title: "原子化接入"
    details: "第三方 Agent 只需遵循 Context、Query、Write 三步即可完成接入。"
---
