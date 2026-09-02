"""Transactional outbox repository."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import update
from sqlmodel import select

from mems.domain.outbox import OutboxEvent
from mems.infrastructure.persistence import models as orm
from mems.infrastructure.persistence.mappers import (
    outbox_from_orm,
    outbox_to_orm,
)
from mems.infrastructure.persistence.repositories.base import SqlRepository


OUTBOX_CLAIM_LEASE_SECONDS = 300


class SqlOutboxRepository(SqlRepository):
    """SQLModel-backed outbox event repository."""

    def add(self, event: OutboxEvent) -> OutboxEvent:
        row = orm.MemsOutbox()
        outbox_to_orm(event, row)
        self._session.add(row)
        self._session.flush()
        event.id = row.id
        return event

    def exists_active(self, event_type: str, aggregate_id: str) -> bool:
        row = self._session.exec(
            select(orm.MemsOutbox.id)
            .where(
                orm.MemsOutbox.event_type == event_type,
                orm.MemsOutbox.aggregate_id == aggregate_id,
                orm.MemsOutbox.status.in_(["pending", "claimed"]),  # type: ignore[attr-defined]
            )
            .limit(1)
        ).first()
        return row is not None

    def claim_ready(
        self, owner: str, limit: int, now: datetime
    ) -> list[OutboxEvent]:
        lease_expiry = now + timedelta(seconds=OUTBOX_CLAIM_LEASE_SECONDS)

        self._session.exec(
            update(orm.MemsOutbox)
            .where(
                orm.MemsOutbox.status == "claimed",  # type: ignore[arg-type]
                orm.MemsOutbox.available_at <= now,  # type: ignore[arg-type]
            )
            .values(status="pending", owner=None)
        )
        self._session.flush()

        candidate_ids = list(
            self._session.exec(
                select(orm.MemsOutbox.id)
                .where(
                    orm.MemsOutbox.status == "pending",
                    orm.MemsOutbox.available_at <= now,
                )
                .order_by(orm.MemsOutbox.id.asc())  # type: ignore[union-attr]
                .limit(limit)
            ).all()
        )
        if not candidate_ids:
            return []

        self._session.exec(
            update(orm.MemsOutbox)
            .where(
                orm.MemsOutbox.id.in_(candidate_ids),  # type: ignore[union-attr]
                orm.MemsOutbox.status == "pending",  # type: ignore[arg-type]
            )
            .values(status="claimed", owner=owner, available_at=lease_expiry)
        )
        self._session.flush()

        rows = self._session.exec(
            select(orm.MemsOutbox)
            .where(
                orm.MemsOutbox.id.in_(candidate_ids),  # type: ignore[union-attr]
                orm.MemsOutbox.status == "claimed",
                orm.MemsOutbox.owner == owner,
            )
            .order_by(orm.MemsOutbox.id.asc())  # type: ignore[union-attr]
        ).all()
        return [outbox_from_orm(row) for row in rows]

    def mark_done(self, event_id: int) -> None:
        row = self._session.get(orm.MemsOutbox, event_id)
        if row is None:
            return
        row.status = "done"
        row.owner = None
        row.last_error = None
        self._session.add(row)
        self._session.flush()

    def mark_failed(
        self, event_id: int, error: str, available_at: datetime
    ) -> None:
        row = self._session.get(orm.MemsOutbox, event_id)
        if row is None:
            return
        row.status = "pending"
        row.attempts += 1
        row.owner = None
        row.last_error = error
        row.available_at = available_at
        self._session.add(row)
        self._session.flush()
