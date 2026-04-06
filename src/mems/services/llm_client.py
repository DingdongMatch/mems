import logging
from typing import Any, AsyncIterator, Dict, List, Optional, cast
from openai import AsyncOpenAI

from mems.config import settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None


def get_llm_client() -> AsyncOpenAI:
    """Create and cache the shared async LLM client.

    创建并缓存共享的异步 LLM 客户端。
    """
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not configured")
        _client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            timeout=float(settings.OPENAI_TIMEOUT),
        )
    return _client


async def chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
) -> str:
    """Send a non-streaming chat request to the configured LLM.

    向配置好的 LLM 发送非流式聊天请求。
    """
    client = get_llm_client()
    model = model or settings.OPENAI_MODEL

    response = await client.chat.completions.create(
        model=model,
        messages=cast(Any, messages),
        temperature=temperature,
    )

    return response.choices[0].message.content or ""


async def stream_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
) -> AsyncIterator[str]:
    """Stream incremental chat deltas from the configured LLM.

    从配置好的 LLM 流式返回增量聊天内容。
    """
    client = get_llm_client()
    model = model or settings.OPENAI_MODEL

    stream = await client.chat.completions.create(
        model=model,
        messages=cast(Any, messages),
        temperature=temperature,
        stream=True,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield delta
