"""Transactional outbox repository."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional, cast

from sqlalchemy import case, func, or_, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import select

from mems.domain.outbox import OutboxEvent, OutboxEventType
from mems.infrastructure.persistence import models
from mems.infrastructure.persistence.mappers import (
    outbox_from_orm,
    outbox_to_orm,
)
from mems.infrastructure.persistence.repositories.base import SqlRepository

orm = cast(Any, models)


def _identity_predicates(
    event_type: str,
    aggregate_id: str,
    tenant_id: Optional[str],
    user_id: Optional[str],
    agent_id: Optional[str],
    namespace: Optional[str],
):
    return (
        orm.MemsOutbox.event_type == event_type,
        orm.MemsOutbox.aggregate_id == aggregate_id,
        orm.MemsOutbox.tenant_id.is_not_distinct_from(tenant_id),
        orm.MemsOutbox.user_id.is_not_distinct_from(user_id),
        orm.MemsOutbox.agent_id.is_not_distinct_from(agent_id),
        orm.MemsOutbox.namespace.is_not_distinct_from(namespace),
    )


class SqlOutboxRepository(SqlRepository):
    """SQLModel-backed outbox event repository."""

    def add(self, event: OutboxEvent) -> OutboxEvent:
        row = orm.MemsOutbox()
        outbox_to_orm(event, row)
        values = {
            column.name: getattr(row, column.name)
            for column in orm.MemsOutbox.__table__.columns
            if column.name != "id"
        }
        # A conflict waits for the competing transaction and then this SELECT sees
        # its active row. No IntegrityError is allowed to poison the UoW session.
        statement = insert(orm.MemsOutbox).values(**values)
        if event.event_type in {
            OutboxEventType.DISTILL_AGENT, OutboxEventType.DISTILL_DUE
        }:
            # A write racing with an already claimed trigger must wake it again.
            statement = statement.on_conflict_do_update(
                index_elements=[
                    "event_type", "aggregate_id", "tenant_id",
                    "user_id", "agent_id", "namespace",
                ],
                index_where=text("dead = false AND status IN ('pending', 'claimed')"),
                set_={"payload": orm.MemsOutbox.payload.op("||")({"_rerun": True})},
            )
        else:
            statement = statement.on_conflict_do_nothing()
        self._session.execute(statement)
        active = self._session.exec(
            select(orm.MemsOutbox).where(
                *_identity_predicates(
                    event.event_type,
                    event.aggregate_id,
                    event.tenant_id,
                    event.user_id,
                    event.agent_id,
                    event.namespace,
                ),
                orm.MemsOutbox.status.in_(["pending", "claimed"]),
                orm.MemsOutbox.dead.is_(False),
            ).execution_options(populate_existing=True)
        ).one()
        event.id = active.id
        event.status = active.status
        event.owner = active.owner
        return event

    def exists_active(
        self,
        event_type: str,
        aggregate_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> bool:
        row = self._session.exec(
            select(orm.MemsOutbox.id)
            .where(
                *_identity_predicates(
                    event_type, aggregate_id, tenant_id, user_id, agent_id, namespace
                ),
                orm.MemsOutbox.status.in_(["pending", "claimed"]),
                orm.MemsOutbox.dead.is_(False),
            )
            .limit(1)
        ).first()
        return row is not None

    def claim_ready(
        self,
        owner: str,
        limit: int,
        now: datetime,
        lease_seconds: int,
        max_attempts: int,
    ) -> list[OutboxEvent]:
        if limit <= 0:
            return []
        lease_expiry = now + timedelta(seconds=lease_seconds)
        ready = or_(
            orm.MemsOutbox.status == "pending",
            orm.MemsOutbox.status == "claimed",
        ) & (orm.MemsOutbox.available_at <= now)

        # Candidate locking and the state transition are one PostgreSQL statement.
        # SKIP LOCKED lets independent workers claim disjoint rows concurrently.
        candidate_ids = list(
            self._session.exec(
                select(orm.MemsOutbox.id)
                .where(ready, orm.MemsOutbox.dead.is_(False))
                .order_by(orm.MemsOutbox.id.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )
        if not candidate_ids:
            return []
        claimed_ids = list(
            self._session.execute(
                update(orm.MemsOutbox).execution_options(synchronize_session=False)
                .where(
                    orm.MemsOutbox.id.in_(candidate_ids),
                    ready,
                    orm.MemsOutbox.dead.is_(False),
                )
                .values(
                    status=case(
                        (orm.MemsOutbox.attempts >= max_attempts, "dead"),
                        else_="claimed",
                    ),
                    attempts=case(
                        (orm.MemsOutbox.attempts >= max_attempts, orm.MemsOutbox.attempts),
                        else_=orm.MemsOutbox.attempts + 1,
                    ),
                    owner=case(
                        (orm.MemsOutbox.attempts >= max_attempts, None),
                        else_=owner,
                    ),
                    dead=orm.MemsOutbox.attempts >= max_attempts,
                    dead_at=case(
                        (orm.MemsOutbox.attempts >= max_attempts, now),
                        else_=None,
                    ),
                    available_at=lease_expiry,
                    payload=orm.MemsOutbox.payload.op("-")("_rerun"),
                )
                .returning(orm.MemsOutbox.id)
            )
            .scalars()
            .all()
        )
        if not claimed_ids:
            return []

        rows = self._session.exec(
            select(orm.MemsOutbox)
            .where(
                orm.MemsOutbox.id.in_(claimed_ids),
                orm.MemsOutbox.status == "claimed",
                orm.MemsOutbox.owner == owner,
            )
            .order_by(orm.MemsOutbox.id.asc())
            .execution_options(populate_existing=True)
        ).all()
        return [outbox_from_orm(row) for row in rows]

    def count_recent_dead(self, cutoff: datetime) -> int:
        statement = select(orm.MemsOutbox).where(
            orm.MemsOutbox.dead.is_(True),
            orm.MemsOutbox.dead_at >= cutoff,
        )
        return int(self._session.exec(select(func.count()).select_from(statement.subquery())).one())

    def renew(
        self,
        event_id: int,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        result = self._session.execute(
            update(orm.MemsOutbox).execution_options(synchronize_session=False)
            .where(
                orm.MemsOutbox.id == event_id,
                orm.MemsOutbox.status == "claimed",
                orm.MemsOutbox.owner == owner,
                orm.MemsOutbox.dead.is_(False),
            )
            .values(available_at=now + timedelta(seconds=lease_seconds))
        )
        return cast(Any, result).rowcount == 1

    def request_rerun(self, event_id: int, owner: str) -> bool:
        result = self._session.execute(
            update(orm.MemsOutbox).execution_options(synchronize_session=False)
            .where(
                orm.MemsOutbox.id == event_id,
                orm.MemsOutbox.status == "claimed",
                orm.MemsOutbox.owner == owner,
                orm.MemsOutbox.dead.is_(False),
            )
            .values(payload=orm.MemsOutbox.payload.op("||")({"_rerun": True}))
        )
        return cast(Any, result).rowcount == 1

    def mark_done(self, event_id: int, owner: str) -> bool:
        rerun = orm.MemsOutbox.payload["_rerun"].as_boolean().is_(True)
        result = self._session.execute(
            update(orm.MemsOutbox).execution_options(synchronize_session=False)
            .where(
                orm.MemsOutbox.id == event_id,
                orm.MemsOutbox.status == "claimed",
                orm.MemsOutbox.owner == owner,
                orm.MemsOutbox.dead.is_(False),
            )
            .values(
                status=case((rerun, "pending"), else_="done"),
                attempts=case((rerun, 0), else_=orm.MemsOutbox.attempts),
                available_at=case((rerun, func.now()), else_=orm.MemsOutbox.available_at),
                payload=orm.MemsOutbox.payload.op("-")("_rerun"),
                owner=None,
                last_error=None,
            )
        )
        return cast(Any, result).rowcount == 1

    def mark_failed(
        self,
        event_id: int,
        owner: str,
        error: str,
        available_at: datetime,
        max_attempts: int,
    ) -> bool:
        exhausted = orm.MemsOutbox.attempts >= max_attempts
        result = self._session.execute(
            update(orm.MemsOutbox).execution_options(synchronize_session=False)
            .where(
                orm.MemsOutbox.id == event_id,
                orm.MemsOutbox.status == "claimed",
                orm.MemsOutbox.owner == owner,
                orm.MemsOutbox.dead.is_(False),
            )
            .values(
                status=case((exhausted, "dead"), else_="pending"),
                owner=None,
                last_error=error,
                dead=exhausted,
                dead_at=case((exhausted, func.now()), else_=None),
                available_at=available_at,
            )
        )
        return cast(Any, result).rowcount == 1
