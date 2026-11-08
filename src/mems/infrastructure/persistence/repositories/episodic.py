"""Episodic memory repository."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import select

from mems.domain.identity import MemoryIdentity
from mems.domain.memory import EpisodicMemory
from mems.infrastructure.persistence import models as orm
from mems.infrastructure.persistence.mappers import (
    episodic_from_orm,
    episodic_to_orm,
)
from mems.infrastructure.persistence.repositories.base import (
    SqlRepository,
    apply_identity_filter,
)


class SqlEpisodicRepository(SqlRepository):
    """SQLModel-backed L1 episodic repository."""

    def add(self, memory: EpisodicMemory) -> Optional[int]:
        row = orm.MemsL1Episodic()
        episodic_to_orm(memory, row)
        self._session.add(row)
        self._session.flush()
        memory.id = row.id
        return row.id

    def save(self, memory: EpisodicMemory) -> None:
        row = self._session.get(orm.MemsL1Episodic, memory.id)
        if row is None:
            raise ValueError(f"Episodic memory {memory.id} not found")
        episodic_to_orm(memory, row)
        self._session.add(row)
        self._session.flush()

    def get_by_vector_ids(
        self,
        agent_id: str,
        vector_ids: list[str],
        identity: Optional[MemoryIdentity] = None,
    ) -> list[EpisodicMemory]:
        if not vector_ids:
            return []
        query = select(orm.MemsL1Episodic).where(
            orm.MemsL1Episodic.agent_id == agent_id,
            orm.MemsL1Episodic.vector_id.in_(vector_ids),  # type: ignore[attr-defined]
            orm.MemsL1Episodic.is_archived == False,  # noqa: E712
        )
        query = apply_identity_filter(query, orm.MemsL1Episodic, identity)
        rows = self._session.exec(query).all()
        return [episodic_from_orm(row) for row in rows]

    def find_for_distill(
        self,
        agent_id: str,
        batch_size: int,
        force: bool,
        min_importance: float,
    ) -> list[EpisodicMemory]:
        query = select(orm.MemsL1Episodic).where(
            orm.MemsL1Episodic.agent_id == agent_id
        )
        if not force:
            query = query.where(
                orm.MemsL1Episodic.is_distilled == False,  # noqa: E712
                orm.MemsL1Episodic.importance_score >= min_importance,
            )
            query = query.order_by(
                orm.MemsL1Episodic.importance_score.desc(),  # type: ignore[attr-defined]
                orm.MemsL1Episodic.created_at.asc(),  # type: ignore[attr-defined]
            )
        else:
            query = query.order_by(
                orm.MemsL1Episodic.created_at.asc()  # type: ignore[attr-defined]
            )
        rows = self._session.exec(query.limit(batch_size)).all()
        return [episodic_from_orm(row) for row in rows]

    def list_expired(
        self,
        agent_id: str,
        tenant_id: Optional[str],
        user_id: Optional[str],
        namespace: Optional[str],
        cutoff: datetime,
    ) -> list[EpisodicMemory]:
        query = select(orm.MemsL1Episodic).where(
            orm.MemsL1Episodic.agent_id == agent_id,
            orm.MemsL1Episodic.created_at < cutoff,
            orm.MemsL1Episodic.is_archived == False,  # noqa: E712
        )
        if tenant_id is not None:
            query = query.where(orm.MemsL1Episodic.tenant_id == tenant_id)
        if user_id is not None:
            query = query.where(orm.MemsL1Episodic.user_id == user_id)
        if namespace is not None:
            query = query.where(orm.MemsL1Episodic.namespace == namespace)
        rows = self._session.exec(query).all()
        return [episodic_from_orm(row) for row in rows]

    def list_archive_identities(
        self, cutoff: datetime
    ) -> list[tuple[Optional[str], Optional[str], str, Optional[str]]]:
        rows = self._session.exec(
            select(
                orm.MemsL1Episodic.tenant_id,
                orm.MemsL1Episodic.user_id,
                orm.MemsL1Episodic.agent_id,
                orm.MemsL1Episodic.namespace,
            )
            .where(
                orm.MemsL1Episodic.created_at < cutoff,
                orm.MemsL1Episodic.is_archived == False,  # noqa: E712
            )
            .distinct()
        ).all()
        return [(row[0], row[1], row[2], row[3]) for row in rows]  # type: ignore[return-value]

    def count_pending_distill(self) -> int:
        statement = select(orm.MemsL1Episodic).where(
            orm.MemsL1Episodic.is_distilled == False  # noqa: E712
        )
        return self._count(statement)

    def count_pending_archive(self, cutoff: datetime) -> int:
        statement = select(orm.MemsL1Episodic).where(
            orm.MemsL1Episodic.created_at < cutoff,
            orm.MemsL1Episodic.is_archived == False,  # noqa: E712
        )
        return self._count(statement)

    def list_agents_with_pending_distill(self) -> list[str]:
        rows = self._session.exec(
            select(orm.MemsL1Episodic.agent_id)
            .where(orm.MemsL1Episodic.is_distilled == False)  # noqa: E712
            .distinct()
        ).all()
        return [row for row in rows if row]

    def healthcheck(self) -> None:
        self._session.exec(select(orm.MemsL1Episodic).limit(1)).all()

    def _count(self, statement) -> int:
        from sqlalchemy import func

        return int(
            self._session.exec(
                select(func.count()).select_from(statement.subquery())
            ).one()
        )
