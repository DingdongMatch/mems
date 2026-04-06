# `POST /v1/mems/write`

Append user and assistant turns and persist the new messages into L0 and L1.

## Supported Fields

- `messages`
- `ttl_seconds`
- `active_plan`
- `temp_variables`
- `metadata`

## Behavior Notes

- the L0 `short_term_buffer` uses a bounded sliding window
- only newly appended messages are persisted into L1
- successful writes return whether L1 persistence succeeded and the resulting `l1_id`
