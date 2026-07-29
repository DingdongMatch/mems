# `POST /v1/mems/write` — Remember

Remember memory content into the system. Mems stores what matters — raw episodic messages or direct memory text — and leaves session state to the caller.

## Supported Fields

- `content`
- `messages`
- `ttl_seconds`
- `tenant_id` / `user_id` / `agent_id` / `session_id` / `scope`

## Behavior Notes

- at least one of `content` or `messages` must be provided
- if both are provided, `content` is used as the memory text and `messages` is preserved as structured source
- L0 caches recent memories with a bounded sliding window and TTL
- L1 persists a new episodic record with the full memory text and optional source messages
- returns whether L1 persistence succeeded and the resulting `l1_id`
