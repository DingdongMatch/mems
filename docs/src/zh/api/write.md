# `POST /v1/mems/write`

按 turn 追加 user / assistant 消息，并把新增消息持久化到 L0 / L1。

## 支持字段

- `messages`
- `ttl_seconds`
- `active_plan`
- `temp_variables`
- `metadata`

## 行为说明

- L0 中的 `short_term_buffer` 采用有限滑窗
- 同步到 L1 时只持久化本次新增消息
- 成功后会返回是否已写入 L1 以及对应 `l1_id`
