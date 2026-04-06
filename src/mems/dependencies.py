from typing import Generator
from sqlmodel import Session

from mems.database import get_session
from mems.services.redis_service import RedisService, get_redis_service


def get_db() -> Generator[Session, None, None]:
    """Proxy the shared SQLModel session dependency.

    代理统一的 SQLModel 会话依赖。
    """
    yield from get_session()


async def get_redis() -> RedisService:
    """Resolve the shared Redis service dependency.

    获取共享的 Redis 服务依赖。
    """
    return await get_redis_service()
