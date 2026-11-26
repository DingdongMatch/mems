"""L3 archive metadata repository."""

from __future__ import annotations

from typing import Any, Optional, cast

from sqlmodel import select

from mems.domain.archive import ArchiveBatch
from mems.domain.identity import MemoryIdentity
from mems.infrastructure.persistence import models
from mems.infrastructure.persistence.mappers import (
    archive_from_orm,
    archive_to_orm,
)
from mems.infrastructure.persistence.repositories.base import SqlRepository

orm = cast(Any, models)


class SqlArchiveRepository(SqlRepository):
    """SQLModel-backed L3 archive metadata repository."""

    def add(self, batch: ArchiveBatch) -> ArchiveBatch:
        row = orm.MemsL3Archive()
        archive_to_orm(batch, row)
        self._session.add(row)
        self._session.flush()
        batch.id = row.id
        return batch

    def list(
        self,
        agent_id: Optional[str] = None,
        identity: Optional[MemoryIdentity] = None,
    ) -> list[ArchiveBatch]:
        query = select(orm.MemsL3Archive)
        if agent_id is not None:
            query = query.where(orm.MemsL3Archive.agent_id == agent_id)
        if identity is not None:
            query = query.where(
                orm.MemsL3Archive.tenant_id.is_not_distinct_from(identity.tenant_id),
                orm.MemsL3Archive.user_id.is_not_distinct_from(identity.user_id),
                orm.MemsL3Archive.agent_id == identity.agent_id,
                orm.MemsL3Archive.namespace.is_not_distinct_from(identity.namespace_key),
            )
        rows = self._session.exec(query).all()
        return [archive_from_orm(row) for row in rows]
