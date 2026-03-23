# Mems Project Review

Date: 2026-03-23

## Overall Assessment

The project direction is good: the L0/L1/L2/L3 split is clear and the repository already contains a runnable prototype. The main risks are not stylistic, but around data consistency, scheduler behavior, and a few implementation details that can directly cause errors or incorrect memory state.

## Strengths

- Clear separation between entrypoint, routers, services, models, and configuration.
- Centralized settings in `src/mems/config.py` make environment migration easier.
- The L3 `scripts/reader.py` design matches the long-term archive goal well.

## High Priority Findings

1. Undefined logger in distill error path
- `src/mems/services/distill.py:100`
- `logger.error(...)` is called without a module-level `logger`, so the exception handler can fail again while handling the original error.

2. Distillation can mark records as distilled even when extraction fails
- `src/mems/services/distill.py:72`
- `src/mems/services/distill.py:146`
- `.env.example:36`
- `.env.example:57`
- If no LLM is configured, or if extraction returns no triples, L1 records are still marked `is_distilled=True`. This can permanently skip future extraction.

3. Archive removes SQL rows but leaves vectors in Qdrant
- `src/mems/services/archive.py:77`
- `src/mems/routers/search.py:27`
- `src/mems/services/vector_service.py:103`
- Search reads directly from Qdrant payloads, so archived content can still appear in results.

4. L0 write and manual commit can duplicate L1 persistence
- `src/mems/routers/l0.py:35`
- `src/mems/routers/l0.py:100`
- `/l0/write` auto-syncs to L1 and `/l0/commit/{agent_id}/{session_id}` can sync the same L0 again.

5. Multi-store writes are not atomic
- `src/mems/routers/ingest.py:32`
- `src/mems/services/l0_sync.py:62`
- `src/mems/services/archive.py:62`
- Redis / SQL / Qdrant / JSONL updates can partially succeed, leaving inconsistent state.

## Medium Priority Findings

1. Async routes are doing blocking sync DB work
- `src/mems/routers/ingest.py:18`
- `src/mems/database.py:20`
- Sync SQLModel session calls are made inside `async def` handlers, which can block the event loop.

2. `force=True` does not actually force re-distillation
- `src/mems/services/distill.py:54`
- The query still always filters `is_distilled == False`.

3. L2 versioning logic skips versions
- `src/mems/services/distill.py:118`
- Existing records are incremented and the new record uses `existing.version + 1`, causing version jumps.

4. Broken DB dependency helper
- `src/mems/dependencies.py:8`
- `get_session()` is a normal generator, but `get_db()` calls `__anext__()`.

5. Duplicate schema definitions create ambiguity
- `src/mems/schemas.py:1`
- `src/mems/schemas/__init__.py:1`
- `mems.schemas` resolves to the package version, which makes the standalone file misleading.

## Low Priority Findings

1. Health check is only static
- `src/mems/main.py:59`
- `/health` does not verify DB, Redis, or Qdrant readiness.

2. README links use the wrong file casing
- `README.md:90`
- `README_zh.md:90`
- On case-sensitive systems these links can break.

3. L2 documentation is ahead of the implementation
- `README.md:12`
- `docs/TECHNICAL_en.md:114`
- `src/mems/services/distill.py:151`
- Docs imply L2 knowledge is exported as structured JSONL, but the code currently writes only distill summary metadata.

4. JSONL date iteration breaks at month boundaries
- `src/mems/services/jsonl_utils.py:65`
- `read_date_range()` increments date by manually adding to the day field.

5. Qdrant auth and timeout settings are unused
- `src/mems/config.py:19`
- `src/mems/services/vector_service.py:12`

## Suggested Fix Order

1. Fix data correctness first: distill marking, archive/vector cleanup, duplicate L0 persistence.
2. Fix deterministic bugs next: logger, dependency helper, versioning, JSONL date increment.
3. Add a minimal test suite around distill, archive, search consistency, and L0-to-L1 sync.
4. Then improve async boundaries, health checks, and documentation accuracy.
