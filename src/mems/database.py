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
    """初始化数据库表"""
    import mems.models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """获取数据库会话"""
    with Session(engine) as session:
        yield session
