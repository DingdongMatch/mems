"""RecallMemories application service."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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
        self._episodic = episodic
        self._knowledge = knowledge
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

    async def _search_vectors(
        self,
        command: QueryMemoryCommand,
        identity: MemoryIdentity,
        query_vector: list[float],
    ) -> list[dict[str, Any]]:
        if not query_vector:
            return []
        # Hydrate each page before deciding whether the candidate set is full.
        # This also tolerates orphan vectors from archives made before cleanup.
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        offset = 0
        page_size = command.top_k * 3
        scan_limit = max(1000, command.top_k * 30)
        summary_ids = {
            item.vector_id for item in self._knowledge.list_summaries_for_query(
                identity, max(SEARCH_MIN_CANDIDATE_LIMIT, command.top_k * 10)
            )
        }
        while offset < scan_limit:
            limit = min(page_size, scan_limit - offset)
            try:
                page = await self._vector.search(
                    collection_name=f"agent_{command.agent_id}",
                    query_vector=query_vector,
                    top_k=limit,
                    filter_agent_id=command.agent_id,
                    filters=identity.filter_fields(),
                    offset=offset,
                )
            except Exception as exc:
                logger.warning(
                    "Vector search unavailable; continuing with non-vector sources",
                    exc_info=True,
                    extra={
                        "event": "memory.vector_search.failed",
                        "error_type": type(exc).__name__,
                        "result": "degraded",
                    },
                )
                break
            keys = [
                str((item.get("payload") or {}).get("vector_id") or item.get("id"))
                for item in page
            ]
            live_ids = {
                item.vector_id for item in self._episodic.get_by_vector_ids(
                    command.agent_id, keys, identity
                )
            } | summary_ids
            for key, item in zip(keys, page):
                if key in live_ids and key not in seen:
                    results.append(item)
                    seen.add(key)
            if len(results) >= command.top_k * 3 or len(page) < limit:
                break
            offset += len(page)
            page_size *= 2
        return results

    async def recall(self, command: QueryMemoryCommand) -> RecallResult:
        identity = MemoryIdentity(
            tenant_id=command.tenant_id,
            user_id=command.user_id,
            agent_id=command.agent_id,
            namespace_segments=tuple(command.namespace),
        )
        query_intents = detect_query_intents(command.query)
        now = self._clock.now()

        query_vector: list[float] = []
        try:
            embeddings = await self._embedding.embed([command.query])
            if embeddings and embeddings[0]:
                query_vector = embeddings[0]
        except Exception as exc:
            logger.warning(
                "Embedding unavailable; continuing with non-vector sources",
                exc_info=True,
                extra={
                    "event": "memory.embedding.failed",
                    "operation": "memory_query",
                    "error_type": type(exc).__name__,
                    "result": "degraded",
                },
            )

        vector_results = await self._search_vectors(command, identity, query_vector)

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
