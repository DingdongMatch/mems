"""SQLModel persistence models for the Mems memory layers.

PostgreSQL-first schema: JSON columns use JSONB, and hot query paths are
covered by composite / partial indexes instead of per-column indexes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class MemsL1Episodic(SQLModel, table=True):
    """Store raw episodic memory records persisted from live sessions."""

    __tablename__ = "mems_l1_episodic"
    __table_args__ = (
        Index(
            "ix_l1_distill",
            "agent_id",
            "is_distilled",
            text("importance_score DESC"),
            text("created_at ASC"),
            postgresql_where=text("is_distilled = false"),
        ),
        Index("ix_l1_agent_created", "agent_id", "created_at"),
        Index(
            "ix_l1_expired",
            "agent_id",
            "is_archived",
            "created_at",
            postgresql_where=text("is_archived = false"),
        ),
        Index(
            "ix_l1_pending_distill",
            "agent_id",
            postgresql_where=text("is_distilled = false"),
        ),
        Index(
            "ix_l1_pending_archive",
            "created_at",
            postgresql_where=text("is_archived = false"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    scope: Optional[str] = None
    content: str
    vector_id: str = Field(unique=True)
    importance_score: float = 0.0
    is_distilled: bool = False
    is_archived: bool = False
    vector_status: str = "pending"
    archive_status: str = "pending"
    last_sync_error: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    messages_json: List[Dict[str, str]] = Field(
        default_factory=list, sa_column=Column(JSONB)
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL2ProfileItem(SQLModel, table=True):
    """Store stable user profile items such as preferences and habits."""

    __tablename__ = "mems_l2_profile_item"
    __table_args__ = (
        Index(
            "ix_l2_profile_lookup",
            "tenant_id",
            "user_id",
            "agent_id",
            "scope",
            "category",
            "key",
            "status",
        ),
        Index(
            "ix_l2_profile_recall",
            "agent_id",
            "status",
            text("last_verified_at DESC"),
        ),
        Index(
            "ix_l2_profile_stale",
            "last_verified_at",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    scope: Optional[str] = None
    category: str
    key: str
    value: str
    confidence: float = 1.0
    status: str = "active"
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSONB))
    supersedes_id: Optional[int] = None
    version: int = 1
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemsL2Fact(SQLModel, table=True):
    """Store durable facts and entity relations in the L2 layer."""

    __tablename__ = "mems_l2_fact"
    __table_args__ = (
        Index(
            "ix_l2_fact_lookup",
            "tenant_id",
            "user_id",
            "agent_id",
            "scope",
            "subject",
            "predicate",
            "object",
            "status",
        ),
        Index(
            "ix_l2_fact_conflict",
            "tenant_id",
            "user_id",
            "agent_id",
            "scope",
            "subject",
            "predicate",
            "status",
        ),
        Index(
            "ix_l2_fact_recall",
            "agent_id",
            "status",
            text("last_verified_at DESC"),
        ),
        Index(
            "ix_l2_fact_stale",
            "last_verified_at",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    scope: Optional[str] = None
    subject: str
    predicate: str
    object: str
    fact_type: str = "general"
    confidence: float = 1.0
    status: str = "active"
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSONB))
    supersedes_id: Optional[int] = None
    version: int = 1
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemsL2Event(SQLModel, table=True):
    """Store important extracted events with time hints and sources."""

    __tablename__ = "mems_l2_event"
    __table_args__ = (
        Index("ix_l2_event_recall", "agent_id", text("created_at DESC")),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    scope: Optional[str] = None
    subject: str
    action: str
    object: str
    time_hint: Optional[str] = None
    importance_score: int = 5
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL2Summary(SQLModel, table=True):
    """Store rolling long-term summaries distilled from L1 activity."""

    __tablename__ = "mems_l2_summary"
    __table_args__ = (
        Index("ix_l2_summary_identity", "tenant_id", "user_id", "agent_id", "scope"),
        Index("ix_l2_summary_recall", "agent_id", text("last_verified_at DESC")),
        Index("ix_l2_summary_stale", "last_verified_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    scope: Optional[str] = None
    summary_type: str = "long_term"
    content: str
    vector_id: Optional[str] = Field(default=None, unique=True)
    vector_status: str = "pending"
    last_sync_error: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemsL2ConflictLog(SQLModel, table=True):
    """Store audit logs for profile and fact reconciliation conflicts."""

    __tablename__ = "mems_l2_conflict_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    scope: Optional[str] = None
    memory_type: str
    old_value: str
    new_value: str
    resolution: str
    reason: str = ""
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL3Archive(SQLModel, table=True):
    """Store archive batch metadata for long-term L3 retention."""

    __tablename__ = "mems_l3_archive"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str = Field(index=True)
    scope: Optional[str] = None
    time_period: str
    summary_text: str
    file_path: str
    record_count: int = 0
    archived_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsRedistillProgress(SQLModel, table=True):
    """Track which archived records were re-distilled with which model."""

    __tablename__ = "mems_redistill_progress"
    __table_args__ = (
        UniqueConstraint("content_hash", "model", name="uq_redistill_hash_model"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    content_hash: str
    model: str
    l3_path: Optional[str] = None
    l3_line: Optional[int] = None
    processed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsOutbox(SQLModel, table=True):
    """Store durable side-effect events for the transactional outbox."""

    __tablename__ = "mems_outbox"
    __table_args__ = (
        Index("ix_outbox_claim", "status", "available_at", "id"),
        Index(
            "ix_outbox_dedupe",
            "event_type",
            "aggregate_id",
            postgresql_where=text("status IN ('pending', 'claimed')"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str
    aggregate_id: str
    payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    status: str = "pending"
    attempts: int = 0
    owner: Optional[str] = None
    last_error: Optional[str] = None
    available_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
