from sqlmodel import Session, create_engine, SQLModel
from typing import Generator

from mems.config import settings


engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.LOG_LEVEL == "DEBUG",
    connect_args={"check_same_thread": False}
    if "sqlite" in settings.DATABASE_URL
    else {},
)


def init_db() -> None:
    """Create database tables for all registered SQLModel models.

    为所有已注册的 SQLModel 模型创建数据库表。
    """
    import mems.models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """Yield a database session for request or service usage.

    提供一个可用于请求或服务逻辑的数据库会话。
    """
    with Session(engine) as session:
        yield session
