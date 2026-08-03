from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Optional, List, Dict
from uuid import uuid4
import redis.asyncio as redis

from mems.config import settings
from mems.schemas import MemsL0Working, MemoryPipelineEvent


logger = logging.getLogger(__name__)


def build_l0_pipeline_event(
    l0_data: MemsL0Working, event_type: str
) -> Dict[str, str]:
    """Build the flat field map for one L0 output-stream event.

    构建一条 L0 输出流事件的扁平字段映射。Redis Stream 存储扁平键值对，
    事件先按 MemoryPipelineEvent 校验，再扁平化为字符串字段；未设置的身份字段
    为空字符串，嵌套结构（如 messages）为 JSON 字符串。
    """
    event = MemoryPipelineEvent(
        event_id=uuid4().hex,
        event_type=event_type,
        timestamp=datetime.now(timezone.utc),
        tenant_id=l0_data.tenant_id,
        user_id=l0_data.user_id,
        agent_id=l0_data.agent_id,
        session_id=l0_data.session_id,
        scope=l0_data.scope,
        content=l0_data.content,
        messages=json.dumps(l0_data.short_term_buffer, ensure_ascii=False),
        expires_at=l0_data.expires_at,
    )
    raw = event.model_dump(mode="json")
    return {key: (value if value is not None else "") for key, value in raw.items()}


async def publish_l0_event(client, event: Dict[str, str]) -> None:
    """Publish one event to the L0 output stream when the pipeline is enabled.

    当输出管道启用时，向 L0 输出流发布一条事件。发布是尽力而为的：
    失败只记录警告，绝不阻断记忆写入本身。
    """
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
        logger.warning(f"Failed to publish L0 pipeline event: {exc}")


class RedisService:
    """Redis L0 工作记忆服务"""

    def __init__(self):
        """Initialize the lazy Redis client holder.

        初始化延迟创建的 Redis 客户端容器。
        """
        self._client: Optional[redis.Redis] = None

    async def get_client(self) -> redis.Redis:
        """Create and cache the async Redis client on first use.

        首次使用时创建并缓存异步 Redis 客户端。
        """
        if self._client is None:
            self._client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD or None,
                decode_responses=True,
            )
        return self._client

    def _key(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> str:
        """Build the Redis key from memory identity fields.

        根据记忆身份字段构造 Redis 键。
        """
        tenant_part = tenant_id or "_"
        user_part = user_id or "_"
        scope_part = scope or "_"
        session_part = session_id or "_"
        return (
            f"mems:l0:{tenant_part}:{user_part}:{scope_part}:{agent_id}:{session_part}"
        )

    async def write(
        self,
        tenant_id: Optional[str],
        user_id: Optional[str],
        agent_id: str,
        session_id: Optional[str],
        messages: List[Dict[str, str]],
        content: Optional[str] = None,
        scope: Optional[str] = None,
        ttl_seconds: int = 1800,
        event_type: str = "write",
    ) -> MemsL0Working:
        """Write the current hot-memory snapshot into Redis.

        将当前热记忆快照写入 Redis。
        """
        client = await self.get_client()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        l0_data = MemsL0Working(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            scope=scope,
            content=content,
            short_term_buffer=messages,
            expires_at=expires_at,
        )

        key = self._key(agent_id, session_id, tenant_id, user_id, scope)
        await client.set(
            key,
            l0_data.model_dump_json(),
            ex=ttl_seconds,
        )
        await publish_l0_event(
            client, build_l0_pipeline_event(l0_data, event_type)
        )
        return l0_data

    async def read(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> Optional[MemsL0Working]:
        """Read a working-memory snapshot from Redis.

        从 Redis 读取工作记忆快照。
        """
        client = await self.get_client()
        key = self._key(agent_id, session_id, tenant_id, user_id, scope)
        data = await client.get(key)

        if data:
            return MemsL0Working.model_validate_json(data)
        return None

    async def delete(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> bool:
        """Delete a working-memory snapshot from Redis.

        从 Redis 删除工作记忆快照。
        """
        client = await self.get_client()
        key = self._key(agent_id, session_id, tenant_id, user_id, scope)
        result = await client.delete(key)
        return result > 0

    async def append_message(
        self,
        agent_id: str,
        session_id: Optional[str],
        message: Dict[str, str],
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        scope: Optional[str] = None,
        max_buffer_size: int = 10,
    ) -> Optional[MemsL0Working]:
        """Append one message to an existing live buffer.

        向现有实时缓冲区追加一条消息。
        """
        l0 = await self.read(
            agent_id,
            session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            scope=scope,
        )
        if l0 is None:
            return None

        l0.short_term_buffer.append(message)
        if len(l0.short_term_buffer) > max_buffer_size:
            l0.short_term_buffer = l0.short_term_buffer[-max_buffer_size:]

        client = await self.get_client()
        ttl = await client.ttl(
            self._key(agent_id, session_id, tenant_id, user_id, scope)
        )
        if ttl > 0:
            await client.set(
                self._key(agent_id, session_id, tenant_id, user_id, scope),
                l0.model_dump_json(),
                ex=ttl,
            )
            await publish_l0_event(client, build_l0_pipeline_event(l0, "append"))
        return l0

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
        scope: Optional[str] = None,
    ) -> MemsL0Working:
        """Append multiple messages and create the session if needed.

        追加多条消息；若会话不存在则自动创建。
        """
        existing = await self.read(
            agent_id,
            session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            scope=scope,
        )
        if existing is None:
            trimmed = messages[-max_buffer_size:]
            return await self.write(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                messages=trimmed,
                content=content,
                scope=scope,
                ttl_seconds=ttl_seconds,
            )

        merged_messages = existing.short_term_buffer + messages
        trimmed_messages = merged_messages[-max_buffer_size:]
        merged_scope = scope if scope is not None else existing.scope
        merged_content = content if content is not None else existing.content

        client = await self.get_client()
        current_ttl = await client.ttl(
            self._key(agent_id, session_id, tenant_id, user_id, merged_scope)
        )
        effective_ttl = current_ttl if current_ttl and current_ttl > 0 else ttl_seconds

        return await self.write(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            messages=trimmed_messages,
            content=merged_content,
            scope=merged_scope,
            ttl_seconds=effective_ttl,
            event_type="append",
        )

    async def close(self):
        """Close and clear the cached Redis client.

        关闭并清理已缓存的 Redis 客户端。
        """
        if self._client:
            await self._client.close()
            self._client = None


redis_service = RedisService()


async def get_redis_service() -> RedisService:
    """Return the module-level Redis service singleton.

    返回模块级 Redis 服务单例。
    """
    return redis_service
