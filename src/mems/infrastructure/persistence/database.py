"""Database engine, schema initialization, and session factories."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from sqlmodel import Session, SQLModel, create_engine

from mems.config import settings


engine = create_engine(
    settings.DATABASE_URL,
    # SQL echo can write memory contents from bound parameters to logs.
    echo=False,
    pool_pre_ping=True,
)


def init_db() -> None:
    """Create database tables for all registered SQLModel models."""
    import mems.infrastructure.persistence.models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """Yield a database session for request or service usage."""
    with Session(engine) as session:
        yield session


class SqlSessionFactory:
    """Create independent sessions for background application services."""

    def __init__(self, target_engine: Any = engine) -> None:
        self._engine = target_engine

    def create(self):
        """Return a context manager yielding a fresh Session."""

        @contextmanager
        def session_scope() -> Generator[Session, None, None]:
            with Session(self._engine) as session:
                yield session

        return session_scope()


sql_session_factory = SqlSessionFactory()
