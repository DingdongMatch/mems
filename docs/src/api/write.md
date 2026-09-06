# `POST /v1/mems/write` — Remember

## Endpoint

| Item | Value |
| --- | --- |
| URI | `/v1/mems/write` |
| Method | `POST` |
| Content-Type | `application/json` |
| Default Base URL | `http://localhost:8210` |

Remember one memory: write the L0 hot snapshot synchronously and, in the same
transaction, persist the L1 episodic master record and enqueue vector-sync and
distillation-trigger events. Mems accepts only memory content and identity
fields — never runtime state (session context, task goals, active plans, etc.
belong to the caller).

## Request Parameters

The request body is JSON:

| Field | Type | Required | Default | Constraint | Meaning |
| --- | --- | --- | --- | --- | --- |
| `agent_id` | string | yes | — | non-empty | agent boundary |
| `content` | string | conditional | `null` | non-empty | memory text; at least one of `content` / `messages` must be provided |
| `messages` | array | conditional | `[]` | items below | structured source messages; at least one of `content` / `messages` must be provided |
| `tenant_id` | string | no | `null` | — | tenant hard boundary |
| `user_id` | string | no | `null` | — | user hard boundary; pass it explicitly in multi-user scenarios |
| `session_id` | string | no | `null` | — | action provenance (not an isolation boundary) |
| `scope` | string | no | `null` | — | soft visibility label |
| `ttl_seconds` | number | no | `1800` | 1-604800 | TTL of the L0 hot snapshot (seconds); L1 records are not affected by this value |
| `importance_score` | number | no | `0.5` | 0-1 | importance, used for distillation priority |

`messages` item format:

| Field | Type | Required | Constraint | Meaning |
| --- | --- | --- | --- | --- |
| `role` | string | yes | 1-32 chars | message role (e.g. `user` / `assistant` / `system`) |
| `content` | string | yes | non-empty | message content |

### Choosing Between content and messages

```text
only content  → content becomes the memory text (used for retrieval / distillation)
only messages → Mems folds the messages into memory text (role: content, one per line)
both          → content becomes the memory text; messages are kept as structured source
```

> Folding example: `[{"role": "user", "content": "I like Rust"}, ...]` produces
> memory text like `user: I like Rust`. Folding is read-only: the original
> messages always remain in `messages` / the L1 `messages_json`.

### Example Request

```json
{
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "session_id": "session-007",
  "scope": "private",
  "content": "The user likes Rust and prefers concise answers.",
  "messages": [
    {"role": "user", "content": "Remember that I like Rust."},
    {"role": "assistant", "content": "Got it — I will keep replies concise."}
  ],
  "ttl_seconds": 1800,
  "importance_score": 0.8
}
```

```bash
curl -X POST http://localhost:8210/v1/mems/write \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-001",
    "user_id": "user-001",
    "agent_id": "agent-001",
    "session_id": "session-007",
    "scope": "private",
    "content": "The user likes Rust and prefers concise answers.",
    "importance_score": 0.8
  }'
```

## Response

### HTTP 200

```json
{
  "success": true,
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "session_id": "session-007",
  "scope": "private",
  "importance_score": 0.8,
  "content": "The user likes Rust and prefers concise answers.",
  "messages": [
    {"role": "user", "content": "Remember that I like Rust."},
    {"role": "assistant", "content": "Got it — I will keep replies concise."}
  ],
  "appended_count": 2,
  "persisted_to_l1": true,
  "l1_id": 101,
  "message": "Memory remembered and persisted to L1"
}
```

### Field Reference

| Field | Type | Meaning |
| --- | --- | --- |
| `success` | boolean | whether the request was handled successfully |
| `tenant_id` / `user_id` / `agent_id` / `session_id` / `scope` | string \| null | echoed identity fields |
| `importance_score` | number | echoed importance |
| `content` | string | the memory text actually stored (given `content`, or folded from `messages`) |
| `messages` | array | structured messages from this request (not the merged full buffer) |
| `appended_count` | number | number of messages appended by this request |
| `persisted_to_l1` | boolean | whether the L1 master record was persisted |
| `l1_id` | number \| null | id of the new L1 record |
| `message` | string | human-readable result message |

## Behavior After a Write

### 1. L0 hot snapshot

- the key is composed from identity + `session_id`;
- when a snapshot already exists for the same session, new messages are
  **appended**; once the short-term buffer exceeds 20 messages, the oldest are
  dropped (`DEFAULT_CONTEXT_WINDOW`);
- appending preserves the remaining TTL of the existing snapshot; a new session
  expires according to `ttl_seconds`;
- the snapshot `content` (memory text) becomes this request's `content` or the
  folded new messages; the short-term buffer appends new messages and trims the
  oldest.

### 2. L1 master record

- every call inserts a new Episodic row (`mems_l1_episodic`) — it never
  overwrites an older record;
- in the same transaction it enqueues `vector_sync` (asynchronous Qdrant vector
  replica) and the distillation-trigger event;
- L1 records have no TTL; aging is handled by archiving (default: L3 JSONL after
  30 days).

### 3. Distillation triggers

```text
importance_score >= 0.8 (DISTILL_HIGH_IMPORTANCE_THRESHOLD)
      → an immediate batch=1 targeted distillation is enqueued after the write

otherwise
      → distill_due is enqueued; per-agent distillation expands once pending
        records reach DISTILL_THRESHOLD (default 100)
```

Distillation is asynchronous: when `POST /v1/mems/write` returns, L2 does not
exist yet — wait for the outbox worker to consume the event. When the LLM is not
configured, distillation is silently skipped (the current implementation emits
neither a log line nor an API-visible message), and the write itself is
unaffected.

## Errors

| Scenario | HTTP | Notes |
| --- | --- | --- |
| both `content` and `messages` missing/empty | `422` | model validation failed |
| missing `agent_id` | `422` | model validation failed |
| `messages` item missing `role`/`content` | `422` | model validation failed |
| `ttl_seconds` outside 1-604800 | `422` | model validation failed |
| `importance_score` outside 0-1 | `422` | model validation failed |
| internal service exception (database/Redis, etc.) | `500` | `detail` carries the exception |

> When Redis is unavailable, the L0 write and the whole request fail (the
> current implementation does not silently degrade by skipping L0).

## Notes

- do not write runtime state as-is (`current_plan`, `tool_results`, full
  transcripts, etc.); write only content worth remembering in the short or long
  term;
- tune `importance_score` to business semantics: 0.8+ for explicit long-term
  preferences/key facts, ~0.5 for routine memories;
- when appending to the same session frequently, `messages` accumulate in the
  short-term buffer and the oldest are trimmed; keep the original transcript on
  your side if you need a complete audit trail;
- L0 is only a hot cache: after its TTL expires, the L1 master record remains
  (unless archived and later cleaned up by a future policy).
