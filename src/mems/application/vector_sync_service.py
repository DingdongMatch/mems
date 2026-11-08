"""Vector replica sync application service."""

from __future__ import annotations

from mems.domain.identity import MemoryIdentity, MemoryNamespace
from mems.domain.outbox import OutboxEvent
from mems.domain.ports import (
    Clock,
    EmbeddingPort,
    EpisodicRepository,
    KnowledgeRepository,
    UnitOfWork,
    VectorStorePort,
)


class VectorSyncApplicationService:
    """Apply one ``vector_sync`` outbox event to the vector store."""

    def __init__(
        self,
        episodic: EpisodicRepository,
        knowledge: KnowledgeRepository,
        vector: VectorStorePort,
        embedding: EmbeddingPort,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._episodic = episodic
        self._knowledge = knowledge
        self._vector = vector
        self._embedding = embedding
        self._clock = clock
        self._uow = uow

    async def sync(self, event: OutboxEvent) -> None:
        payload = event.payload
        kind = payload.get("kind")
        vector_id = payload["vector_id"]
        content = payload["content"]
        agent_id = payload["agent_id"]
        identity = MemoryIdentity(
            tenant_id=payload.get("tenant_id"),
            user_id=payload.get("user_id"),
            agent_id=agent_id,
            memory_namespace=(
                MemoryNamespace.from_key(payload["namespace"])
                if payload.get("namespace")
                else None
            ),
        )

        vectors = await self._embedding.embed([content])
        vector = vectors[0]
        collection_name = f"agent_{agent_id}"

        if kind == "episodic":
            records = self._episodic.get_by_vector_ids(
                agent_id, [vector_id], identity
            )
            if not records:
                return
            memory = records[0]
            try:
                await self._vector.upsert_episodic(collection_name, memory, vector)
            except Exception as exc:
                memory.mark_vector_failed(f"vector: {exc}", self._clock.now())
                self._episodic.save(memory)
                self._uow.commit()
                raise
            memory.mark_vector_ready(self._clock.now())
            self._episodic.save(memory)
        elif kind == "summary":
            summaries = self._knowledge.list_summaries(identity)
            summary = next(
                (item for item in summaries if item.vector_id == vector_id), None
            )
            if summary is None:
                return
            try:
                await self._vector.upsert_summary(collection_name, summary, vector)
            except Exception as exc:
                summary.vector_status = "failed"
                summary.last_sync_error = f"vector: {exc}"
                summary.last_sync_at = self._clock.now()
                self._knowledge.save_summary(summary)
                self._uow.commit()
                raise
            summary.vector_status = "ready"
            summary.last_sync_error = None
            summary.last_sync_at = self._clock.now()
            self._knowledge.save_summary(summary)
        else:
            raise ValueError(f"Unknown vector_sync kind: {kind}")

        self._uow.commit()
