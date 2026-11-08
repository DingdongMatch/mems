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
    details: "Redis 承载近期会话快照，毫秒级召回活跃上下文。"
  - title: "L1 情景记忆账本"
    details: "SQL 持久化原始叙事与结构化消息，Qdrant 提供向量副本；可回放、可审计，是在线记忆的真相源。"
  - title: "L2 异步知识蒸馏"
    details: "后台 LLM 流水线自动把原始叙事提炼为画像、事实、事件与摘要，沉淀稳定、可复用的语义知识。"
  - title: "L3 跨世纪归档"
    details: "Text-First 的 JSONL 归档让超长期记忆可读、可迁移、可再蒸馏，对抗硬件更迭与格式淘汰。"
  - title: "证据溯源与版本管理"
    details: "L2 知识保留 L1 来源与版本链，再蒸馏仍可回溯至原始语料，让长期记忆可追溯、可解释。"
  - title: "L3 → L2 再蒸馏"
    details: "归档不是终点。模型升级后手动重蒸 L3，经版本链无损升级 L2，让系统随模型能力持续进化。"
  - title: "生产级隔离"
    details: "服务端生成的 namespace 统一 Tenant、User、Agent 存储分区规则；业务授权由调用方应用负责。"
  - title: "原子化接入"
    details: "第三方 Agent 只需 Remember 与 Recall 两步即可接入，无需感知底层存储。"
---
