"""Mappings between domain aggregates/entities and SQLModel rows."""

from __future__ import annotations

from mems.domain.archive import ArchiveBatch
from mems.domain.knowledge import ConflictLog, Event, Fact, Profile, Summary
from mems.domain.memory import EpisodicMemory
from mems.domain.outbox import OutboxEvent, OutboxEventType
from mems.infrastructure.persistence import models as orm


def episodic_from_orm(row: orm.MemsL1Episodic) -> EpisodicMemory:
    return EpisodicMemory(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        session_id=row.session_id,
        namespace=row.namespace,
        content=row.content,
        messages=list(row.messages_json or []),
        vector_id=row.vector_id,
        importance_score=row.importance_score,
        is_distilled=row.is_distilled,
        is_archived=row.is_archived,
        vector_status=row.vector_status,
        archive_status=row.archive_status,
        last_sync_error=row.last_sync_error,
        last_sync_at=row.last_sync_at,
        created_at=row.created_at,
    )


def episodic_to_orm(
    memory: EpisodicMemory, row: orm.MemsL1Episodic
) -> orm.MemsL1Episodic:
    row.tenant_id = memory.tenant_id
    row.user_id = memory.user_id
    row.agent_id = memory.agent_id
    row.session_id = memory.session_id
    row.namespace = memory.namespace
    row.content = memory.content
    row.messages_json = list(memory.messages)
    row.vector_id = memory.vector_id
    row.importance_score = memory.importance_score
    row.is_distilled = memory.is_distilled
    row.is_archived = memory.is_archived
    row.vector_status = memory.vector_status
    row.archive_status = memory.archive_status
    row.last_sync_error = memory.last_sync_error
    row.last_sync_at = memory.last_sync_at
    row.created_at = memory.created_at
    return row


def profile_from_orm(row: orm.MemsL2ProfileItem) -> Profile:
    return Profile(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        namespace=row.namespace,
        category=row.category,
        key=row.key,
        value=row.value,
        confidence=row.confidence,
        status=row.status,
        source_l1_ids=list(row.source_l1_ids or []),
        supersedes_id=row.supersedes_id,
        version=row.version,
        first_seen_at=row.first_seen_at,
        last_verified_at=row.last_verified_at,
    )


def profile_to_orm(profile: Profile, row: orm.MemsL2ProfileItem) -> orm.MemsL2ProfileItem:
    row.tenant_id = profile.tenant_id
    row.user_id = profile.user_id
    row.agent_id = profile.agent_id
    row.namespace = profile.namespace
    row.category = profile.category
    row.key = profile.key
    row.value = profile.value
    row.confidence = profile.confidence
    row.status = profile.status
    row.source_l1_ids = list(profile.source_l1_ids)
    row.supersedes_id = profile.supersedes_id
    row.version = profile.version
    row.first_seen_at = profile.first_seen_at
    row.last_verified_at = profile.last_verified_at
    return row


def fact_from_orm(row: orm.MemsL2Fact) -> Fact:
    return Fact(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        namespace=row.namespace,
        subject=row.subject,
        predicate=row.predicate,
        object=row.object,
        fact_type=row.fact_type,
        confidence=row.confidence,
        status=row.status,
        source_l1_ids=list(row.source_l1_ids or []),
        supersedes_id=row.supersedes_id,
        version=row.version,
        first_seen_at=row.first_seen_at,
        last_verified_at=row.last_verified_at,
    )


def fact_to_orm(fact: Fact, row: orm.MemsL2Fact) -> orm.MemsL2Fact:
    row.tenant_id = fact.tenant_id
    row.user_id = fact.user_id
    row.agent_id = fact.agent_id
    row.namespace = fact.namespace
    row.subject = fact.subject
    row.predicate = fact.predicate
    row.object = fact.object
    row.fact_type = fact.fact_type
    row.confidence = fact.confidence
    row.status = fact.status
    row.source_l1_ids = list(fact.source_l1_ids)
    row.supersedes_id = fact.supersedes_id
    row.version = fact.version
    row.first_seen_at = fact.first_seen_at
    row.last_verified_at = fact.last_verified_at
    return row


def event_from_orm(row: orm.MemsL2Event) -> Event:
    return Event(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        namespace=row.namespace,
        subject=row.subject,
        action=row.action,
        object=row.object,
        time_hint=row.time_hint,
        importance_score=row.importance_score,
        source_l1_ids=list(row.source_l1_ids or []),
        created_at=row.created_at,
    )


def event_to_orm(event: Event, row: orm.MemsL2Event) -> orm.MemsL2Event:
    row.tenant_id = event.tenant_id
    row.user_id = event.user_id
    row.agent_id = event.agent_id
    row.namespace = event.namespace
    row.subject = event.subject
    row.action = event.action
    row.object = event.object
    row.time_hint = event.time_hint
    row.importance_score = event.importance_score
    row.source_l1_ids = list(event.source_l1_ids)
    row.created_at = event.created_at
    return row


def summary_from_orm(row: orm.MemsL2Summary) -> Summary:
    return Summary(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        namespace=row.namespace,
        summary_type=row.summary_type,
        content=row.content,
        vector_id=row.vector_id,
        vector_status=row.vector_status,
        last_sync_error=row.last_sync_error,
        last_sync_at=row.last_sync_at,
        source_l1_ids=list(row.source_l1_ids or []),
        created_at=row.created_at,
        last_verified_at=row.last_verified_at,
    )


def summary_to_orm(summary: Summary, row: orm.MemsL2Summary) -> orm.MemsL2Summary:
    row.tenant_id = summary.tenant_id
    row.user_id = summary.user_id
    row.agent_id = summary.agent_id
    row.namespace = summary.namespace
    row.summary_type = summary.summary_type
    row.content = summary.content
    row.vector_id = summary.vector_id
    row.vector_status = summary.vector_status
    row.last_sync_error = summary.last_sync_error
    row.last_sync_at = summary.last_sync_at
    row.source_l1_ids = list(summary.source_l1_ids)
    row.created_at = summary.created_at
    row.last_verified_at = summary.last_verified_at
    return row


def conflict_from_orm(row: orm.MemsL2ConflictLog) -> ConflictLog:
    return ConflictLog(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        namespace=row.namespace,
        memory_type=row.memory_type,
        old_value=row.old_value,
        new_value=row.new_value,
        resolution=row.resolution,
        reason=row.reason,
        source_l1_ids=list(row.source_l1_ids or []),
        created_at=row.created_at,
    )


def conflict_to_orm(
    conflict: ConflictLog, row: orm.MemsL2ConflictLog
) -> orm.MemsL2ConflictLog:
    row.tenant_id = conflict.tenant_id
    row.user_id = conflict.user_id
    row.agent_id = conflict.agent_id
    row.namespace = conflict.namespace
    row.memory_type = conflict.memory_type
    row.old_value = conflict.old_value
    row.new_value = conflict.new_value
    row.resolution = conflict.resolution
    row.reason = conflict.reason
    row.source_l1_ids = list(conflict.source_l1_ids)
    row.created_at = conflict.created_at
    return row


def archive_from_orm(row: orm.MemsL3Archive) -> ArchiveBatch:
    return ArchiveBatch(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        namespace=row.namespace,
        time_period=row.time_period,
        summary_text=row.summary_text,
        file_path=row.file_path,
        record_count=row.record_count,
        archived_at=row.archived_at,
    )


def archive_to_orm(
    batch: ArchiveBatch, row: orm.MemsL3Archive
) -> orm.MemsL3Archive:
    row.tenant_id = batch.tenant_id
    row.user_id = batch.user_id
    row.agent_id = batch.agent_id
    row.namespace = batch.namespace
    row.time_period = batch.time_period
    row.summary_text = batch.summary_text
    row.file_path = batch.file_path
    row.record_count = batch.record_count
    row.archived_at = batch.archived_at
    return row


def outbox_from_orm(row: orm.MemsOutbox) -> OutboxEvent:
    return OutboxEvent(
        id=row.id,
        event_type=OutboxEventType(row.event_type),
        aggregate_id=row.aggregate_id,
        payload=dict(row.payload or {}),
        status=row.status,
        attempts=row.attempts,
        owner=row.owner,
        last_error=row.last_error,
        available_at=row.available_at,
        created_at=row.created_at,
    )


def outbox_to_orm(event: OutboxEvent, row: orm.MemsOutbox) -> orm.MemsOutbox:
    row.event_type = event.event_type
    row.aggregate_id = event.aggregate_id
    row.payload = dict(event.payload)
    row.status = event.status
    row.attempts = event.attempts
    row.owner = event.owner
    row.last_error = event.last_error
    row.available_at = event.available_at
    row.created_at = event.created_at
    return row
