"""L2 knowledge repository."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import select

from mems.domain.identity import MemoryIdentity
from mems.domain.knowledge import ConflictLog, Event, Fact, Profile, Summary
from mems.infrastructure.persistence import models as orm
from mems.infrastructure.persistence.mappers import (
    conflict_to_orm,
    event_from_orm,
    event_to_orm,
    fact_from_orm,
    fact_to_orm,
    profile_from_orm,
    profile_to_orm,
    summary_from_orm,
    summary_to_orm,
)
from mems.infrastructure.persistence.repositories.base import SqlRepository


class SqlKnowledgeRepository(SqlRepository):
    """SQLModel-backed L2 knowledge repository."""

    def list_active_profiles(self, identity: MemoryIdentity) -> list[Profile]:
        query = select(orm.MemsL2ProfileItem).where(
            orm.MemsL2ProfileItem.tenant_id == identity.tenant_id,
            orm.MemsL2ProfileItem.user_id == identity.user_id,
            orm.MemsL2ProfileItem.agent_id == identity.agent_id,
            orm.MemsL2ProfileItem.namespace == identity.namespace_key,
            orm.MemsL2ProfileItem.status == "active",
        )
        rows = self._session.exec(query).all()
        return [profile_from_orm(row) for row in rows]

    def list_active_facts(self, identity: MemoryIdentity) -> list[Fact]:
        query = select(orm.MemsL2Fact).where(
            orm.MemsL2Fact.tenant_id == identity.tenant_id,
            orm.MemsL2Fact.user_id == identity.user_id,
            orm.MemsL2Fact.agent_id == identity.agent_id,
            orm.MemsL2Fact.namespace == identity.namespace_key,
            orm.MemsL2Fact.status == "active",
        )
        rows = self._session.exec(query).all()
        return [fact_from_orm(row) for row in rows]

    def list_summaries(self, identity: MemoryIdentity) -> list[Summary]:
        query = select(orm.MemsL2Summary).where(
            orm.MemsL2Summary.tenant_id == identity.tenant_id,
            orm.MemsL2Summary.user_id == identity.user_id,
            orm.MemsL2Summary.agent_id == identity.agent_id,
            orm.MemsL2Summary.namespace == identity.namespace_key,
        )
        rows = self._session.exec(query).all()
        return [summary_from_orm(row) for row in rows]

    def list_profiles_for_query(
        self, identity: MemoryIdentity, limit: int
    ) -> list[Profile]:
        query = select(orm.MemsL2ProfileItem).where(
            orm.MemsL2ProfileItem.agent_id == identity.agent_id,
            orm.MemsL2ProfileItem.status == "active",
        )
        query = self._apply_identity_filter(query, orm.MemsL2ProfileItem, identity)
        query = query.order_by(
            orm.MemsL2ProfileItem.last_verified_at.desc()  # type: ignore[attr-defined]
        ).limit(limit)
        rows = self._session.exec(query).all()
        return [profile_from_orm(row) for row in rows]

    def list_facts_for_query(
        self, identity: MemoryIdentity, limit: int
    ) -> list[Fact]:
        query = select(orm.MemsL2Fact).where(
            orm.MemsL2Fact.agent_id == identity.agent_id,
            orm.MemsL2Fact.status == "active",
        )
        query = self._apply_identity_filter(query, orm.MemsL2Fact, identity)
        query = query.order_by(
            orm.MemsL2Fact.last_verified_at.desc()  # type: ignore[attr-defined]
        ).limit(limit)
        rows = self._session.exec(query).all()
        return [fact_from_orm(row) for row in rows]

    def list_events_for_query(
        self, identity: MemoryIdentity, limit: int
    ) -> list[Event]:
        query = select(orm.MemsL2Event).where(
            orm.MemsL2Event.agent_id == identity.agent_id
        )
        query = self._apply_identity_filter(query, orm.MemsL2Event, identity)
        query = query.order_by(
            orm.MemsL2Event.created_at.desc()  # type: ignore[attr-defined]
        ).limit(limit)
        rows = self._session.exec(query).all()
        return [event_from_orm(row) for row in rows]

    def list_summaries_for_query(
        self, identity: MemoryIdentity, limit: int
    ) -> list[Summary]:
        query = select(orm.MemsL2Summary).where(
            orm.MemsL2Summary.agent_id == identity.agent_id
        )
        query = self._apply_identity_filter(query, orm.MemsL2Summary, identity)
        query = query.order_by(
            orm.MemsL2Summary.last_verified_at.desc()  # type: ignore[attr-defined]
        ).limit(limit)
        rows = self._session.exec(query).all()
        return [summary_from_orm(row) for row in rows]

    def get_active_profile(
        self, identity: MemoryIdentity, category: str, key: str
    ) -> Profile | None:
        row = self._session.exec(
            select(orm.MemsL2ProfileItem).where(
                orm.MemsL2ProfileItem.tenant_id == identity.tenant_id,
                orm.MemsL2ProfileItem.user_id == identity.user_id,
                orm.MemsL2ProfileItem.agent_id == identity.agent_id,
                orm.MemsL2ProfileItem.namespace == identity.namespace_key,
                orm.MemsL2ProfileItem.category == category,
                orm.MemsL2ProfileItem.key == key,
                orm.MemsL2ProfileItem.status == "active",
            )
        ).first()
        return profile_from_orm(row) if row else None

    def get_exact_fact(
        self,
        identity: MemoryIdentity,
        subject: str,
        predicate: str,
        object: str,
    ) -> Fact | None:
        row = self._session.exec(
            select(orm.MemsL2Fact).where(
                orm.MemsL2Fact.tenant_id == identity.tenant_id,
                orm.MemsL2Fact.user_id == identity.user_id,
                orm.MemsL2Fact.agent_id == identity.agent_id,
                orm.MemsL2Fact.namespace == identity.namespace_key,
                orm.MemsL2Fact.subject == subject,
                orm.MemsL2Fact.predicate == predicate,
                orm.MemsL2Fact.object == object,
                orm.MemsL2Fact.status == "active",
            )
        ).first()
        return fact_from_orm(row) if row else None

    def get_conflicting_facts(
        self, identity: MemoryIdentity, subject: str, predicate: str
    ) -> list[Fact]:
        rows = self._session.exec(
            select(orm.MemsL2Fact).where(
                orm.MemsL2Fact.tenant_id == identity.tenant_id,
                orm.MemsL2Fact.user_id == identity.user_id,
                orm.MemsL2Fact.agent_id == identity.agent_id,
                orm.MemsL2Fact.namespace == identity.namespace_key,
                orm.MemsL2Fact.subject == subject,
                orm.MemsL2Fact.predicate == predicate,
                orm.MemsL2Fact.status == "active",
            )
        ).all()
        return [fact_from_orm(row) for row in rows]

    def save_profile(self, profile: Profile) -> Profile:
        if profile.id is None:
            row = orm.MemsL2ProfileItem()
            profile_to_orm(profile, row)
            self._session.add(row)
            self._session.flush()
            profile.id = row.id
        else:
            existing = self._session.get(orm.MemsL2ProfileItem, profile.id)
            if existing is None:
                raise ValueError(f"Profile {profile.id} not found")
            profile_to_orm(profile, existing)
            self._session.add(existing)
            self._session.flush()
        return profile

    def save_fact(self, fact: Fact) -> Fact:
        if fact.id is None:
            row = orm.MemsL2Fact()
            fact_to_orm(fact, row)
            self._session.add(row)
            self._session.flush()
            fact.id = row.id
        else:
            existing = self._session.get(orm.MemsL2Fact, fact.id)
            if existing is None:
                raise ValueError(f"Fact {fact.id} not found")
            fact_to_orm(fact, existing)
            self._session.add(existing)
            self._session.flush()
        return fact

    def add_event(self, event: Event) -> Event:
        row = orm.MemsL2Event()
        event_to_orm(event, row)
        self._session.add(row)
        self._session.flush()
        event.id = row.id
        return event

    def add_conflict(self, conflict: ConflictLog) -> ConflictLog:
        row = orm.MemsL2ConflictLog()
        conflict_to_orm(conflict, row)
        self._session.add(row)
        self._session.flush()
        conflict.id = row.id
        return conflict

    def save_summary(self, summary: Summary) -> Summary:
        if summary.id is None:
            row = orm.MemsL2Summary()
            summary_to_orm(summary, row)
            self._session.add(row)
            self._session.flush()
            summary.id = row.id
        else:
            existing = self._session.get(orm.MemsL2Summary, summary.id)
            if existing is None:
                raise ValueError(f"Summary {summary.id} not found")
            summary_to_orm(summary, existing)
            self._session.add(existing)
            self._session.flush()
        return summary

    def count_active_profiles(self) -> int:
        return self._count(
            select(orm.MemsL2ProfileItem).where(
                orm.MemsL2ProfileItem.status == "active"
            )
        )

    def count_active_facts(self) -> int:
        return self._count(
            select(orm.MemsL2Fact).where(orm.MemsL2Fact.status == "active")
        )

    def count_summaries(self) -> int:
        return self._count(select(orm.MemsL2Summary))

    def count_conflicts(self) -> int:
        return self._count(select(orm.MemsL2ConflictLog))

    def count_stale_profiles(self, cutoff: datetime) -> int:
        return self._count(
            select(orm.MemsL2ProfileItem).where(
                orm.MemsL2ProfileItem.status == "active",
                orm.MemsL2ProfileItem.last_verified_at < cutoff,
            )
        )

    def count_stale_facts(self, cutoff: datetime) -> int:
        return self._count(
            select(orm.MemsL2Fact).where(
                orm.MemsL2Fact.status == "active",
                orm.MemsL2Fact.last_verified_at < cutoff,
            )
        )

    def count_stale_summaries(self, cutoff: datetime) -> int:
        return self._count(
            select(orm.MemsL2Summary).where(
                orm.MemsL2Summary.last_verified_at < cutoff
            )
        )

    def _count(self, statement) -> int:
        from sqlalchemy import func

        return int(
            self._session.exec(
                select(func.count()).select_from(statement.subquery())
            ).one()
        )

    def _apply_identity_filter(self, query, model, identity: MemoryIdentity):
        from mems.infrastructure.persistence.repositories.base import (
            apply_identity_filter,
        )

        return apply_identity_filter(query, model, identity)
