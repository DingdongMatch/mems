"""Episodic memory repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, cast

from sqlalchemy import update
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import select

from mems.domain.identity import MemoryIdentity
from mems.domain.memory import EpisodicMemory
from mems.infrastructure.persistence import models
from mems.infrastructure.persistence.mappers import (
    episodic_from_orm,
    episodic_to_orm,
)
from mems.infrastructure.persistence.repositories.base import (
    SqlRepository,
    apply_identity_filter,
)

orm = cast(Any, models)


class SqlEpisodicRepository(SqlRepository):
    """SQLModel-backed L1 episodic repository."""

    def add(self, memory: EpisodicMemory) -> Optional[int]:
        row = orm.MemsL1Episodic()
        episodic_to_orm(memory, row)
        self._session.add(row)
        self._session.flush()
        memory.id = row.id
        if hasattr(memory, "version"):
            setattr(memory, "version", getattr(row, "version", 1))
        return row.id

    def save(self, memory: EpisodicMemory) -> None:
        row = self._session.get(orm.MemsL1Episodic, memory.id)
        if row is None:
            raise ValueError(f"Episodic memory {memory.id} not found")

        expected_version = getattr(memory, "version", getattr(row, "version", 1))
        candidate = episodic_to_orm(
            memory,
            orm.MemsL1Episodic(),  # type: ignore[call-arg]
        )
        values = candidate.model_dump(exclude={"id", "version"})
        result = self._session.exec(
            update(orm.MemsL1Episodic)
            .where(
                orm.MemsL1Episodic.id == memory.id,  # type: ignore[arg-type]
                orm.MemsL1Episodic.version == expected_version,  # type: ignore[arg-type]
            )
            .values(**values, version=expected_version + 1)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise StaleDataError(
                f"Episodic memory {memory.id} has stale version {expected_version}"
            )
        if hasattr(memory, "version"):
            setattr(memory, "version", expected_version + 1)
        self._session.expire(row)

    def get_by_vector_ids(
        self,
        agent_id: str,
        vector_ids: list[str],
        identity: Optional[MemoryIdentity] = None,
        *,
        for_update: bool = False,
    ) -> list[EpisodicMemory]:
        if not vector_ids:
            return []
        query = select(orm.MemsL1Episodic).where(
            orm.MemsL1Episodic.agent_id == agent_id,
            orm.MemsL1Episodic.vector_id.in_(vector_ids),  # type: ignore[attr-defined]
            orm.MemsL1Episodic.is_archived == False,  # noqa: E712
        )
        query = apply_identity_filter(query, orm.MemsL1Episodic, identity)
        if for_update:
            query = query.with_for_update(of=orm.MemsL1Episodic)
        rows = self._session.exec(query.execution_options(populate_existing=True)).all()
        return [episodic_from_orm(row) for row in rows]

    def find_for_distill(
        self,
        agent_id: str,
        batch_size: int,
        force: bool,
        min_importance: float,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> list[EpisodicMemory]:
        query = select(orm.MemsL1Episodic).where(
            orm.MemsL1Episodic.agent_id == agent_id,
            orm.MemsL1Episodic.tenant_id.is_not_distinct_from(tenant_id),
            orm.MemsL1Episodic.user_id.is_not_distinct_from(user_id),
            orm.MemsL1Episodic.namespace.is_not_distinct_from(namespace),
            orm.MemsL1Episodic.is_archived == False,  # noqa: E712
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
        query = query.with_for_update(skip_locked=True, of=orm.MemsL1Episodic).limit(batch_size)
        rows = self._session.exec(query).all()
        return [episodic_from_orm(row) for row in rows]

    def has_pending_for_distill(
        self,
        agent_id: str,
        force: bool,
        min_importance: float,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> bool:
        query = select(orm.MemsL1Episodic.id).where(
            orm.MemsL1Episodic.agent_id == agent_id,
            orm.MemsL1Episodic.tenant_id.is_not_distinct_from(tenant_id),
            orm.MemsL1Episodic.user_id.is_not_distinct_from(user_id),
            orm.MemsL1Episodic.namespace.is_not_distinct_from(namespace),
            orm.MemsL1Episodic.is_archived == False,  # noqa: E712
        )
        if not force:
            query = query.where(
                orm.MemsL1Episodic.is_distilled == False,  # noqa: E712
                orm.MemsL1Episodic.importance_score >= min_importance,
            )
        return self._session.exec(query.limit(1)).first() is not None

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
            orm.MemsL1Episodic.tenant_id.is_not_distinct_from(tenant_id),
            orm.MemsL1Episodic.user_id.is_not_distinct_from(user_id),
            orm.MemsL1Episodic.namespace.is_not_distinct_from(namespace),
            orm.MemsL1Episodic.created_at < cutoff,
            orm.MemsL1Episodic.is_archived == False,  # noqa: E712
        )
        rows = self._session.exec(
            query.with_for_update(skip_locked=True, of=orm.MemsL1Episodic)
        ).all()
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
            orm.MemsL1Episodic.is_distilled == False,  # noqa: E712
            orm.MemsL1Episodic.is_archived == False,  # noqa: E712
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
            .where(
                orm.MemsL1Episodic.is_distilled == False,  # noqa: E712
                orm.MemsL1Episodic.is_archived == False,  # noqa: E712
            )
            .distinct()
        ).all()
        return [row for row in rows if row]

    def list_pending_distill_identities(
        self,
    ) -> list[tuple[Optional[str], Optional[str], str, Optional[str]]]:
        rows = self._session.exec(
            select(
                orm.MemsL1Episodic.tenant_id,
                orm.MemsL1Episodic.user_id,
                orm.MemsL1Episodic.agent_id,
                orm.MemsL1Episodic.namespace,
            )
            .where(
                orm.MemsL1Episodic.is_distilled == False,  # noqa: E712
                orm.MemsL1Episodic.is_archived == False,  # noqa: E712
            )
            .distinct()
        ).all()
        return [(row[0], row[1], row[2], row[3]) for row in rows]  # type: ignore[return-value]

    def healthcheck(self) -> None:
        self._session.exec(select(orm.MemsL1Episodic).limit(1)).all()

    def _count(self, statement) -> int:
        from sqlalchemy import func

        return int(self._session.exec(select(func.count()).select_from(statement.subquery())).one())
