"""Redis L0 working-memory adapter."""

from __future__ import annotations

import json
import inspect
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import uuid4

import redis.asyncio as redis

from mems.config import settings
from mems.domain.events import MemoryPipelineEvent
from mems.domain.identity import (
    MemoryIdentity,
    MemoryNamespace,
    encode_redis_component,
)
from mems.domain.memory import WorkingMemory


logger = logging.getLogger(__name__)


def build_l0_pipeline_event(l0_data: WorkingMemory, event_type: str) -> Dict[str, str]:
    """Build the flat field map for one L0 output-stream event."""
    event = MemoryPipelineEvent(
        event_id=uuid4().hex,
        event_type=event_type,
        timestamp=datetime.now(timezone.utc),
        tenant_id=l0_data.tenant_id,
        user_id=l0_data.user_id,
        agent_id=l0_data.agent_id,
        session_id=l0_data.session_id,
        namespace=l0_data.namespace,
        content=l0_data.content,
        messages=json.dumps(l0_data.short_term_buffer, ensure_ascii=False),
        expires_at=l0_data.expires_at,
    )
    raw = event.model_dump(mode="json")
    return {key: (value if value is not None else "") for key, value in raw.items()}


async def publish_l0_event(client, event: Dict[str, str]) -> None:
    """Publish one event to the L0 output stream when the pipeline is enabled."""
    if not settings.L0_PIPELINE_ENABLED or not settings.L0_PIPELINE_STREAM:
        return
    try:
        await client.xadd(
            settings.L0_PIPELINE_STREAM,
            event,
            id="*",
            maxlen=settings.L0_PIPELINE_MAXLEN,
            approximate=True,
        )
    except Exception as exc:
        logger.warning(
            "Failed to publish L0 pipeline event",
            exc_info=True,
            extra={
                "event": "memory.l0_pipeline_publish.failed",
                "error_type": type(exc).__name__,
            },
        )


class RedisService:
    """Redis L0 工作记忆服务"""

    def __init__(self):
        self._client: Optional[redis.Redis] = None

    async def get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD or None,
                decode_responses=True,
            )
        return self._client

    async def ping(self) -> None:
        client = await self.get_client()
        ping_result = client.ping()
        if inspect.isawaitable(ping_result):
            await ping_result

    def _key(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> str:
        return MemoryIdentity(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            memory_namespace=(
                MemoryNamespace.from_key(namespace) if namespace is not None else None
            ),
        ).redis_key()

    async def write(
        self,
        tenant_id: Optional[str],
        user_id: Optional[str],
        agent_id: str,
        session_id: Optional[str],
        messages: List[Dict[str, str]],
        content: Optional[str] = None,
        namespace: Optional[str] = None,
        ttl_seconds: int = 1800,
        event_type: str = "write",
    ) -> WorkingMemory:
        client = await self.get_client()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        l0_data = WorkingMemory(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            namespace=namespace,
            content=content,
            short_term_buffer=messages,
            expires_at=expires_at,
        )

        key = self._key(agent_id, session_id, tenant_id, user_id, namespace)
        await client.set(
            key,
            l0_data.model_dump_json(),
            ex=ttl_seconds,
        )
        await publish_l0_event(client, build_l0_pipeline_event(l0_data, event_type))
        return l0_data

    async def read(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> Optional[WorkingMemory]:
        client = await self.get_client()
        key = self._key(agent_id, session_id, tenant_id, user_id, namespace)
        data = await client.get(key)

        if data:
            return WorkingMemory.model_validate_json(data)
        return None

    async def list_active(
        self,
        agent_id: str,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        namespace: Optional[str] = None,
        limit: int = 10,
    ) -> List[WorkingMemory]:
        client = await self.get_client()
        tenant_part = encode_redis_component(tenant_id) if tenant_id is not None else "*"
        user_part = encode_redis_component(user_id) if user_id is not None else "*"
        namespace_part = (
            encode_redis_component(namespace) if namespace is not None else "*"
        )
        agent_part = encode_redis_component(agent_id)
        pattern = f"mems:l0:{tenant_part}:{user_part}:{namespace_part}:{agent_part}:*"

        snapshots: List[WorkingMemory] = []
        async for key in client.scan_iter(match=pattern, count=100):
            data = await client.get(key)
            if not data:
                continue
            try:
                snapshots.append(WorkingMemory.model_validate_json(data))
            except Exception as exc:
                logger.warning(
                    "Skipping malformed L0 snapshot",
                    extra={
                        "event": "memory.l0_snapshot.invalid",
                        "error_type": type(exc).__name__,
                    },
                )
                continue

        snapshots.sort(key=lambda item: item.expires_at, reverse=True)
        return snapshots[:limit]

    async def delete(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> bool:
        client = await self.get_client()
        key = self._key(agent_id, session_id, tenant_id, user_id, namespace)
        result = await client.delete(key)
        return result > 0

    async def append_messages(
        self,
        tenant_id: Optional[str],
        user_id: Optional[str],
        agent_id: str,
        session_id: Optional[str],
        messages: List[Dict[str, str]],
        content: Optional[str] = None,
        ttl_seconds: int = 1800,
        max_buffer_size: int = 10,
        namespace: Optional[str] = None,
    ) -> WorkingMemory:
        existing = await self.read(
            agent_id,
            session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            namespace=namespace,
        )
        if existing is None:
            trimmed = messages[-max_buffer_size:] if max_buffer_size > 0 else messages
            return await self.write(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                messages=trimmed,
                content=content,
                namespace=namespace,
                ttl_seconds=ttl_seconds,
            )

        merged = existing.merge_append(messages, content, namespace, max_buffer_size)
        client = await self.get_client()
        current_ttl = await client.ttl(
            self._key(agent_id, session_id, tenant_id, user_id, merged.namespace)
        )
        effective_ttl = current_ttl if current_ttl and current_ttl > 0 else ttl_seconds

        return await self.write(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            messages=merged.short_term_buffer,
            content=merged.content,
            namespace=merged.namespace,
            ttl_seconds=effective_ttl,
            event_type="append",
        )

    async def close(self):
        if self._client:
            await self._client.close()
            self._client = None


redis_service = RedisService()


async def get_redis_service() -> RedisService:
    return redis_service
