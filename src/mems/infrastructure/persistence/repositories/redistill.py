"""Re-distillation progress repository."""

from __future__ import annotations

from typing import Optional

from sqlmodel import select

from mems.infrastructure.persistence import models as orm
from mems.infrastructure.persistence.repositories.base import SqlRepository


class SqlRedistillProgressRepository(SqlRepository):
    """SQLModel-backed re-distillation progress repository."""

    def exists(self, content_hash: str, model: str) -> bool:
        row = self._session.exec(
            select(orm.MemsRedistillProgress).where(
                orm.MemsRedistillProgress.content_hash == content_hash,
                orm.MemsRedistillProgress.model == model,
            )
        ).first()
        return row is not None

    def add(
        self,
        content_hash: str,
        model: str,
        l3_path: Optional[str],
        l3_line: Optional[int],
    ) -> None:
        row = orm.MemsRedistillProgress(
            content_hash=content_hash,
            model=model,
            l3_path=l3_path,
            l3_line=l3_line,
        )
        self._session.add(row)
        self._session.flush()
