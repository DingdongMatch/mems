"""L3 archive metadata repository."""

from __future__ import annotations

from typing import Optional

from sqlmodel import select

from mems.domain.archive import ArchiveBatch
from mems.infrastructure.persistence import models as orm
from mems.infrastructure.persistence.mappers import (
    archive_from_orm,
    archive_to_orm,
)
from mems.infrastructure.persistence.repositories.base import SqlRepository


class SqlArchiveRepository(SqlRepository):
    """SQLModel-backed L3 archive metadata repository."""

    def add(self, batch: ArchiveBatch) -> ArchiveBatch:
        row = orm.MemsL3Archive()
        archive_to_orm(batch, row)
        self._session.add(row)
        self._session.flush()
        batch.id = row.id
        return batch

    def list(self, agent_id: Optional[str] = None) -> list[ArchiveBatch]:
        query = select(orm.MemsL3Archive)
        if agent_id:
            query = query.where(orm.MemsL3Archive.agent_id == agent_id)
        rows = self._session.exec(query).all()
        return [archive_from_orm(row) for row in rows]
