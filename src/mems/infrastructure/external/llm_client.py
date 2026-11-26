"""OpenAI-compatible LLM adapter."""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List, Optional, cast

from openai import AsyncOpenAI
from pydantic import SecretStr

from mems.config import settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None


def _secret_text(value: SecretStr | str) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def get_llm_client() -> AsyncOpenAI:
    """Create and cache the shared async OpenAI-compatible client."""
    global _client
    if settings.LLM_PROVIDER != "openai":
        raise ValueError("LLM_PROVIDER is disabled")
    if _client is None:
        api_key = _secret_text(settings.OPENAI_API_KEY)
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured")
        _client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.OPENAI_BASE_URL,
            timeout=float(settings.OPENAI_TIMEOUT),
        )
    return _client


async def close_llm_client() -> None:
    """Close and reset the shared LLM client; repeated calls are harmless."""
    global _client
    client = _client
    if client is None:
        return
    _client = None
    result = client.close()
    if inspect.isawaitable(result):
        await result


async def chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
    json_mode: bool = False,
) -> str:
    """Send a non-streaming chat request to the configured LLM."""
    client = get_llm_client()
    model = model or settings.OPENAI_MODEL
    request_kwargs: Dict[str, Any] = {
        "model": model,
        "messages": cast(Any, messages),
        "temperature": temperature,
    }
    if json_mode:
        request_kwargs["response_format"] = {"type": "json_object"}
    try:
        response = await client.chat.completions.create(**request_kwargs)
    except Exception as exc:
        logger.error(
            "LLM request failed",
            exc_info=True,
            extra={
                "event": "llm.request.failed",
                "model": model,
                "error_type": type(exc).__name__,
                "result": "failed",
            },
        )
        raise
    return response.choices[0].message.content or ""


class OpenAILLMClient:
    """LLMPort adapter exposing a ``chat`` method."""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> str:
        return await chat(messages, model, temperature, json_mode)

    async def close(self) -> None:
        await close_llm_client()

    async def aclose(self) -> None:
        await self.close()


llm_client = OpenAILLMClient()


def get_llm_port() -> OpenAILLMClient:
    return llm_client
