"""Memory aggregates for the hot cache (L0) and episodic ledger (L1).

热缓存（L0）与情景记忆账本（L1）聚合。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from mems.domain.identity import MemoryIdentity


class WorkingMemory(BaseModel):
    """L0 hot-memory snapshot aggregate."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    scope: Optional[str] = None
    content: Optional[str] = None
    short_term_buffer: list[dict[str, str]] = Field(default_factory=list)
    expires_at: datetime

    @property
    def identity(self) -> MemoryIdentity:
        return MemoryIdentity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            scope=self.scope,
        )

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at <= now

    def merge_append(
        self,
        messages: list[dict[str, str]],
        content: Optional[str],
        scope: Optional[str],
        max_buffer_size: int,
    ) -> "WorkingMemory":
        """Merge new messages into this snapshot with a bounded buffer.

        将新消息合并到当前快照，并按 ``max_buffer_size`` 裁剪缓冲区。
        """
        merged = self.short_term_buffer + messages
        if max_buffer_size > 0:
            merged = merged[-max_buffer_size:]
        return self.model_copy(
            update={
                "short_term_buffer": merged,
                "content": content if content is not None else self.content,
                "scope": scope if scope is not None else self.scope,
            }
        )

    @classmethod
    def create(
        cls,
        identity: MemoryIdentity,
        messages: list[dict[str, str]],
        content: Optional[str],
        scope: Optional[str],
        expires_at: datetime,
        max_buffer_size: int = 0,
    ) -> "WorkingMemory":
        """Create a fresh L0 snapshot.

        新建一个 L0 快照；``max_buffer_size`` 为 0 时不裁剪。
        """
        buffer = messages
        if max_buffer_size > 0:
            buffer = messages[-max_buffer_size:]
        return cls(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            session_id=identity.session_id,
            scope=scope if scope is not None else identity.scope,
            content=content,
            short_term_buffer=buffer,
            expires_at=expires_at,
        )


class EpisodicMemory(BaseModel):
    """L1 episodic memory aggregate rooted at one durable record."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    scope: Optional[str] = None
    content: str
    messages: list[dict[str, str]] = Field(default_factory=list)
    vector_id: str
    importance_score: float = 0.5
    is_distilled: bool = False
    is_archived: bool = False
    vector_status: str = "pending"
    archive_status: str = "pending"
    last_sync_error: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def identity(self) -> MemoryIdentity:
        return MemoryIdentity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            scope=self.scope,
        )

    @classmethod
    def create(
        cls,
        identity: MemoryIdentity,
        content: str,
        messages: list[dict[str, str]],
        importance_score: float,
        now: datetime,
    ) -> "EpisodicMemory":
        return cls(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            session_id=identity.session_id,
            scope=identity.scope,
            content=content,
            messages=messages,
            vector_id=str(uuid4()),
            importance_score=importance_score,
            created_at=now,
        )

    def mark_distilled(self) -> None:
        self.is_distilled = True

    def mark_archived(self, now: datetime) -> None:
        self.is_archived = True
        self.archive_status = "ready"
        self.last_sync_error = None
        self.last_sync_at = now

    def mark_archive_failed(self, error: str, now: datetime) -> None:
        self.archive_status = "failed"
        self.last_sync_error = error
        self.last_sync_at = now

    def mark_vector_ready(self, now: datetime) -> None:
        self.vector_status = "ready"
        self.last_sync_error = None
        self.last_sync_at = now

    def mark_vector_failed(self, error: str, now: datetime) -> None:
        self.vector_status = "failed"
        self.last_sync_error = error
        self.last_sync_at = now
