# `GET /v1/mems/context`

Fetch the live context page for a `session_id` and page backward through older L1 history with `before_id`.

## Key Behavior

- without `before_id`, Mems returns the live page
- the live page prefers Redis L0 data
- if Redis does not fully cover the latest persisted history, Mems merges in recent L1 rows
- `limit` is the number of L1 records per page

## Important Response Fields

- `page_type`
- `has_more`
- `next_before_id`
- `messages`
