"""Qdrant vector store adapter."""

from __future__ import annotations

import logging
from typing import Any, Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from mems.config import settings
from mems.domain.knowledge import Summary
from mems.domain.memory import EpisodicMemory
from mems.infrastructure.external.vector_payload import (
    episodic_payload,
    summary_payload,
)


logger = logging.getLogger(__name__)


class VectorService:
    """Qdrant 向量服务 - 使用官方 Async SDK，客户端延迟创建。"""

    def __init__(self) -> None:
        self._client: Optional[AsyncQdrantClient] = None
        self._known_collections: set[str] = set()
        self._indexed_collections: set[str] = set()

    async def _get_client(self) -> AsyncQdrantClient:
        if self._client is None:
            if settings.QDRANT_URL:
                self._client = AsyncQdrantClient(
                    url=settings.QDRANT_URL,
                    api_key=settings.QDRANT_API_KEY or None,
                    timeout=settings.QDRANT_TIMEOUT,
                    check_compatibility=False,
                    prefer_grpc=settings.QDRANT_PREFER_GRPC,
                    grpc_port=settings.QDRANT_GRPC_PORT,
                    https=settings.QDRANT_HTTPS,
                )
            else:
                self._client = AsyncQdrantClient(
                    host=settings.QDRANT_HOST,
                    port=settings.QDRANT_PORT,
                    api_key=settings.QDRANT_API_KEY or None,
                    timeout=settings.QDRANT_TIMEOUT,
                    check_compatibility=False,
                    prefer_grpc=settings.QDRANT_PREFER_GRPC,
                    grpc_port=settings.QDRANT_GRPC_PORT,
                    https=settings.QDRANT_HTTPS,
                )
        return self._client

    async def _ensure_payload_indexes(self, collection_name: str) -> None:
        if collection_name in self._indexed_collections:
            return

        client = await self._get_client()
        index_fields = [
            "tenant_id",
            "user_id",
            "agent_id",
            "memory_type",
            "session_id",
            "namespace",
            "vector_id",
        ]
        for field_name in index_fields:
            try:
                await client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=False,
                    timeout=settings.QDRANT_TIMEOUT,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to create Qdrant payload index",
                    exc_info=True,
                    extra={
                        "event": "vector.payload_index.failed",
                        "error_type": type(exc).__name__,
                    },
                )

        self._indexed_collections.add(collection_name)

    async def get_collections(self) -> list[str]:
        client = await self._get_client()
        response = await client.get_collections()
        collections = [collection.name for collection in response.collections]
        self._known_collections = set(collections)
        return collections

    async def create_collection(self, collection_name: str, vector_size: int) -> bool:
        client = await self._get_client()
        if collection_name in self._known_collections:
            await self._ensure_payload_indexes(collection_name)
            return True

        exists = collection_name in await self.get_collections()
        if not exists:
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                ),
                timeout=settings.QDRANT_TIMEOUT,
            )

        self._known_collections.add(collection_name)
        await self._ensure_payload_indexes(collection_name)
        return True

    async def upsert(self, collection_name: str, points: list[dict[str, Any]]) -> bool:
        if not points:
            return True

        client = await self._get_client()
        dimension = len(points[0]["vector"])
        await self.create_collection(collection_name, dimension)

        await client.upsert(
            collection_name=collection_name,
            points=[
                models.PointStruct(
                    id=point["id"],
                    vector=point["vector"],
                    payload=point.get("payload", {}),
                )
                for point in points
            ],
            wait=True,
            timeout=settings.QDRANT_TIMEOUT,
        )
        return True

    async def upsert_episodic(
        self, collection_name: str, memory: EpisodicMemory, vector: list[float]
    ) -> bool:
        return await self.upsert(
            collection_name,
            [
                {
                    "id": memory.vector_id,
                    "vector": vector,
                    "payload": episodic_payload(memory),
                }
            ],
        )

    async def upsert_summary(
        self, collection_name: str, summary: Summary, vector: list[float]
    ) -> bool:
        if summary.vector_id is None:
            return False
        return await self.upsert(
            collection_name,
            [
                {
                    "id": summary.vector_id,
                    "vector": vector,
                    "payload": summary_payload(
                        tenant_id=summary.tenant_id,
                        user_id=summary.user_id,
                        agent_id=summary.agent_id,
                        namespace=summary.namespace,
                        vector_id=summary.vector_id,
                        content=summary.content,
                    ),
                }
            ],
        )

    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        top_k: int = 5,
        filter_agent_id: Optional[str] = None,
        filters: Optional[dict[str, str]] = None,
    ) -> list[dict[str, Any]]:
        client = await self._get_client()
        must_conditions: list[models.FieldCondition] = []
        if filter_agent_id:
            must_conditions.append(
                models.FieldCondition(
                    key="agent_id",
                    match=models.MatchValue(value=filter_agent_id),
                )
            )
        for key, value in (filters or {}).items():
            if value:
                must_conditions.append(
                    models.FieldCondition(
                        key=key,
                        match=models.MatchValue(value=value),
                    )
                )
        query_filter: models.Filter | None = None
        if must_conditions:
            query_filter = models.Filter(
                must=must_conditions  # type: ignore[arg-type]
            )

        response = await client.query_points(
            collection_name=collection_name,
            query=query_vector,
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
            with_vectors=False,
            timeout=settings.QDRANT_TIMEOUT,
        )

        return [
            {
                "id": str(point.id),
                "score": point.score,
                "payload": point.payload or {},
            }
            for point in response.points
        ]

    async def delete_collection(self, collection_name: str) -> bool:
        client = await self._get_client()
        await client.delete_collection(
            collection_name=collection_name,
            timeout=settings.QDRANT_TIMEOUT,
        )
        self._known_collections.discard(collection_name)
        self._indexed_collections.discard(collection_name)
        return True

    async def delete_points(self, collection_name: str, point_ids: list[str]) -> bool:
        if not point_ids:
            return True

        client = await self._get_client()
        selector = models.PointIdsList.model_validate({"points": point_ids})
        await client.delete(
            collection_name=collection_name,
            points_selector=selector,
            wait=True,
            timeout=settings.QDRANT_TIMEOUT,
        )
        return True


vector_service = VectorService()


async def get_vector_service() -> VectorService:
    return vector_service
