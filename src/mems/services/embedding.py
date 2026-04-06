from abc import ABC, abstractmethod
from typing import List, cast


class EmbeddingProvider(ABC):
    """Embedding 提供者抽象基类"""

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Convert input texts into embedding vectors.

        将输入文本转换为向量表示。
        """
        pass

    @abstractmethod
    async def get_dimension(self) -> int:
        """Return the embedding dimensionality.

        返回向量维度。
        """
        pass


class SentenceTransformersProvider(EmbeddingProvider):
    """sentence-transformers 本地向量化"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        """Store sentence-transformers model configuration lazily.

        保存 sentence-transformers 模型配置并延迟加载。
        """
        self.model_name = model_name
        self._model = None
        self._dimension = None

    async def _load_model(self):
        """Load the local sentence-transformers model once.

        按需一次性加载本地 sentence-transformers 模型。
        """
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            self._dimension = self._model.get_sentence_embedding_dimension()

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts with the local sentence-transformers model.

        使用本地 sentence-transformers 模型生成文本向量。
        """
        await self._load_model()
        model = cast(object, self._model)
        embeddings = model.encode(texts, convert_to_numpy=True)
        return [emb.tolist() for emb in embeddings]

    async def get_dimension(self) -> int:
        """Return the loaded local model vector dimension.

        返回已加载本地模型的向量维度。
        """
        await self._load_model()
        return cast(int, self._dimension)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI API 向量化"""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
    ):
        """Store OpenAI embedding API connection settings.

        保存 OpenAI embedding API 的连接配置。
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._dimension = None

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Call the OpenAI-compatible embeddings endpoint.

        调用兼容 OpenAI 的 embeddings 接口生成向量。
        """
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

    async def get_dimension(self) -> int:
        """Infer vector dimension from the configured model name.

        根据配置的模型名推断向量维度。
        """
        if self._dimension is None:
            if "3-small" in self.model or "ada" in self.model:
                self._dimension = 1536
            elif "3-large" in self.model:
                self._dimension = 3072
            elif "text-embedding-002" in self.model:
                self._dimension = 1536
            else:
                self._dimension = 1536
        return self._dimension


def get_embedding_provider() -> EmbeddingProvider:
    """Build the configured embedding provider implementation.

    根据配置构建对应的 embedding 提供者实现。
    """
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
    else:
        raise ValueError(f"Unknown EMBEDDING_PROVIDER: {settings.EMBEDDING_PROVIDER}")


_embedding_provider: EmbeddingProvider | None = None


async def get_embedding_service() -> EmbeddingProvider:
    """Return a cached embedding provider singleton.

    返回已缓存的 embedding 提供者单例。
    """
    global _embedding_provider
    if _embedding_provider is None:
        _embedding_provider = get_embedding_provider()
    return _embedding_provider
