# `POST /v1/mems/query` — Recall

## Endpoint

| Item | Value |
| --- | --- |
| URI | `/v1/mems/query` |
| Method | `POST` |
| Content-Type | `application/json` |
| Default Base URL | `http://localhost:8210` |

Recall memories across L0 hot snapshots, active L1 episodic records, and L2
distilled knowledge. Mems returns **Memory Candidates**; deciding whether they
enter the context and how to present them belongs to the caller. Mems does not
manage session context.

## Request Parameters

The request body is JSON:

| Field | Type | Required | Default | Constraint | Meaning |
| --- | --- | --- | --- | --- | --- |
| `agent_id` | string | yes | — | non-empty | agent boundary; every recall filters by this field |
| `query` | string | yes | — | non-empty and not blank after trimming | search text used for semantic / lexical retrieval |
| `tenant_id` | string | no | `null` | — | tenant hard boundary; equality filter when provided |
| `user_id` | string | no | `null` | — | user hard boundary; pass it explicitly in multi-user scenarios |
| `namespace` | string[] | no | `[]` | up to 16 non-empty segments, each at most 128 chars | generic caller context for an exact storage partition |
| `top_k` | number | no | `5` | 1-100 | maximum number of candidates returned |

Use the same `namespace` path used for writes. It selects one exact partition and
does not imply wildcard or descendant search. The caller application must
authorize the business context first. The current request also does **not**
accept the following fields (semantics in the
[Memory Recall Protocol](/introduction/memory-recall-protocol)):

- `session_id`: write-side provenance; not a recall filter or isolation field;
- `memory_types`: source filter, planned; recall currently always spans
  L0/L1/L2;
- `time_range`: time range for archived L3 recall, planned;
- `state` / `messages` / `system_prompt` / `current_plan` / `tools` and other
  runtime state: explicit non-goal;
- `intent`, `max_tokens`, etc.: explicit non-goal.

### Example Request

```json
{
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "query": "What language does the user mainly use?",
  "top_k": 5
}
```

```bash
curl -X POST http://localhost:8210/v1/mems/query \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-001",
    "user_id": "user-001",
    "agent_id": "agent-001",
    "query": "What language does the user mainly use?",
    "top_k": 5
  }'
```

## Response

### HTTP 200

```json
{
  "query": "What language does the user mainly use?",
  "results": [
    {
      "source": "l2_profile",
      "content": "like language Java",
      "score": 1.18,
      "source_l1_ids": [102, 231],
      "created_at": "2026-04-01T10:00:00Z",
      "tenant_id": "tenant-001",
      "user_id": "user-001"
    },
    {
      "source": "l1_episodic",
      "content": "user: I previously wrote an order system in Java\nassistant: Noted.",
      "score": 1.02,
      "source_l1_ids": [102],
      "session_id": "session-007",
      "created_at": "2026-03-20T08:00:00Z",
      "tenant_id": "tenant-001",
      "user_id": "user-001"
    }
  ],
  "total": 2
}
```

### Top-Level Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `query` | string | echoed search text |
| `results` | array | ranked Memory Candidates, ordered by `score` descending |
| `total` | number | number of items actually returned, equal to `results.length`, always ≤ `top_k`; not the candidate-pool size or database match count |

### results Item Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `source` | string | origin: `l0_hot` / `l1_episodic` / `l2_profile` / `l2_fact` / `l2_event` / `l2_summary` |
| `content` | string | readable memory text |
| `score` | number | composite ranking score; composition varies by source; meaningful only within this recall |
| `created_at` | string \| null | time context; absent for L0; for L2 Profile/Fact it is actually `last_verified_at` (see below) |
| `source_l1_ids` | number[] | evidence chain: L1 record ids backing this memory (L2 results for traceability; L1 results usually their own id) |
| `session_id` | string \| null | source session; only L0/L1 results carry a value |
| `tenant_id` | string \| null | identity context |
| `user_id` | string \| null | identity context |

The server derives immutable `MemoryNamespace` from validated identity
plus optional `namespace` path segments. The complete internal namespace is
never a query parameter. The caller application authorizes both identity and
business context before making the request; Mems uses them to constrain storage
queries.

> `id` (stable memory identifier) and `confidence` (trustworthiness component)
> are MRP target fields and are not returned yet.

### content and created_at Semantics by Source

| source | what `content` is | meaning of `created_at` |
| --- | --- | --- |
| `l0_hot` | memory text of the active session snapshot | none (not returned) |
| `l1_episodic` | original memory text (written `content` or folded messages) | record creation time |
| `l2_profile` | profile text (category/key/value joined) | `last_verified_at` |
| `l2_fact` | fact text (subject/predicate/object joined) | `last_verified_at` |
| `l2_event` | event text | event record time |
| `l2_summary` | summary content | summary creation time |

## Retrieval Behavior

### Current Recall Scope

```text
L0: active Redis snapshots → lexical overlap
L1: Qdrant vector recall over active (non-archived) L1 records
L2: Profile/Fact/Event go through SQL candidates + lexical/factor ranking;
    Summary goes through Qdrant vector recall
```

Behavior details:

- archived L1 (L3) is excluded from online recall by default; archive recall
  requires an explicit `time_range` (planned);
- vector search and L2 SQL candidates each have internal candidate caps; the
  final unified ranking truncates to `top_k`;
- L0 snapshots are stored per session; a recall sees each active session
  snapshot of the agent (returned only when there is lexical overlap);
- in multi-user scenarios, pass the intended `user_id` and applicable
  `tenant_id`; omitted values select a dedicated null partition, not a wildcard
  (see [Identity Model](/integration/identity-model)).

### score Composition

`score` is a ranking score — not a probability and not factual correctness:

| source | factors |
| --- | --- |
| `l0_hot` | lexical overlap |
| `l1_episodic` | vector similarity + lexical overlap + intent bonus + freshness |
| `l2_profile` / `l2_fact` | confidence + lexical overlap (multiplied by verification decay first) + intent bonus + freshness |
| `l2_event` | importance + lexical overlap + intent bonus + freshness |
| `l2_summary` | lexical overlap / vector similarity (multiplied by verification decay first) + intent bonus + freshness |

Each source base is normalized to 0-1 before intent/freshness bonuses are added,
so the final value can exceed 1. Weights are an internal implementation detail
and may change. See [MRP 4.3](/introduction/memory-recall-protocol).

## Errors

| Scenario | HTTP | Notes |
| --- | --- | --- |
| missing `agent_id` / `query` | `422` | FastAPI validation; response body is a `detail` array |
| `query` is blank | `422` | rejected after server-side trimming |
| `top_k` outside 1-100 | `422` | validation failed |
| internal service exception | `500` | `detail` carries the exception (embedding, database, and other hard dependencies) |

> If the vector service is unavailable, the recall service logs a warning and
> continues with empty vector results through SQL/lexical retrieval (L1 and L2
> Summaries are missing, but the request itself succeeds); embedding or
> database hard-dependency failures return 500.

## Notes

- Query returns candidates; context assembly, prompt construction, and final
  memory selection belong to the caller;
- use `source_l1_ids` to trace back to L1 originals when presenting evidence;
- the same content may appear both in L0 (hot snapshot) and L1 (master record);
  deduplicate as needed on your side;
- there is no built-in auth header today; enforce authentication at the gateway
  layer.
