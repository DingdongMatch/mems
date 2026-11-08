"""L2 knowledge reconciliation domain service.

画像、事实与事件的对账规则。
"""

from __future__ import annotations

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
    ) -> tuple[int, int]:
        """Merge one extracted profile item into L2 profile storage."""
        existing = self._repository.get_active_profile(
            identity, update.category, update.key
        )
        now = self._clock.now()

        if existing and existing.value == update.value:
            existing.last_verified_at = now
            existing.confidence = max(existing.confidence, update.confidence)
            existing.source_l1_ids = self._append_source(
                existing.source_l1_ids, source_id
            )
            self._repository.save_profile(existing)
            return 0, 1

        if existing and existing.value != update.value:
            existing.status = "superseded"
            existing.last_verified_at = now
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
        )
        self._repository.save_profile(new_item)
        return 1, 0

    def reconcile_fact(
        self,
        identity: MemoryIdentity,
        fact: DistillFactItem,
        source_id: int,
    ) -> tuple[int, int]:
        """Merge one extracted fact into L2 fact storage."""
        existing_exact = self._repository.get_exact_fact(
            identity, fact.subject, fact.relation, fact.object
        )
        now = self._clock.now()

        if existing_exact:
            existing_exact.last_verified_at = now
            existing_exact.confidence = max(existing_exact.confidence, fact.confidence)
            existing_exact.source_l1_ids = self._append_source(
                existing_exact.source_l1_ids, source_id
            )
            self._repository.save_fact(existing_exact)
            return 0, 1

        if fact.relation in MULTI_VALUE_PREDICATES:
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
            )
            self._repository.save_fact(new_item)
            return 1, 0

        conflicting = self._repository.get_conflicting_facts(
            identity, fact.subject, fact.relation
        )
        for existing in conflicting:
            if existing.object == fact.object:
                continue
            existing.status = "superseded"
            existing.last_verified_at = now
            self._repository.save_fact(existing)
            self._new_conflict(
                identity,
                "fact",
                f"{existing.subject} {existing.predicate} {existing.object}",
                f"{fact.subject} {fact.relation} {fact.object}",
                "fact updated by newer memory",
                source_id,
            )
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
                supersedes_id=existing.id,
                version=existing.version + 1,
            )
            self._repository.save_fact(new_item)
            return 1, 1

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
        )
        self._repository.save_fact(new_item)
        return 1, 0

    def commit_event(
        self,
        identity: MemoryIdentity,
        event: DistillEventItem,
        source_id: int,
    ) -> int:
        """Persist one extracted event into L2 event storage."""
        self._repository.add_event(
            Event(
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
        )
        return 1
