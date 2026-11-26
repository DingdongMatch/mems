"""L2 knowledge repository."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import select

from mems.domain.identity import MemoryIdentity
from mems.domain.knowledge import ConflictLog, Event, Fact, Profile, Summary
from mems.infrastructure.persistence import models
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

orm = cast(Any, models)


class SqlKnowledgeRepository(SqlRepository):
    """SQLModel-backed L2 knowledge repository."""

    def source_created_at(
        self, identity: MemoryIdentity, source_ids: list[int]
    ) -> datetime | None:
        if not source_ids:
            return None
        rows = self._session.exec(
            select(orm.MemsL1Episodic.id, orm.MemsL1Episodic.created_at).where(
                orm.MemsL1Episodic.id.in_(source_ids),
                orm.MemsL1Episodic.tenant_id.is_not_distinct_from(identity.tenant_id),
                orm.MemsL1Episodic.user_id.is_not_distinct_from(identity.user_id),
                orm.MemsL1Episodic.agent_id == identity.agent_id,
                orm.MemsL1Episodic.namespace == identity.namespace_key,
            )
        ).all()
        # Archive-only evidence uses the source dates persisted on the L2 entity.
        if len(rows) != len(set(source_ids)):
            return None
        return max(row[1] for row in rows)

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
        query = (
            select(orm.MemsL2Summary)
            .where(
                orm.MemsL2Summary.tenant_id == identity.tenant_id,
                orm.MemsL2Summary.user_id == identity.user_id,
                orm.MemsL2Summary.agent_id == identity.agent_id,
                orm.MemsL2Summary.namespace == identity.namespace_key,
            )
            .order_by(
                orm.MemsL2Summary.created_at.asc(),  # type: ignore[attr-defined]
                orm.MemsL2Summary.id.asc(),  # type: ignore[union-attr]
            )
        )
        rows = self._session.exec(query).all()
        return [summary_from_orm(row) for row in rows]

    def list_profiles_for_query(self, identity: MemoryIdentity, limit: int) -> list[Profile]:
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

    def list_facts_for_query(self, identity: MemoryIdentity, limit: int) -> list[Fact]:
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

    def list_events_for_query(self, identity: MemoryIdentity, limit: int) -> list[Event]:
        query = select(orm.MemsL2Event).where(orm.MemsL2Event.agent_id == identity.agent_id)
        query = self._apply_identity_filter(query, orm.MemsL2Event, identity)
        query = query.order_by(
            orm.MemsL2Event.created_at.desc()  # type: ignore[attr-defined]
        ).limit(limit)
        rows = self._session.exec(query).all()
        return [event_from_orm(row) for row in rows]

    def list_summaries_for_query(self, identity: MemoryIdentity, limit: int) -> list[Summary]:
        query = select(orm.MemsL2Summary).where(orm.MemsL2Summary.agent_id == identity.agent_id)
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

    def lock_fact_scope(
        self, identity: MemoryIdentity, subject: str, predicate: str
    ) -> None:
        lock_key = json.dumps(
            {
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
                "agent_id": identity.agent_id,
                "namespace": identity.namespace_key,
                "subject": subject,
                "predicate": predicate,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )

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
        if event.semantic_hash is None:
            raise ValueError("Event semantic_hash is required")

        row = event_to_orm(event, orm.MemsL2Event())
        values = row.model_dump(exclude={"id"})
        self._session.exec(pg_insert(orm.MemsL2Event).values(**values).on_conflict_do_nothing())

        canonical = self._session.exec(
            select(orm.MemsL2Event)
            .where(
                orm.MemsL2Event.tenant_id == event.tenant_id,
                orm.MemsL2Event.user_id == event.user_id,
                orm.MemsL2Event.agent_id == event.agent_id,
                orm.MemsL2Event.namespace == event.namespace,
                orm.MemsL2Event.semantic_hash == event.semantic_hash,
            )
            .with_for_update(of=orm.MemsL2Event)
            .execution_options(populate_existing=True)
        ).one()
        merged_sources = list(
            dict.fromkeys([*(canonical.source_l1_ids or []), *event.source_l1_ids])
        )
        if merged_sources != canonical.source_l1_ids:
            canonical.source_l1_ids = merged_sources
            self._session.add(canonical)
            self._session.flush()
        event.id = canonical.id
        event.source_l1_ids = merged_sources
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
            select(orm.MemsL2ProfileItem).where(orm.MemsL2ProfileItem.status == "active")
        )

    def count_active_facts(self) -> int:
        return self._count(select(orm.MemsL2Fact).where(orm.MemsL2Fact.status == "active"))

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
            select(orm.MemsL2Summary).where(orm.MemsL2Summary.last_verified_at < cutoff)
        )

    def _count(self, statement) -> int:
        from sqlalchemy import func

        return int(self._session.exec(select(func.count()).select_from(statement.subquery())).one())

    def _apply_identity_filter(self, query, model, identity: MemoryIdentity):
        from mems.infrastructure.persistence.repositories.base import (
            apply_identity_filter,
        )

        return apply_identity_filter(query, model, identity)
