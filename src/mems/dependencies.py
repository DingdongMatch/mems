from typing import Generator
from sqlmodel import Session

from mems.database import get_session
from mems.services.redis_service import RedisService, get_redis_service


def get_db() -> Generator[Session, None, None]:
    return get_session().__anext__()


async def get_redis() -> RedisService:
    return await get_redis_service()