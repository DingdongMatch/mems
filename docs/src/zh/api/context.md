# `GET /v1/mems/context`

读取当前 `session_id` 的 live context 首页，并支持用 `before_id` 向前分页读取更早的 L1 历史。

## 关键行为

- 不传 `before_id` 时，返回 live 首页
- live 首页优先使用 Redis L0
- 当 Redis 不能完整覆盖最近内容时，会合并最新 L1 记录
- `limit` 表示每页读取的 L1 record 数量

## 关键响应字段

- `page_type`
- `has_more`
- `next_before_id`
- `messages`
