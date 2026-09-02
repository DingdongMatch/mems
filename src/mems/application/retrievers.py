"""Per-source recall retrievers.

每个来源（L0 / L1 / L2 各类实体）一个检索器，返回统一的 ``ScoredMemory``
读模型；编排器并行调用后合并排序。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from mems.domain.identity import MemoryIdentity
from mems.domain.ports import (
    EpisodicRepository,
    KnowledgeRepository,
    WorkingMemoryStore,
)
from mems.domain.services.ranking import (
    ScoredMemory,
    intent_bonus,
    l1_base,
    l2_entity_base,
    l2_event_base,
    l2_summary_base,
    lexical_overlap,
    rank_score,
    rank_verified_score,
)


class Retriever(Protocol):
    """A single recall source that returns ranked ``ScoredMemory`` items."""

    async def retrieve(self, ctx: "RecallContext") -> list[ScoredMemory]: ...


@dataclass
class RecallContext:
    """Shared inputs for one recall run."""

    query: str
    identity: MemoryIdentity
    intents: set[str]
    now: datetime
    top_k: int
    candidate_limit: int
    vector_results: list[dict[str, Any]]
    summary_vector_scores: dict[str, float] = field(default_factory=dict)


class L0HotRetriever:
    """Retrieve active L0 hot-memory snapshots."""

    def __init__(self, store: WorkingMemoryStore, limit: int) -> None:
        self._store = store
        self._limit = limit

    async def retrieve(self, ctx: RecallContext) -> list[ScoredMemory]:
        snapshots = await self._store.list_active(
            ctx.identity.agent_id,
            tenant_id=ctx.identity.tenant_id,
            user_id=ctx.identity.user_id,
            scope=ctx.identity.scope,
            limit=self._limit,
        )
        results: list[ScoredMemory] = []
        for l0 in snapshots:
            if not l0.content:
                continue
            base = 0.7 * lexical_overlap(ctx.query, l0.content)
            if base > 0:
                results.append(
                    ScoredMemory(
                        source="l0_hot",
                        content=l0.content,
                        score=base + intent_bonus("l0_hot", ctx.intents),
                        session_id=l0.session_id,
                        tenant_id=l0.tenant_id,
                        user_id=l0.user_id,
                        scope=l0.scope,
                    )
                )
        return results


class L1EpisodicRetriever:
    """Retrieve L1 episodic records matching vector search results."""

    def __init__(self, episodic: EpisodicRepository) -> None:
        self._episodic = episodic

    async def retrieve(self, ctx: RecallContext) -> list[ScoredMemory]:
        vector_ids = []
        for result in ctx.vector_results:
            payload = result.get("payload") or {}
            vector_id = payload.get("vector_id") or result.get("id")
            if vector_id is not None:
                vector_ids.append(str(vector_id))

        records_by_vector_id = {}
        if vector_ids:
            records = self._episodic.get_by_vector_ids(
                ctx.identity.agent_id, vector_ids, ctx.identity
            )
            records_by_vector_id = {
                record.vector_id: record for record in records if record.vector_id
            }

        results: list[ScoredMemory] = []
        seen_l1_ids: set[int] = set()
        for result in ctx.vector_results:
            payload = result.get("payload") or {}
            vector_id = payload.get("vector_id") or result.get("id")
            if vector_id is None:
                continue
            record = records_by_vector_id.get(str(vector_id))
            if not record:
                continue
            record_id = record.id
            if record_id is None or record_id in seen_l1_ids:
                continue
            seen_l1_ids.add(record_id)
            results.append(
                ScoredMemory(
                    source="l1_episodic",
                    content=record.content,
                    score=rank_score(
                        l1_base(
                            result["score"],
                            lexical_overlap(ctx.query, record.content),
                        ),
                        "l1_episodic",
                        record.created_at,
                        ctx.intents,
                        ctx.now,
                    ),
                    source_l1_ids=[record_id],
                    session_id=record.session_id,
                    created_at=record.created_at,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    scope=record.scope,
                )
            )
        return results


class L2ProfileRetriever:
    def __init__(self, knowledge: KnowledgeRepository) -> None:
        self._knowledge = knowledge

    async def retrieve(self, ctx: RecallContext) -> list[ScoredMemory]:
        results: list[ScoredMemory] = []
        for profile in self._knowledge.list_profiles_for_query(
            ctx.identity, ctx.candidate_limit
        ):
            text = f"{profile.category} {profile.key} {profile.value}"
            results.append(
                ScoredMemory(
                    source="l2_profile",
                    content=text,
                    score=rank_verified_score(
                        l2_entity_base(
                            profile.confidence, lexical_overlap(ctx.query, text)
                        ),
                        "l2_profile",
                        profile.last_verified_at,
                        profile.last_verified_at,
                        ctx.intents,
                        ctx.now,
                    ),
                    source_l1_ids=profile.source_l1_ids,
                    created_at=profile.last_verified_at,
                    tenant_id=profile.tenant_id,
                    user_id=profile.user_id,
                    scope=profile.scope,
                )
            )
        return results


class L2FactRetriever:
    def __init__(self, knowledge: KnowledgeRepository) -> None:
        self._knowledge = knowledge

    async def retrieve(self, ctx: RecallContext) -> list[ScoredMemory]:
        results: list[ScoredMemory] = []
        for fact in self._knowledge.list_facts_for_query(
            ctx.identity, ctx.candidate_limit
        ):
            text = f"{fact.subject} {fact.predicate} {fact.object}"
            results.append(
                ScoredMemory(
                    source="l2_fact",
                    content=text,
                    score=rank_verified_score(
                        l2_entity_base(
                            fact.confidence, lexical_overlap(ctx.query, text)
                        ),
                        "l2_fact",
                        fact.last_verified_at,
                        fact.last_verified_at,
                        ctx.intents,
                        ctx.now,
                    ),
                    source_l1_ids=fact.source_l1_ids,
                    created_at=fact.last_verified_at,
                    tenant_id=fact.tenant_id,
                    user_id=fact.user_id,
                    scope=fact.scope,
                )
            )
        return results


class L2EventRetriever:
    def __init__(self, knowledge: KnowledgeRepository) -> None:
        self._knowledge = knowledge

    async def retrieve(self, ctx: RecallContext) -> list[ScoredMemory]:
        results: list[ScoredMemory] = []
        for event in self._knowledge.list_events_for_query(
            ctx.identity, ctx.candidate_limit
        ):
            text = f"{event.subject} {event.action} {event.object}"
            results.append(
                ScoredMemory(
                    source="l2_event",
                    content=text,
                    score=rank_score(
                        l2_event_base(
                            event.importance_score,
                            lexical_overlap(ctx.query, text),
                        ),
                        "l2_event",
                        event.created_at,
                        ctx.intents,
                        ctx.now,
                    ),
                    source_l1_ids=event.source_l1_ids,
                    created_at=event.created_at,
                    tenant_id=event.tenant_id,
                    user_id=event.user_id,
                    scope=event.scope,
                )
            )
        return results


class L2SummaryRetriever:
    def __init__(self, knowledge: KnowledgeRepository) -> None:
        self._knowledge = knowledge

    async def retrieve(self, ctx: RecallContext) -> list[ScoredMemory]:
        results: list[ScoredMemory] = []
        for summary in self._knowledge.list_summaries_for_query(
            ctx.identity, ctx.candidate_limit
        ):
            summary_vector_score = 0.0
            if summary.vector_id and summary.vector_id in ctx.summary_vector_scores:
                summary_vector_score = ctx.summary_vector_scores[summary.vector_id]
            results.append(
                ScoredMemory(
                    source="l2_summary",
                    content=summary.content,
                    score=rank_verified_score(
                        l2_summary_base(
                            lexical_overlap(ctx.query, summary.content),
                            summary_vector_score,
                        ),
                        "l2_summary",
                        summary.created_at,
                        summary.last_verified_at,
                        ctx.intents,
                        ctx.now,
                    ),
                    source_l1_ids=summary.source_l1_ids,
                    created_at=summary.created_at,
                    tenant_id=summary.tenant_id,
                    user_id=summary.user_id,
                    scope=summary.scope,
                )
            )
        return results
