"""RecallMemories application service."""

from __future__ import annotations

import asyncio
import logging

from mems.application.commands import QueryMemoryCommand, RecallResult
from mems.application.retrievers import (
    L0HotRetriever,
    L1EpisodicRetriever,
    L2EventRetriever,
    L2FactRetriever,
    L2ProfileRetriever,
    L2SummaryRetriever,
    RecallContext,
    Retriever,
)
from mems.domain.identity import MemoryIdentity
from mems.domain.ports import (
    Clock,
    EmbeddingPort,
    EpisodicRepository,
    KnowledgeRepository,
    VectorStorePort,
    WorkingMemoryStore,
)
from mems.domain.services.ranking import detect_query_intents, sort_results


logger = logging.getLogger(__name__)

SEARCH_CANDIDATE_LIMIT_MULTIPLIER = 10
SEARCH_MIN_CANDIDATE_LIMIT = 50


class QueryMemoryService:
    """Hybrid recall across L0, L1, and L2."""

    def __init__(
        self,
        store: WorkingMemoryStore,
        episodic: EpisodicRepository,
        knowledge: KnowledgeRepository,
        vector: VectorStorePort,
        embedding: EmbeddingPort,
        clock: Clock,
        l0_recall_max_snapshots: int,
    ) -> None:
        self._embedding = embedding
        self._vector = vector
        self._clock = clock
        self._retrievers: list[Retriever] = [
            L0HotRetriever(store, l0_recall_max_snapshots),
            L1EpisodicRetriever(episodic),
            L2ProfileRetriever(knowledge),
            L2FactRetriever(knowledge),
            L2EventRetriever(knowledge),
            L2SummaryRetriever(knowledge),
        ]

    async def recall(self, command: QueryMemoryCommand) -> RecallResult:
        identity = MemoryIdentity(
            tenant_id=command.tenant_id,
            user_id=command.user_id,
            agent_id=command.agent_id,
            namespace_segments=tuple(command.namespace),
        )
        query_intents = detect_query_intents(command.query)
        now = self._clock.now()

        embeddings = await self._embedding.embed([command.query])
        query_vector = embeddings[0]

        try:
            vector_results = await self._vector.search(
                collection_name=f"agent_{command.agent_id}",
                query_vector=query_vector,
                top_k=command.top_k * 3,
                filter_agent_id=command.agent_id,
                filters=identity.filter_fields(),
            )
        except Exception as exc:
            logger.warning(
                "Vector search unavailable; continuing with non-vector sources",
                exc_info=True,
                extra={
                    "event": "memory.vector_search.failed",
                    "operation": "memory_query",
                    "error_type": type(exc).__name__,
                    "result": "degraded",
                },
            )
            vector_results = []

        summary_vector_scores: dict[str, float] = {}
        for result in vector_results:
            payload = result.get("payload") or {}
            if payload.get("memory_type") != "l2_summary":
                continue
            key = payload.get("vector_id") or result.get("id")
            if key is not None:
                summary_vector_scores[str(key)] = float(result["score"])

        candidate_limit = max(
            command.top_k * SEARCH_CANDIDATE_LIMIT_MULTIPLIER,
            SEARCH_MIN_CANDIDATE_LIMIT,
        )

        ctx = RecallContext(
            query=command.query,
            identity=identity,
            intents=query_intents,
            now=now,
            top_k=command.top_k,
            candidate_limit=candidate_limit,
            vector_results=vector_results,
            summary_vector_scores=summary_vector_scores,
        )

        groups = await asyncio.gather(
            *(retriever.retrieve(ctx) for retriever in self._retrievers)
        )
        results = [item for group in groups for item in group]
        results = sort_results(results)[: command.top_k]
        return RecallResult(query=command.query, items=results, total=len(results))
