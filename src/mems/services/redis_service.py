from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
import redis.asyncio as redis

from mems.config import settings
from mems.schemas import MemsL0Working


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
        session_id: str,
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
        return f"mems:l0:{tenant_part}:{user_part}:{scope_part}:{agent_id}:{session_id}"

    async def write(
        self,
        tenant_id: Optional[str],
        user_id: Optional[str],
        agent_id: str,
        session_id: str,
        messages: List[Dict[str, str]],
        scope: Optional[str] = None,
        active_plan: Optional[str] = None,
        temp_variables: Dict[str, Any] | None = None,
        ttl_seconds: int = 1800,
    ) -> MemsL0Working:
        """Write the current working-memory snapshot into Redis.

        将当前工作记忆快照写入 Redis。
        """
        client = await self.get_client()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        l0_data = MemsL0Working(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            scope=scope,
            short_term_buffer=messages,
            active_plan=active_plan,
            temp_variables=temp_variables or {},
            expires_at=expires_at,
        )

        key = self._key(agent_id, session_id, tenant_id, user_id, scope)
        await client.set(
            key,
            l0_data.model_dump_json(),
            ex=ttl_seconds,
        )
        return l0_data

    async def read(
        self,
        agent_id: str,
        session_id: str,
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
        session_id: str,
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
        session_id: str,
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
        return l0

    async def append_messages(
        self,
        tenant_id: Optional[str],
        user_id: Optional[str],
        agent_id: str,
        session_id: str,
        messages: List[Dict[str, str]],
        ttl_seconds: int = 1800,
        max_buffer_size: int = 10,
        scope: Optional[str] = None,
        active_plan: Optional[str] = None,
        temp_variables: Dict[str, Any] | None = None,
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
                scope=scope,
                active_plan=active_plan,
                temp_variables=temp_variables,
                ttl_seconds=ttl_seconds,
            )

        merged_messages = existing.short_term_buffer + messages
        trimmed_messages = merged_messages[-max_buffer_size:]
        merged_plan = active_plan if active_plan is not None else existing.active_plan
        merged_scope = scope if scope is not None else existing.scope
        merged_variables = dict(existing.temp_variables)
        if temp_variables:
            merged_variables.update(temp_variables)

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
            scope=merged_scope,
            active_plan=merged_plan,
            temp_variables=merged_variables,
            ttl_seconds=effective_ttl,
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
