from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
import redis.asyncio as redis

from mems.config import settings
from mems.schemas import MemsL0Working


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

    def _key(self, agent_id: str, session_id: str) -> str:
        return f"mems:l0:{agent_id}:{session_id}"

    async def write(
        self,
        agent_id: str,
        session_id: str,
        messages: List[Dict[str, str]],
        active_plan: Optional[str] = None,
        temp_variables: Dict[str, Any] | None = None,
        ttl_seconds: int = 1800,
    ) -> MemsL0Working:
        """写入 L0 工作记忆"""
        client = await self.get_client()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        l0_data = MemsL0Working(
            agent_id=agent_id,
            session_id=session_id,
            short_term_buffer=messages,
            active_plan=active_plan,
            temp_variables=temp_variables or {},
            expires_at=expires_at,
        )

        key = self._key(agent_id, session_id)
        await client.set(
            key,
            l0_data.model_dump_json(),
            ex=ttl_seconds,
        )
        return l0_data

    async def read(self, agent_id: str, session_id: str) -> Optional[MemsL0Working]:
        """读取 L0 工作记忆"""
        client = await self.get_client()
        key = self._key(agent_id, session_id)
        data = await client.get(key)

        if data:
            return MemsL0Working.model_validate_json(data)
        return None

    async def delete(self, agent_id: str, session_id: str) -> bool:
        """删除 L0 工作记忆"""
        client = await self.get_client()
        key = self._key(agent_id, session_id)
        result = await client.delete(key)
        return result > 0

    async def append_message(
        self,
        agent_id: str,
        session_id: str,
        message: Dict[str, str],
        max_buffer_size: int = 10,
    ) -> Optional[MemsL0Working]:
        """追加消息到缓冲区"""
        l0 = await self.read(agent_id, session_id)
        if l0 is None:
            return None

        l0.short_term_buffer.append(message)
        if len(l0.short_term_buffer) > max_buffer_size:
            l0.short_term_buffer = l0.short_term_buffer[-max_buffer_size:]

        client = await self.get_client()
        ttl = await client.ttl(self._key(agent_id, session_id))
        if ttl > 0:
            await client.set(
                self._key(agent_id, session_id),
                l0.model_dump_json(),
                ex=ttl,
            )
        return l0

    async def close(self):
        if self._client:
            await self._client.close()
            self._client = None


redis_service = RedisService()


async def get_redis_service() -> RedisService:
    return redis_service
