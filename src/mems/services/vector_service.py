from typing import Dict, List, Any, Optional
import httpx

from mems.config import settings


class VectorService:
    """Qdrant 向量服务 - 使用 REST API"""

    def __init__(self):
        self._base_url = f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}"

    async def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        headers = kwargs.pop("headers", {})
        if settings.QDRANT_API_KEY:
            headers["api-key"] = settings.QDRANT_API_KEY

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                timeout=settings.QDRANT_TIMEOUT,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()

    async def get_collections(self) -> List[str]:
        result = await self._request("GET", "/collections")
        return [c["name"] for c in result.get("result", {}).get("collections", [])]

    async def create_collection(self, collection_name: str, vector_size: int) -> bool:
        """创建 Collection"""
        collections = await self.get_collections()
        if collection_name not in collections:
            await self._request(
                "PUT",
                f"/collections/{collection_name}",
                json={"vectors": {"size": vector_size, "distance": "Cosine"}},
            )
        return True

    async def upsert(self, collection_name: str, points: List[Dict[str, Any]]) -> bool:
        """批量插入/更新向量"""
        dimension = len(points[0]["vector"])
        await self.create_collection(collection_name, dimension)

        await self._request(
            "PUT",
            f"/collections/{collection_name}/points?wait=true",
            json={
                "points": [
                    {
                        "id": p["id"],
                        "vector": p["vector"],
                        "payload": p.get("payload", {}),
                    }
                    for p in points
                ]
            },
        )
        return True

    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        top_k: int = 5,
        filter_agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """向量搜索"""
        json_body = {
            "limit": top_k,
            "vector": query_vector,
        }

        if filter_agent_id:
            json_body["filter"] = {
                "must": [{"key": "agent_id", "match": {"value": filter_agent_id}}]
            }

        result = await self._request(
            "POST", f"/collections/{collection_name}/points/search", json=json_body
        )

        return [
            {
                "id": r["id"],
                "score": r["score"],
                "payload": r.get("payload", {}),
            }
            for r in result.get("result", [])
        ]

    async def delete_collection(self, collection_name: str) -> bool:
        """删除 Collection"""
        await self._request("DELETE", f"/collections/{collection_name}")
        return True

    async def delete_points(self, collection_name: str, point_ids: List[str]) -> bool:
        """删除指定向量点"""
        if not point_ids:
            return True

        await self._request(
            "POST",
            f"/collections/{collection_name}/points/delete?wait=true",
            json={"points": point_ids},
        )
        return True


vector_service = VectorService()


async def get_vector_service() -> VectorService:
    return vector_service
