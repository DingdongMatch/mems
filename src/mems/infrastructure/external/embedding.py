"""Embedding provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, cast


class EmbeddingProvider(ABC):
    """Embedding 提供者抽象基类"""

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Convert input texts into embedding vectors."""
        pass


class SentenceTransformersProvider(EmbeddingProvider):
    """sentence-transformers 本地向量化"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self.model_name = model_name
        self._model = None

    async def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)

    async def embed(self, texts: List[str]) -> List[List[float]]:
        await self._load_model()
        model = cast(object, self._model)
        embeddings = model.encode(  # type: ignore[attr-defined]
            texts, convert_to_numpy=True
        )
        return [emb.tolist() for emb in embeddings]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI API 向量化"""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def embed(self, texts: List[str]) -> List[List[float]]:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": texts, "model": self.model},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return [item["embedding"] for item in data["data"]]


def get_embedding_provider() -> EmbeddingProvider:
    """Build the configured embedding provider implementation."""
    from mems.config import settings

    if settings.EMBEDDING_PROVIDER == "sentence-transformers":
        return SentenceTransformersProvider(settings.SENTENCE_TRANSFORMERS_MODEL)
    elif settings.EMBEDDING_PROVIDER == "openai":
        if not settings.OPENAI_EMBEDDING_API_KEY:
            raise ValueError(
                "OPENAI_EMBEDDING_API_KEY is required when using openai provider"
            )
        return OpenAIEmbeddingProvider(
            settings.OPENAI_EMBEDDING_API_KEY,
            settings.OPENAI_EMBEDDING_MODEL,
        )
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {settings.EMBEDDING_PROVIDER}")


_embedding_provider: EmbeddingProvider | None = None


async def get_embedding_service() -> EmbeddingProvider:
    """Return a cached embedding provider singleton."""
    global _embedding_provider
    if _embedding_provider is None:
        _embedding_provider = get_embedding_provider()
    return _embedding_provider
