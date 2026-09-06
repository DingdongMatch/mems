# Identity Model

Mems describes memory ownership and visibility with flat string fields. It does
not assume a business hierarchy and does not have a built-in account system.
The following fields are used on both `/v1/mems/write` and `/v1/mems/query`.

## Field Overview

| Field | Type | Role | Boundary type |
| --- | --- | --- | --- |
| `tenant_id` | string | tenant / organization | hard boundary (optional; equality filter when provided) |
| `user_id` | string | user | hard boundary (optional; equality filter when provided) |
| `agent_id` | string | agent | hard boundary (required) |
| `scope` | string | business-defined visibility label | soft label (optional; equality filter when provided) |
| `session_id` | string | write-side action provenance | not an isolation boundary (not accepted by query) |

The difference between hard boundaries and soft labels:

```text
tenant_id / user_id / agent_id
    |
    v
hard isolation: decides "which memories can be accessed"

scope
    |
    v
soft visibility: narrows further within the identity range
(private / shared / team:* ...)
```

## Filtering Semantics (Important)

The current implementation uses **equality filtering**:

```text
field provided -> only memories where that field equals the value
field omitted  -> no narrowing along that dimension
```

Therefore:

- a query carrying only `agent_id` sees every tenant/user/scope memory under
  that agent;
- `scope` can only narrow within the identity fields already provided; it can
  never widen visibility;
- `agent_id` is the only required dimension, but multi-user systems **cannot**
  rely on it alone for isolation.

Recommended multi-user combination:

```json
{
  "tenant_id": "tenant-001",
  "user_id": "user-42",
  "agent_id": "support-agent",
  "scope": "private"
}
```

`write` and `query` must use a consistent identity. If a write omits `user_id`
and a later query provides it, that memory cannot be recalled; conversely, if
both sides omit it, different users may see each other's data.

## The Role of session_id

- `POST /v1/mems/write` accepts `session_id` to answer "which session did this
  memory come from";
- L0 snapshots are organized per `(identity + session_id)`, and writes to the
  same session are merged;
- the L1 master record persists `session_id`;
- `POST /v1/mems/query` does **not** accept `session_id`; it cannot be used as a
  recall filter or an isolation boundary;
- query responses carry `session_id` on L0/L1 results; L2 distilled knowledge
  does not depend on it.

## Recommended Usage

| Scenario | Recommendation |
| --- | --- |
| Single-user local agent | use one fixed `user_id` (e.g. `local-user`), leave `tenant_id` empty, `scope="private"` |
| Multi-user SaaS | pass `tenant_id` + `user_id` explicitly on every request |
| Multi-agent system | use a different `agent_id` per agent; still pass `user_id` for user isolation as needed |
| Shared / team spaces | express `private`, `shared`, `team:*` with `scope`, combined with identity boundaries |

Naming conventions:

- `tenant_id`: stable enterprise/organization id (e.g. `acme`, `tenant_001`);
- `user_id`: stable business-user id (avoid PII; be careful about using raw
  email addresses as ids);
- `agent_id`: id of the agent instance deployed on the user side;
- `scope`: lowercase; recommended styles are `private`, `shared`,
  `team:<team-id>`.

## Relationship to Storage

```text
L0 (Redis key)   : mems:l0:{tenant}:{user}:{scope}:{agent}:{session}
L1 (SQL columns) : tenant_id / user_id / agent_id / session_id / scope
L2 (SQL columns) : tenant_id / user_id / agent_id / scope (no session_id)
Qdrant payload   : mirrors L1/L2 for vector filtering
L3 (JSONL fields): keeps the full identity fields
```

At query time, both SQL and Qdrant apply equality filters on the non-null
identity fields of the request.

## Checklist

- [ ] all call sites use the same identity field names;
- [ ] multi-user services pass `user_id` on both `write` and `query`;
- [ ] tests cover "what is visible with only `agent_id`" so isolation matches
      expectations;
- [ ] `session_id` is never used as an isolation dimension;
- [ ] when `scope` controls shared/private spaces, identity boundaries are kept
      in place.

More semantics: [Memory Recall Protocol](/introduction/memory-recall-protocol)
and [Integration Overview](/integration/overview).
