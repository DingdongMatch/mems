"""Embedding provider adapters."""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from typing import Any, List, cast

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from pydantic import SecretStr

from mems.config import settings


class EmbeddingProvider(ABC):
    """Embedding 提供者抽象基类"""

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Convert input texts into embedding vectors."""
        pass

    async def close(self) -> None:
        """Release provider resources, if any."""


class SentenceTransformersProvider(EmbeddingProvider):
    """sentence-transformers 本地向量化"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self.model_name = model_name
        self._model: Any = None
        self._load_lock = asyncio.Lock()

    def _load_model_sync(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name)

    async def _load_model(self) -> None:
        if self._model is None:
            async with self._load_lock:
                if self._model is None:
                    await asyncio.to_thread(self._load_model_sync)

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        await self._load_model()
        model = cast(object, self._model)
        embeddings = await asyncio.to_thread(
            model.encode,  # type: ignore[attr-defined]
            texts,
            convert_to_numpy=True,
        )
        return [emb.tolist() for emb in embeddings]


def _is_retryable_embedding_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return False


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible API 向量化，复用异步 HTTP 客户端。"""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        timeout=self.timeout,
                    )
        return self._client

    @retry(
        retry=retry_if_exception(_is_retryable_embedding_error),
        wait=wait_exponential(multiplier=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _request(self, texts: List[str]) -> List[List[float]]:
        client = await self._get_client()
        response = await client.post(
            f"{self.base_url}/embeddings",
            json={"input": texts, "model": self.model},
        )
        response.raise_for_status()
        data = response.json()
        items = sorted(data["data"], key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in items]

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return await self._request(texts)

    async def close(self) -> None:
        client = self._client
        if client is None:
            return
        self._client = None
        await client.aclose()

    async def aclose(self) -> None:
        await self.close()


def _secret_text(value: SecretStr | str) -> str:
    """Read a configured secret while tolerating legacy string test doubles."""
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def get_embedding_provider() -> EmbeddingProvider:
    """Build the configured embedding provider implementation."""
    if settings.EMBEDDING_PROVIDER == "sentence-transformers":
        return SentenceTransformersProvider(settings.SENTENCE_TRANSFORMERS_MODEL)
    if settings.EMBEDDING_PROVIDER == "openai":
        api_key = _secret_text(settings.OPENAI_EMBEDDING_API_KEY) or _secret_text(
            settings.OPENAI_API_KEY
        )
        if not api_key:
            raise ValueError(
                "OPENAI_EMBEDDING_API_KEY or OPENAI_API_KEY is required when "
                "using openai embedding provider"
            )
        return OpenAIEmbeddingProvider(
            api_key,
            settings.OPENAI_EMBEDDING_MODEL,
            settings.OPENAI_EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL,
            timeout=float(settings.OPENAI_TIMEOUT),
        )
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {settings.EMBEDDING_PROVIDER}")


_embedding_provider: EmbeddingProvider | None = None


async def get_embedding_service() -> EmbeddingProvider:
    """Return a cached embedding provider singleton."""
    global _embedding_provider
    if _embedding_provider is None:
        _embedding_provider = get_embedding_provider()
    return _embedding_provider


async def close_embedding_service() -> None:
    """Close and reset the embedding singleton without creating it."""
    global _embedding_provider
    provider = _embedding_provider
    _embedding_provider = None
    if provider is not None:
        result = provider.close()
        if inspect.isawaitable(result):
            await result
