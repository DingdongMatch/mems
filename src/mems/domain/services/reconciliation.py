"""L2 knowledge reconciliation domain service.

画像、事实与事件的对账规则。
"""

from __future__ import annotations

from datetime import datetime, timezone

from mems.domain.identity import MemoryIdentity
from mems.domain.knowledge import (
    ConflictLog,
    DistillEventItem,
    DistillFactItem,
    DistillProfileUpdate,
    Event,
    Fact,
    Profile,
)
from mems.domain.ports import Clock, KnowledgeRepository
from mems.domain.services.prompts import MULTI_VALUE_PREDICATES


class ReconciliationService:
    """Merge extracted L2 candidates into durable knowledge."""

    def __init__(self, repository: KnowledgeRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def _source_time(self, identity: MemoryIdentity, item: Profile | Fact) -> datetime:
        # Prefer evidence dates for legacy knowledge written with processing time.
        evidence_time = self._repository.source_created_at(identity, item.source_l1_ids)
        return self._utc(evidence_time or item.last_verified_at)

    @staticmethod
    def _append_source(existing_ids: list[int], source_id: int) -> list[int]:
        merged = list(existing_ids)
        if source_id not in merged:
            merged.append(source_id)
        return merged

    def _new_conflict(
        self,
        identity: MemoryIdentity,
        memory_type: str,
        old_value: str,
        new_value: str,
        reason: str,
        source_id: int,
    ) -> None:
        self._repository.add_conflict(
            ConflictLog(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                namespace=identity.namespace_key,
                memory_type=memory_type,
                old_value=old_value,
                new_value=new_value,
                resolution="superseded",
                reason=reason,
                source_l1_ids=[source_id],
            )
        )

    def reconcile_profile(
        self,
        identity: MemoryIdentity,
        update: DistillProfileUpdate,
        source_id: int,
        source_created_at: datetime | None = None,
    ) -> tuple[int, int]:
        """Merge one extracted profile item into L2 profile storage."""
        existing = self._repository.get_active_profile(identity, update.category, update.key)
        now = self._utc(source_created_at or self._clock.now())

        if existing and existing.value == update.value:
            existing.last_verified_at = max(now, self._source_time(identity, existing))
            existing.confidence = max(existing.confidence, update.confidence)
            existing.source_l1_ids = self._append_source(existing.source_l1_ids, source_id)
            self._repository.save_profile(existing)
            return 0, 1

        if existing and now < self._source_time(identity, existing):
            return 0, 0

        if existing and existing.value != update.value:
            existing.status = "superseded"
            existing.last_verified_at = max(now, self._source_time(identity, existing))
            self._repository.save_profile(existing)
            self._new_conflict(
                identity,
                "profile",
                f"{existing.category}:{existing.key}={existing.value}",
                f"{update.category}:{update.key}={update.value}",
                "value updated by newer memory",
                source_id,
            )
            new_item = Profile(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                namespace=identity.namespace_key,
                category=update.category,
                key=update.key,
                value=update.value,
                confidence=update.confidence,
                source_l1_ids=[source_id],
                first_seen_at=now,
                last_verified_at=now,
                supersedes_id=existing.id,
                version=existing.version + 1,
            )
            self._repository.save_profile(new_item)
            return 1, 1

        new_item = Profile(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            namespace=identity.namespace_key,
            category=update.category,
            key=update.key,
            value=update.value,
            confidence=update.confidence,
            source_l1_ids=[source_id],
            first_seen_at=now,
            last_verified_at=now,
        )
        self._repository.save_profile(new_item)
        return 1, 0

    def reconcile_fact(
        self,
        identity: MemoryIdentity,
        fact: DistillFactItem,
        source_id: int,
        source_created_at: datetime | None = None,
    ) -> tuple[int, int]:
        """Merge one extracted fact into L2 fact storage."""
        if fact.relation not in MULTI_VALUE_PREDICATES:
            self._repository.lock_fact_scope(identity, fact.subject, fact.relation)
        existing_exact = self._repository.get_exact_fact(
            identity, fact.subject, fact.relation, fact.object
        )
        now = self._utc(source_created_at or self._clock.now())

        if fact.relation in MULTI_VALUE_PREDICATES:
            if existing_exact:
                existing_exact.last_verified_at = max(now, self._source_time(identity, existing_exact))
                existing_exact.confidence = max(existing_exact.confidence, fact.confidence)
                existing_exact.source_l1_ids = self._append_source(
                    existing_exact.source_l1_ids, source_id
                )
                self._repository.save_fact(existing_exact)
                return 0, 1
            new_item = Fact(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                namespace=identity.namespace_key,
                subject=fact.subject,
                predicate=fact.relation,
                object=fact.object,
                fact_type=fact.fact_type,
                confidence=fact.confidence,
                source_l1_ids=[source_id],
                first_seen_at=now,
                last_verified_at=now,
            )
            self._repository.save_fact(new_item)
            return 1, 0

        conflicting = self._repository.get_conflicting_facts(identity, fact.subject, fact.relation)
        other_values = [item for item in conflicting if item.object != fact.object]
        if not other_values:
            if existing_exact:
                existing_exact.last_verified_at = max(now, self._source_time(identity, existing_exact))
                existing_exact.confidence = max(existing_exact.confidence, fact.confidence)
                existing_exact.source_l1_ids = self._append_source(
                    existing_exact.source_l1_ids, source_id
                )
                self._repository.save_fact(existing_exact)
                return 0, 1
            new_item = Fact(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                namespace=identity.namespace_key,
                subject=fact.subject,
                predicate=fact.relation,
                object=fact.object,
                fact_type=fact.fact_type,
                confidence=fact.confidence,
                source_l1_ids=[source_id],
                first_seen_at=now,
                last_verified_at=now,
            )
            self._repository.save_fact(new_item)
            return 1, 0

        if any(now < self._source_time(identity, item) for item in conflicting):
            return 0, 0

        for existing in conflicting:
            existing.status = "superseded"
            existing.last_verified_at = max(now, self._source_time(identity, existing))
            self._repository.save_fact(existing)
            self._new_conflict(
                identity,
                "fact",
                f"{existing.subject} {existing.predicate} {existing.object}",
                f"{fact.subject} {fact.relation} {fact.object}",
                "fact updated by newer memory",
                source_id,
            )

        latest = max(
            conflicting,
            key=lambda item: (item.version, item.id if item.id is not None else 0),
        )
        source_ids = list(existing_exact.source_l1_ids) if existing_exact else []
        source_ids = self._append_source(source_ids, source_id)
        new_item = Fact(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            namespace=identity.namespace_key,
            subject=fact.subject,
            predicate=fact.relation,
            object=fact.object,
            fact_type=fact.fact_type,
            confidence=fact.confidence,
            source_l1_ids=source_ids,
            first_seen_at=now,
            last_verified_at=now,
            supersedes_id=latest.id,
            version=max(item.version for item in conflicting) + 1,
        )
        self._repository.save_fact(new_item)
        return 1, len(conflicting)

    def commit_event(
        self,
        identity: MemoryIdentity,
        event: DistillEventItem,
        source_id: int,
    ) -> int:
        """Persist one extracted event, merging replayed source provenance."""
        record = Event(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            namespace=identity.namespace_key,
            subject=event.subject,
            action=event.action,
            object=event.object,
            time_hint=event.time_hint,
            importance_score=event.importance,
            source_l1_ids=[source_id],
        )
        record.semantic_hash = record.compute_semantic_hash()
        self._repository.add_event(record)
        return 1
