import logging
from typing import Any, Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from mems.config import settings


logger = logging.getLogger(__name__)


class VectorService:
    """Qdrant 向量服务 - 使用官方 Async SDK。"""

    def __init__(self) -> None:
        """Create the async Qdrant client and local caches.

        初始化异步 Qdrant 客户端及本地缓存集合。
        """
        client_kwargs = {
            "api_key": settings.QDRANT_API_KEY or None,
            "timeout": settings.QDRANT_TIMEOUT,
            "check_compatibility": False,
            "prefer_grpc": settings.QDRANT_PREFER_GRPC,
            "grpc_port": settings.QDRANT_GRPC_PORT,
            "https": settings.QDRANT_HTTPS,
        }
        if settings.QDRANT_URL:
            client_kwargs["url"] = settings.QDRANT_URL
        else:
            client_kwargs["host"] = settings.QDRANT_HOST
            client_kwargs["port"] = settings.QDRANT_PORT

        self._client = AsyncQdrantClient(**client_kwargs)
        self._known_collections: set[str] = set()
        self._indexed_collections: set[str] = set()

    async def _ensure_payload_indexes(self, collection_name: str) -> None:
        """Ensure common payload indexes exist for a collection.

        确保指定集合已创建常用 payload 索引。
        """
        if collection_name in self._indexed_collections:
            return

        index_fields = [
            "tenant_id",
            "user_id",
            "agent_id",
            "memory_type",
            "session_id",
            "scope",
            "vector_id",
        ]
        for field_name in index_fields:
            try:
                await self._client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    # Index creation can be slow on freshly created collections.
                    # Do not block writes on synchronous optimizer completion.
                    wait=False,
                    timeout=settings.QDRANT_TIMEOUT,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to create payload index %s on %s: %r",
                    field_name,
                    collection_name,
                    exc,
                )

        self._indexed_collections.add(collection_name)

    async def get_collections(self) -> list[str]:
        """Fetch and cache available Qdrant collection names.

        获取并缓存当前可用的 Qdrant 集合名称。
        """
        response = await self._client.get_collections()
        collections = [collection.name for collection in response.collections]
        self._known_collections = set(collections)
        return collections

    async def create_collection(self, collection_name: str, vector_size: int) -> bool:
        """Create a collection if it does not already exist.

        如果集合不存在，则创建该集合。
        """
        if collection_name in self._known_collections:
            await self._ensure_payload_indexes(collection_name)
            return True

        exists = collection_name in await self.get_collections()
        if not exists:
            await self._client.create_collection(
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
        """Insert or update a batch of vector points.

        批量插入或更新向量点。
        """
        if not points:
            return True

        dimension = len(points[0]["vector"])
        await self.create_collection(collection_name, dimension)

        await self._client.upsert(
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

    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        top_k: int = 5,
        filter_agent_id: Optional[str] = None,
        filters: Optional[dict[str, str]] = None,
    ) -> list[dict[str, Any]]:
        """Run vector search with optional identity filters.

        执行带可选身份过滤条件的向量检索。
        """
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
            query_filter = models.Filter(must=must_conditions)

        response = await self._client.query_points(
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
        """Delete a collection and clear local cache state.

        删除集合并清理本地缓存状态。
        """
        await self._client.delete_collection(
            collection_name=collection_name,
            timeout=settings.QDRANT_TIMEOUT,
        )
        self._known_collections.discard(collection_name)
        self._indexed_collections.discard(collection_name)
        return True

    async def delete_points(self, collection_name: str, point_ids: list[str]) -> bool:
        """Delete specific vector points from a collection.

        从集合中删除指定向量点。
        """
        if not point_ids:
            return True

        selector = models.PointIdsList.model_validate({"points": point_ids})
        await self._client.delete(
            collection_name=collection_name,
            points_selector=selector,
            wait=True,
            timeout=settings.QDRANT_TIMEOUT,
        )
        return True


vector_service = VectorService()


async def get_vector_service() -> VectorService:
    """Return the module-level vector service singleton.

    返回模块级向量服务单例。
    """
    return vector_service
