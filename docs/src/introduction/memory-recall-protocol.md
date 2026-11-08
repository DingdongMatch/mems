# Memory Recall Protocol (MRP)

> **Memory Recall Protocol (MRP) defines the standard protocol between a caller and Mems: how to request memories, what Mems returns, what semantics memories carry, and how relevance, trustworthiness, and evidence are expressed.**

> MRP is a **protocol specification**, not documentation for one concrete API. HTTP APIs, SDKs, and other transports are only implementations of MRP.

Every protocol point is annotated with its implementation status:

- `(Implemented)`: consistent with the current code
- `(Planned)`: part of the MRP target contract, not yet exposed by the current API
- `(Explicit Non-Goal)`: intentionally excluded from MRP recall semantics
- `(To Be Decided)`: not finalized yet

The current HTTP transport implementation is `POST /v1/mems/query`. For the HTTP
field reference, see [Query API](/api/query); for identity and namespace
semantics, see [Identity Model](/integration/identity-model).

## 1. Positioning and Boundaries

### 1.1 Protocol, Not API

The API answers "how to call":

```http
POST /v1/mems/query
```

The protocol answers "what the call means semantically":

```text
What does query mean?
What does top_k mean?
What does score mean?
Why does source_l1_ids exist?
What is the difference between confidence and score?
Why is session_id not a Recall Filter?
```

MRP is the semantic contract; the API is only one transport for it. In the
future, agents in any language or runtime can access Mems through the same
protocol:

```text
Python Agent
Java Agent
Go Agent
LangGraph Agent
Claude Agent
Custom Agent Runtime
       │
       ▼
      MRP
       │
       ▼
     Mems
```

### 1.2 Memories, Not Context; Candidates, Not Decisions

Mems provides **Memory Candidates** relevant to the current request, together
with relevance, confidence, and evidence signals. Mems does not decide whether
those candidates finally enter the LLM context:

```text
Mems
  │
  │ Memory Candidates + Evidence + Confidence + Relevance
  ▼
Agent Runtime
  │
  │ decides
  ▼
Context
```

Therefore:

> **Mems may recommend memories, but never owns the final decision about whether
> those memories enter the Agent context.**

(Implemented: the current `/v1/mems/query` returns candidates in a unified
result model; assembling them into context is the caller's job.)

### 1.3 Runtime State and Context Belong to the Caller

The Agent Runtime owns runtime state. The following belong to the caller, not
to Mems:

```text
current_task
current_goal
current_plan
current_step
messages
tool_results
temporary_variables
```

Therefore a Recall Request must not accept full runtime state; the following
fields must not appear:

```json
{
  "state": "...",
  "messages": "...",
  "system_prompt": "...",
  "current_plan": "...",
  "tools": "..."
}
```

Responsibility boundary:

| Mems owns | Caller owns |
| --- | --- |
| Memory Storage / Recall | Agent Runtime State |
| Memory Ranking | Context Construction |
| Evidence / Provenance | Memory Selection |
| Recall Constraints (e.g., `top_k`) | Prompt Construction |
| Memory Lifecycle | Context Window / Token Budget |
| Recommending candidate memories | Agent Decision |

(Implemented: the request schema contains identity, `namespace`, `query`, and
`top_k`; the server derives the internal namespace from identity and path
segments. See [Identity and Namespace](/integration/identity-model).)

### 1.4 Agent-Agnostic and Memory Autonomy

Mems does not depend on a specific Agent Framework, and MRP must not require the
caller to adopt a particular Agent State, Prompt Format, Planning Model, Tool
Model, or Framework.

Mems manages the **Memory Lifecycle**, not the **Agent Decision Lifecycle**:

- Mems can provide `relevance`, `score`, `confidence`, `evidence`, and time
  information;
- Mems never tells the Agent "you must use this memory";
- the final decision always belongs to the caller.

### 1.5 Time-Forward

> **Memory evolves forward in time; historical records are not rewritten.**

Memories that have already happened are not down-weighted, rewritten, withdrawn,
or deleted because of later usage. New facts, verification results, or conflicts
form new memories at a later point in time. Long-term knowledge evolves forward
through `supersedes` + conflict log + version chain, and the original records
always remain.

(Implemented: when reconciliation detects a conflict, the old Profile/Fact is
marked `superseded` and retained; the new record uses `version + 1` and points to
the old record with `supersedes_id`.)

### 1.6 Summary of Design Principles

The core positioning of MRP can be summarized in four statements:

> **Mems provides memory, not context.**

> **Mems provides evidence, not truth.**

> **Mems provides candidates, not decisions.**

> **Mems owns memory lifecycle; Agent owns runtime lifecycle.**

## 2. Core Terminology

| Term | Definition |
| --- | --- |
| Mems | Memory Infrastructure; stores, recalls, and manages the Memory lifecycle |
| Agent Runtime | The agent execution environment calling Mems |
| Recall | Retrieving relevant Memory from Mems for a request |
| Recall Request | A memory request issued by the Agent Runtime (see Chapter 3) |
| Recall Response | The set of Memory Candidates returned by Mems (see Chapter 4) |
| Memory Candidate | One memory result that Mems considers relevant to the request |
| Relevance | How relevant a Memory is to the current Query, reflected in `score` |
| Score | The composite ranking score within one recall; composition varies by source (see 4.3) |
| Confidence | Mems' judgment of how trustworthy the Memory content is (see 5.2) |
| Evidence | References to the historical records backing a Memory (e.g., `source_l1_ids`) |
| Provenance | The source and evolution chain of a Memory (L2 → L1 → L3 pointers) |
| Identity | The hard isolation boundary (`tenant_id` / `user_id` / `agent_id`) |
| Namespace | Immutable storage partition generated by Mems from validated identity |
| L0 / L1 / L2 / L3 | Hot / Episodic / Distilled Knowledge / Archive |

## 3. Recall Request

### 3.1 Current HTTP Shape and Protocol Target Shape

The current HTTP request only contains the implemented fields:

```json
{
  "query": "Design a cache plan for the order system",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "top_k": 10
}
```

The MRP target shape adds `memory_types` and `time_range`:

```json
{
  "query": "Design a cache plan for the order system",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "memory_types": ["l1_episodic", "l2_fact", "l2_summary"],
  "top_k": 10,
  "time_range": null
}
```

Where:

- `memory_types`: **(Planned)**; by default, recall spans L0/L1/L2;
- `time_range`: **(Planned)**; only used to trigger explicit L3 archive recall;
- `session_id`: never accepted in the request (see 3.4).

### 3.2 Field Reference

| Field | Semantics | Status |
| --- | --- | --- |
| `tenant_id` / `user_id` / `agent_id` | hard identity boundaries (flat fields, see 3.3) | Implemented |
| `namespace` | optional generic path segments for caller business context | Implemented; defaults to the identity's exact default partition |
| `MemoryNamespace` | immutable server-generated storage partition derived from validated identity plus `namespace` | Internal; full value is not a request field |
| `query` | search text; required and must not be blank | Implemented |
| `top_k` | maximum number of candidates returned (default 5, range 1-100) | Implemented |
| `memory_types` | optional source filter; defaults to recall across L0/L1/L2 | Planned |
| `time_range` | optional time filter; only for explicit L3 archive recall | Planned |
| `session_id` | not a recall filter field (write-side provenance) | Explicit Non-Goal |

`query` drives semantic / lexical retrieval. The server performs keyword-based
intent inference on the Query and uses the result as an internal ranking bonus
(`intent_bonus`, **implemented**); this mechanism is not exposed as a request
field.

### 3.3 Identity and Namespace

`tenant_id` / `user_id` / `agent_id` are the hard identity boundary of Memory:

```text
Identity (tenant_id / user_id / agent_id)
    ↓
Hard Isolation
```

The API accepts optional `namespace` path segments (a `string[]`) and
derives an immutable, versioned internal namespace from them and the validated
identity:

```text
 ("v1", "tenant", <tenant_id>, "user", <user_id>, "agent", <agent_id>,
  "memories", ...<namespace>)
```

For example, `namespace: ["projects", "prj_abc123"]` can select a project
partition. Segments are opaque to Mems; use stable identifiers. Omitted or empty
segments select the identity's default partition, with no implicit descendant
search. Write and query must use the same path. The complete internal namespace
is never client-set.
The namespace is a storage partition key, not an authorization grant. The
caller application authenticates the caller and authorizes both identity and
business context. Shared memory must also use an identity representing its
shared owner. Memory type and retrieval attributes remain record fields so
recall can span types within the selected partition.

Current filtering semantics:

- `agent_id` is required and every recall filters by agent;
- the exact `MemoryNamespace` includes `tenant_id` and `user_id`; an omitted
  optional identity value selects its dedicated null partition, not a wildcard;
- multi-user deployments should pass the intended `user_id` (and `tenant_id`
  when needed) to address the correct identity partition;
- Mems does not assume a business `Tenant → User → Agent` hierarchy; the
  namespace is a canonical storage path derived from flat identity fields and
  optional caller-supplied context. Authorization remains with the caller app.

### 3.4 The Boundary of session_id

`session_id` is write-side action provenance:

- `POST /v1/mems/write` accepts `session_id`; L0 snapshots are stored per
  session, and the L1 master record persists the field;
- Recall Requests do not accept `session_id`; it is neither a recall filter nor
  an isolation boundary;
- In Recall Responses, L0/L1 results may carry `session_id`; L2 distilled
  knowledge does not depend on `session_id` (L2 traces back to the original L1
  through `source_l1_ids`).

(Explicit Non-Goal)

### 3.5 Request Non-Goals

The following do not belong to a Recall Request.

**Runtime State**

```text
state
messages
system_prompt
current_plan
tools
```

(Explicit Non-Goal; see 1.3)

**Explicit Intent**

```json
{
  "intent": "architecture_design"
}
```

Query Intent is an internal Mems Retrieval/Ranking mechanism (keyword inference
plus `intent_bonus`), not a protocol field callers must understand.

(Explicit Non-Goal)

**Context Token Budget**

```text
max_tokens
context_budget
prompt_budget
```

The boundary:

```text
top_k       → Memory Retrieval Constraint → Mems
max_tokens  → Generation / Context Budget  → Agent Runtime
```

(Explicit Non-Goal)

**session_id as a Recall Filter**

See 3.4.

(Explicit Non-Goal)

## 4. Recall Response

### 4.1 Target Shape

The MRP target response shape:

```json
{
  "query": "What language does the user mainly use?",
  "results": [
    {
      "id": "mem-001",
      "source": "l2_profile",
      "content": "like language Java",
      "score": 0.94,
      "confidence": 0.91,
      "source_l1_ids": [102, 231],
      "created_at": "2026-04-01T10:00:00Z",
      "tenant_id": "tenant-001",
      "user_id": "user-001"
    }
  ],
  "total": 1
}
```

The `id` and `confidence` fields are not yet returned (see 4.2). The current
`/v1/mems/query` response has top-level `query` / `results` / `total`; each
`results` item contains `source`, `content`, `score`, `created_at` (nullable),
`source_l1_ids`, `session_id` (nullable), and identity-context fields.

### 4.2 Field Reference

| Field | Semantics | Status |
| --- | --- | --- |
| `query` | echo of the search text | Implemented |
| `results` | list of Memory Candidates | Implemented |
| `id` | stable Memory Identifier (dedup / reference / version association) | Planned |
| `source` | origin type (below) | Implemented |
| `content` | readable memory text | Implemented |
| `score` | composite ranking score within this recall (see 4.3) | Implemented |
| `confidence` | trustworthiness component (Profiles/Facts currently rank with it but do not return it separately; other sources do not have this component) | Planned |
| `source_l1_ids` | evidence chain (see 5.1) | Implemented |
| `created_at` | time context; semantics vary by source (see 5.3) | Implemented |
| `session_id` | source session (only L0/L1 carry a value) | Implemented |
| `tenant_id` / `user_id` | identity context | Implemented |
| `total` | number of `results` actually returned in this response | Implemented |

`source` values map one-to-one with request-side `memory_types`; no extra
business vocabulary is introduced:

```text
l0_hot
l1_episodic
l2_profile
l2_fact
l2_event
l2_summary
```

`total` semantics:

```text
total = len(results) <= top_k
```

`total` is not the candidate-pool size, the database match count, or the global
Memory count.

### 4.3 Score Composition (Ranking Factors)

`score` is the composite ranking score Mems uses for Recall Ranking:

> **The ranking value of this Memory under the current Recall Request.**

`score` is ranking semantics, not factual correctness and not a probability.

Ranking factors currently participating per source:

| `source` | Factors |
| --- | --- |
| `l0_hot` | lexical overlap (against the L0 snapshot text) |
| `l1_episodic` | vector similarity + lexical overlap + intent bonus + freshness |
| `l2_profile` / `l2_fact` | confidence + lexical overlap (multiplied by verification decay first) + intent bonus + freshness |
| `l2_event` | importance + lexical overlap + intent bonus + freshness |
| `l2_summary` | lexical overlap / vector similarity (multiplied by verification decay first) + intent bonus + freshness |

Current implementation notes (weights are internal details and **not part of the
protocol contract**; they may change at any time):

- lexical scoring uses CJK bigrams plus ASCII whole words;
- each source base score is first normalized to 0-1, then intent and freshness
  bonuses are added, so the final `score` can exceed 1;
- L0: `base = 0.7 × lexical_overlap`, returned only when `base > 0`; in the
  current implementation L0 has no intent bonus, no freshness bonus, and no
  `created_at`;
- L1: `base = 0.6 × clip(vector) + 0.4 × lexical`, then intent and freshness
  bonuses;
- L2 Profile / Fact: `base = 0.7 × clip(confidence) + 0.3 × lexical`, multiplied
  by `verification_decay`, then intent and freshness bonuses;
- L2 Event: `base = 0.6 × clip(importance / 10) + 0.4 × lexical`;
- L2 Summary: `base = max(lexical, 0.6 × clip(vector))`, multiplied by
  verification decay, then intent and freshness bonuses;
- equal scores are ordered by `created_at` descending (deterministic ordering);
- `score` is only comparable within the same Recall; there is no absolute
  cross-request or cross-implementation meaning.

### 4.4 No reason Field (Explicit Non-Goal)

"Why was this recalled?" is determined by the explainable factors above; the
factor combination is an internal Mems implementation. Mems does not use an LLM
to generate a `reason` for each result — the cost, latency, and uncertainty are
not worthwhile, and the internal factors already explain the ranking without
committing to a per-recall response contract.

(Explicit Non-Goal)

## 5. Semantics: Evidence, Confidence, and Time

### 5.1 Evidence Is a First-Class Concept

Evidence is a first-class concept in MRP, especially for L2 Distilled Knowledge:

```text
L2 Knowledge
      │
      ▼
   Evidence (source_l1_ids)
      │
      ▼
 L1 Narrative
      │
      ▼
Original Experience
```

Evidence answers "where does this Memory come from"; it does not mean "this
Memory is definitely correct":

```text
Evidence   → Where did this come from?
Confidence → How trustworthy is this?
```

Recall returns only Evidence References (`source_l1_ids`) by default; callers
can request the original L1 text on demand:

```text
Recall L2
   │
   ├── Need Evidence? No  → Use L2 directly
   └── Need Evidence? Yes → Expand L1 (on demand)
```

Implementation status:

- `source_l1_ids`: **(Implemented)**;
- `l3_path` / `l3_line` fallback evidence pointers in the re-distillation
  progress table: **(Implemented)**;
- Evidence Expansion API (on-demand L1 retrieval): **(Planned)**.

Note: `l3_path` / `l3_line` are provenance pointers on the re-distillation
(L3 → L2) side; they are not returned through the current Recall Response.

### 5.2 Confidence and Verification Decay

`confidence` expresses Mems' judgment of how trustworthy the Memory content is,
and is not equivalent to `score`. For example:

```text
Memory A: score = 0.95, confidence = 0.90
→ highly relevant to the Query, and the content itself is fairly trustworthy

Memory B: score = 0.92, confidence = 0.40
→ relevant to the Query, but the Memory itself is less trustworthy
```

Current implementation:

- L2 Profiles/Facts carry `confidence` (0-1) and `last_verified_at`, and both
  participate in ranking;
- L2 Summaries have no separate `confidence` field, but ranking applies
  verification decay using `last_verified_at` in the same way;
- L2 Events do not carry `confidence`; they rank by importance + lexical
  overlap;
- returning `confidence` as a separate response field: **(Planned)** — it is
  currently folded into `score`.

Verification Decay semantics:

```text
not re-verified for a long time
    ↓
the base score that confidence participates in is multiplied by decay
    ↓
trust in Recall decreases
```

(Implemented)

### 5.3 Temporal Context

Different Memory Sources have different time semantics; the time field in a
response must be interpreted together with `source`:

| Source | Meaning of time | Response `created_at` | Verification-decay basis |
| --- | --- | --- | --- |
| L0 | no fixed creation time; only `expires_at` | absent | none |
| L1 | creation / record time | `created_at` | none |
| L2 Event | event record time | `created_at` | none |
| L2 Summary | creation time; last-verified time is stored separately | `created_at` | `last_verified_at` |
| L2 Profile / Fact | last-verified time | `last_verified_at` (returned in the `created_at` field) | `last_verified_at` |

### 5.4 Conflict and the Version Chain

Conflicts mainly occur during distillation / re-distillation reconciliation:

```text
Old L2: user prefers Redis
      ↓
New Evidence
      ↓
New L2: user now uses PostgreSQL
```

Reconciliation results are written to `mems_l2_conflict_log`, including:

```text
memory_type
old_value
new_value
resolution
reason
source_l1_ids
```

(Implemented)

Knowledge also evolves forward through the version chain:

```text
old record status → superseded (retained)
new record version = old version + 1
new record supersedes_id → old record id
```

(Implemented)

At Recall time:

- Mems performs no real-time Conflict Reasoning;
- the response does not return `conflict` / `conflict_reason` /
  `resolution` — conflicts are already resolved during distillation /
  re-distillation; inference at recall time would only add cost, latency, and
  uncertainty.

(Explicit Non-Goal)

### 5.5 Feedback (Explicit Non-Goal)

Mems does not modify memories that have already happened based on usage
feedback:

```text
Recall → Memory A → Agent ignored A
does not cause A.score to drop, or A to be rewritten or deleted
```

The reason is the Time-Forward principle (see 1.5): Memory is historical
record. If a new fact arises later, a new Memory should be created at that later
point in time and evolve through the version chain. Recall Feedback is therefore
not a Memory Rewrite / Reweight mechanism.

Whether Feedback can be used for Retrieval Evaluation, Benchmarks, or Offline
Ranking Optimization in the future is a separate discussion; it is not part of
the current MRP Memory Lifecycle.

(Explicit Non-Goal)

## 6. Retrieval Semantics and Pipeline

### 6.1 Recall Is Not Vector Search

Vector Search answers "which vectors are similar"; Memory Recall answers "which
Memories have value for the current request". Therefore:

```text
Memory Recall
      │
      ├── Vector Retrieval
      ├── Lexical Retrieval
      ├── Temporal Filtering
      ├── Confidence / Verification Decay
      ├── Intent Bonus
      ├── Source-specific Ranking
      └── Evidence
```

Vector Search is only one underlying capability of Recall.

### 6.2 Current Retrieval Pipeline

```text
Query
  │
  ▼
Query Understanding (keyword intent inference, internal mechanism, implemented)
  │
  ▼
Memory Retrieval
  │
  ├── L0 Hot: Redis active snapshots → lexical overlap (implemented)
  │
  ├── L1 Episodic: Qdrant vector recall over active records (implemented)
  │
  ├── L2
  │      ├── Profile SQL candidates (implemented)
  │      ├── Fact SQL candidates (implemented)
  │      ├── Event SQL candidates (implemented)
  │      └── Summary Qdrant vector recall (implemented)
  │
  └── L3: explicit time_range only (planned)
  │
  ▼
Ranking (per-source factor combination → Top K, internal)
  │
  ▼
Evidence Packaging (source_l1_ids)
  │
  ▼
Recall Result
```

Notes:

- online recall covers L0 hot cache, active (non-archived) L1, and L2; archived
  L1 is excluded by default;
- L2 structured candidates only go through SQL candidate limiting; final
  relevance is decided by the unified ranking;
- L0 only returns active snapshots that have lexical overlap with the Query and
  does not carry `created_at`.

### 6.3 L3 and time_range (Planned)

L3 Archive Recall is explicit behavior:

- L3 archives are scanned only when the request carries `time_range`;
- there is **no automatic fallback** to L3 when online recall is insufficient,
  to avoid adding archive-scan latency to every recall.

(Planned)

What is already implemented on the L3 side is archive writing and manual
re-distillation (`POST /v1/mems/redistill`). These belong to the Memory
Lifecycle, not online Recall, and the two should not be confused.

### 6.4 Ranking Is an Internal Implementation

Ranking is an internal Mems implementation; concrete weights are not part of the
MRP Contract:

```text
Candidate Retrieval
        ↓
Source-specific Ranking
        ↓
Score
        ↓
Top K
```

Mems can keep optimizing Ranking without changing the protocol. The protocol
only requires that `score` provides a meaningful ordering, descending, within
one Recall.

## 7. Error Handling and Protocol Versioning

### 7.1 Error Semantics

MRP should provide stable error semantics. Suggested error shape:

```json
{
  "error": {
    "code": "INVALID_IDENTITY",
    "message": "Invalid memory identity"
  }
}
```

Suggested error codes:

```text
INVALID_REQUEST
INVALID_IDENTITY
INVALID_MEMORY_TYPE
MEMORY_ACCESS_DENIED
RECALL_TIMEOUT
UNSUPPORTED_VERSION
INTERNAL_ERROR
```

HTTP status codes belong to the API Transport Layer, not to MRP core semantics.
The current HTTP implementation uses regular validation errors (422) and
exceptions (500); the structured error-code envelope is not finalized:

**(To Be Decided)**

### 7.2 Versioning

MRP itself uses semantic versioning:

```text
MAJOR.MINOR.PATCH
```

For example: `0.1.0`

- **MAJOR**: breaking change;
- **MINOR**: backward-compatible feature;
- **PATCH**: bug fix / specification clarification.

(Planned: the current HTTP API has no `protocol_version` field.)

### 7.3 Compatibility

In the target shape, callers declare the MRP version they support in the
request, and Mems returns the version it used in the response:

```json
{
  "protocol_version": "0.1"
}
```

The Protocol Version and API Version can evolve independently:

```text
MRP v0.1
   │
   ├── HTTP API v1
   ├── Python SDK
   ├── Java SDK
   └── Go SDK
```

(Planned)

## 8. Examples

### 8.1 User-Preference Recall

Request (currently implemented fields):

```json
{
  "query": "What language does the user mainly use?",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "top_k": 5
}
```

Response (example of the current field shape; optional fields with null values
are omitted; `id` / `confidence` are not yet returned):

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
    }
  ],
  "total": 1
}
```

Mems returns candidates; the Agent decides whether to add these memories to its
context.

### 8.2 Evidence-Backed Knowledge

```text
L2 Profile: user prefers Java

        │
        ▼

Evidence: source_l1_ids = [102, 231]

        │
        ▼

L1 original experiences
```

If the caller needs to verify, it can expand the Evidence further (Expansion
API, planned).

### 8.3 Historical Recall (L3, Planned)

```json
{
  "query": "What order-system-related experiences happened in H1 2026?",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_id": "agent-001",
  "time_range": {
    "from": "2026-01-01",
    "to": "2026-06-30"
  }
}
```

This request explicitly triggers L3 Archive Recall; there is no automatic
fallback to L3 when online recall is insufficient.

**(Planned)**

## 9. Conformance

An implementation that declares itself **MRP Compatible** must satisfy at least:

### Request

- support `query`;
- support Identity fields and filter by the requested identity boundary;
- use the server-generated namespace derived from the authorized identity;
- honor the Runtime State Boundary (no runtime state accepted);
- honor `top_k` semantics (a limit on returned candidates, not a token budget).

### Response

- return Memory Candidates;
- return `source` and `content`;
- return `score`;
- provide an Evidence Reference (`source_l1_ids`) when the Memory has evidence;
- honor Identity Isolation;
- `total` is the actual number of returned items and does not exceed `top_k`.

### Semantics

- never make the final Memory Selection for the Agent;
- never modify historical Memory (Time-Forward);
- never treat Runtime State as a required part of Memory Recall;
- never move Context Construction into Mems' responsibility;
- no real-time Conflict Reasoning or Conflict Marker at Recall time;
- no LLM-generated `reason` in the response.

## 10. Implementation Status Summary

| MRP Capability | Current Implementation | Status |
| --- | --- | --- |
| Layered L0/L1/L2 recall | `/v1/mems/query` | Implemented |
| L3 recall | online recall excludes archived L1 | Planned (explicit `time_range` only) |
| Query Intent Inference | keyword inference + `intent_bonus` (internal) | Implemented |
| `memory_types` request field | none | Planned |
| Evidence chain | `QueryResultItem.source_l1_ids` | Implemented |
| L3 evidence pointer | `l3_path` / `l3_line` in the re-distillation progress table | Implemented |
| Confidence (storage and ranking) | Profiles/Facts `confidence` + `verification_decay`; Summaries also decay | Implemented |
| Separate `confidence` response field | none (folded into `score`) | Planned |
| Stable Memory `id` | none | Planned |
| Conflict Log | `mems_l2_conflict_log` (distillation / re-distillation reconciliation) | Implemented |
| Recall Conflict Marker | none | Explicit Non-Goal |
| LLM-generated `reason` | none | Explicit Non-Goal |
| Feedback-driven Reweighting | none (Time-Forward) | Explicit Non-Goal |
| Runtime State fields | none | Explicit Non-Goal |
| Explicit `intent` field | none | Explicit Non-Goal |
| Budget fields such as `max_tokens` | none | Explicit Non-Goal |
| `session_id` as a Recall Filter | absent from the Query schema; responses carry it on L0/L1 only | Explicit Non-Goal |
| Automatic L3 fallback | none | Explicit Non-Goal |
| `created_at` semantics | Profiles/Facts use `last_verified_at`; other sources use creation/record time | Implemented |
| `total` semantics | actual returned count (≤ `top_k`) | Implemented |
| Structured error-code envelope | none | To Be Decided |
| `protocol_version` | none | Planned |
| Evidence Expansion API | none | Planned |

## 11. Roadmap

### P0 — Protocol Completeness

- Request: `memory_types` source filter, `time_range` (explicit L3 recall);
- Response: stable `id`, separate `confidence` component (Profiles/Facts).

### P1 — Evidence Capability

- Evidence Expansion API (on-demand L1 retrieval);
- improve L3 → L1 / L3 → L2 provenance.

### P2 — Retrieval Evolution

- land explicit L3 recall;
- richer temporal retrieval;
- hybrid retrieval and ranking evaluation.

### To Be Decided

- structured error-code envelope (see 7.1);
- `protocol_version` negotiation (see 7.2).

### Future — Separate Future Protocols

The following capabilities are not part of MRP for now; they should be
considered only after separate protocols mature:

```text
Memory Feedback Protocol
Memory Update Protocol
Memory Consolidation Protocol
Memory Forgetting Protocol
Cross-Agent Memory Protocol
Memory Subscription / Streaming
```

> Maintenance note: the "current implementation" statements in this document
> follow the `src/mems/` code. When public APIs or ranking behavior change,
> update this document together with the [Query API](/api/query) and
> [Identity Model](/integration/identity-model).
