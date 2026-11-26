"""Redis L0 working-memory adapter."""

from __future__ import annotations

import json
import inspect
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, cast
from uuid import uuid4

import redis.asyncio as redis
from pydantic import SecretStr

from mems.config import settings
from mems.domain.events import MemoryPipelineEvent
from mems.domain.identity import (
    MemoryIdentity,
    MemoryNamespace,
    encode_redis_component,
)
from mems.domain.memory import WorkingMemory


logger = logging.getLogger(__name__)


def _secret_text(value: SecretStr | str) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


_WRITE_SNAPSHOT_LUA = r"""
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
redis.call('ZADD', KEYS[2], ARGV[3], KEYS[1])
return 1
"""

_DELETE_SNAPSHOT_LUA = r"""
local removed = redis.call('DEL', KEYS[1])
redis.call('ZREM', KEYS[2], KEYS[1])
return removed
"""

_APPEND_MESSAGES_LUA = r"""
local current = redis.call('GET', KEYS[1])
if not current then
    redis.call('SET', KEYS[1], ARGV[8], 'PX', ARGV[7])
    redis.call('ZADD', KEYS[2], ARGV[9], KEYS[1])
    return {ARGV[8], ARGV[7], '0'}
end

local ttl = redis.call('PTTL', KEYS[1])
if ttl < 0 then
    ttl = tonumber(ARGV[7])
end
local snapshot = cjson.decode(current)
local incoming = cjson.decode(ARGV[1])
local merged = {}
if type(snapshot.short_term_buffer) == 'table' then
    for _, message in ipairs(snapshot.short_term_buffer) do
        table.insert(merged, message)
    end
end
for _, message in ipairs(incoming) do
    table.insert(merged, message)
end
local max_buffer = tonumber(ARGV[2])
if max_buffer > 0 and #merged > max_buffer then
    local trimmed = {}
    for index = #merged - max_buffer + 1, #merged do
        table.insert(trimmed, merged[index])
    end
    merged = trimmed
end
snapshot.short_term_buffer = merged
if ARGV[3] == '1' then
    snapshot.content = cjson.decode(ARGV[4])
end
if ARGV[5] == '1' then
    snapshot.namespace = cjson.decode(ARGV[6])
end
local encoded = cjson.encode(snapshot)
encoded = string.gsub(encoded, '"short_term_buffer":{}', '"short_term_buffer":[]')
redis.call('SET', KEYS[1], encoded, 'PX', ttl)
local now = redis.call('TIME')
local expires_at = tonumber(now[1]) + tonumber(now[2]) / 1000000 + ttl / 1000
redis.call('ZADD', KEYS[2], expires_at, KEYS[1])
return {encoded, tostring(ttl), '1'}
"""


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
                password=_secret_text(settings.REDIS_PASSWORD) or None,
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

    def _index_key(
        self,
        agent_id: str,
        tenant_id: Optional[str],
        user_id: Optional[str],
        namespace: Optional[str],
    ) -> str:
        effective_namespace = namespace or MemoryIdentity(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
        ).namespace_key
        return ":".join(
            (
                "mems",
                "l0",
                "index",
                encode_redis_component(tenant_id),
                encode_redis_component(user_id),
                encode_redis_component(effective_namespace),
                encode_redis_component(agent_id),
            )
        )

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
        index_key = self._index_key(agent_id, tenant_id, user_id, namespace)
        write_result = client.eval(
            _WRITE_SNAPSHOT_LUA,
            2,
            key,
            index_key,
            l0_data.model_dump_json(),
            ttl_seconds * 1000,
            expires_at.timestamp(),
        )
        if inspect.isawaitable(write_result):
            await write_result
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
        if limit <= 0:
            return []
        client = await self.get_client()
        index_key = self._index_key(agent_id, tenant_id, user_id, namespace)
        now = time.time()
        await client.zremrangebyscore(index_key, "-inf", now)
        snapshots: List[WorkingMemory] = []
        offset = 0
        while len(snapshots) < limit and offset < limit * 4:
            keys = await client.zrevrangebyscore(
                index_key,
                "+inf",
                now,
                start=offset,
                num=limit,
            )
            if not keys:
                break
            async with client.pipeline(transaction=False) as pipeline:
                for key in keys:
                    pipeline.get(key)
                values = await pipeline.execute()
            for key, data in zip(keys, values, strict=True):
                if not data:
                    await client.zrem(index_key, key)
                    continue
                try:
                    snapshots.append(WorkingMemory.model_validate_json(data))
                except Exception as exc:
                    await client.zrem(index_key, key)
                    logger.warning(
                        "Skipping malformed L0 snapshot",
                        extra={
                            "event": "memory.l0_snapshot.invalid",
                            "error_type": type(exc).__name__,
                        },
                    )
            offset += len(keys)
            if len(keys) < limit:
                break
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
        index_key = self._index_key(agent_id, tenant_id, user_id, namespace)
        result = client.eval(_DELETE_SNAPSHOT_LUA, 2, key, index_key)
        if inspect.isawaitable(result):
            result = await result
        return int(result) > 0

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
        client = await self.get_client()
        key = self._key(agent_id, session_id, tenant_id, user_id, namespace)
        index_key = self._index_key(agent_id, tenant_id, user_id, namespace)
        now = datetime.now(timezone.utc)
        fresh = WorkingMemory(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            namespace=namespace,
            content=content,
            short_term_buffer=(
                messages[-max_buffer_size:] if max_buffer_size > 0 else messages
            ),
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        raw_result = client.eval(
            _APPEND_MESSAGES_LUA,
            2,
            key,
            index_key,
            json.dumps(messages, ensure_ascii=False),
            max_buffer_size,
            int(content is not None),
            json.dumps(content, ensure_ascii=False),
            int(namespace is not None),
            json.dumps(namespace, ensure_ascii=False),
            ttl_seconds * 1000,
            fresh.model_dump_json(),
            fresh.expires_at.timestamp(),
        )
        if inspect.isawaitable(raw_result):
            raw_result = await raw_result
        raw = cast(List[str], raw_result)
        merged = WorkingMemory.model_validate_json(raw[0])
        event_type = "append" if str(raw[2]) == "1" else "write"
        await publish_l0_event(client, build_l0_pipeline_event(merged, event_type))
        return merged

    async def close(self) -> None:
        client = self._client
        if client is None:
            return
        self._client = None
        closer = getattr(client, "aclose", None) or client.close
        result = closer()
        if inspect.isawaitable(result):
            await result

    async def aclose(self) -> None:
        await self.close()


async def close_redis_service() -> None:
    """Close the shared Redis client without creating a connection."""
    await redis_service.close()


redis_service = RedisService()


async def get_redis_service() -> RedisService:
    return redis_service
