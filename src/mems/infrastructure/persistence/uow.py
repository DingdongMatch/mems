"""SQLModel-backed unit of work and database health check."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, select

from mems.infrastructure.persistence import models as orm


class SqlUnitOfWork:
    """Coordinate commit/rollback for repositories bound to one session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    @contextmanager
    def begin_nested(self) -> Iterator[None]:
        """Open a savepoint that is rolled back when its body fails."""
        with self._session.begin_nested():
            yield


class SqlDatabaseHealthCheck:
    """Verify database connectivity with a lightweight query."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def healthcheck(self) -> None:
        self._session.exec(select(orm.MemsL1Episodic).limit(1)).all()
