# `POST /v1/mems/write` — 写入记忆

将记忆内容写入系统。Mems 只存储值得记住的东西 — 原始对话消息或直接记忆文本 — 运行时状态由调用方自行管理。

## 支持字段

- `content`
- `messages`
- `ttl_seconds`
- `importance_score`
- `tenant_id` / `user_id` / `agent_id` / `session_id` / `scope`

## 行为说明

- `content` 和 `messages` 至少提供一个
- 如果同时提供，`content` 作为记忆文本，`messages` 作为结构化来源原文保留
- L0 以有限滑窗 + TTL 缓存近期记忆
- L1 落一条新的情景记录，包含完整记忆文本和可选的来源消息
- `importance_score` 控制蒸馏优先级；达到
  `DISTILL_HIGH_IMPORTANCE_THRESHOLD`（默认 0.8）的记录写入后立即触发
  batch=1 蒸馏
- 返回是否已成功持久化到 L1 及对应的 `l1_id`
