"""L2 knowledge entities and distillation extraction DTOs.

L2 知识实体与蒸馏提取结构。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from mems.domain.identity import MemoryIdentity, MemoryNamespace


class Profile(BaseModel):
    """Stable user-profile assertion entity."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    namespace: Optional[str] = None
    category: str
    key: str
    value: str
    confidence: float = 1.0
    status: str = "active"
    source_l1_ids: list[int] = Field(default_factory=list)
    supersedes_id: Optional[int] = None
    version: int = 1
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def identity(self) -> MemoryIdentity:
        return MemoryIdentity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            memory_namespace=(
                MemoryNamespace.from_key(self.namespace) if self.namespace else None
            ),
        )


class Fact(BaseModel):
    """Durable fact/entity-relation entity."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    namespace: Optional[str] = None
    subject: str
    predicate: str
    object: str
    fact_type: str = "general"
    confidence: float = 1.0
    status: str = "active"
    source_l1_ids: list[int] = Field(default_factory=list)
    supersedes_id: Optional[int] = None
    version: int = 1
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def identity(self) -> MemoryIdentity:
        return MemoryIdentity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            memory_namespace=(
                MemoryNamespace.from_key(self.namespace) if self.namespace else None
            ),
        )


class Event(BaseModel):
    """Important extracted event entity."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    namespace: Optional[str] = None
    subject: str
    action: str
    object: str
    time_hint: Optional[str] = None
    importance_score: int = 5
    source_l1_ids: list[int] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Summary(BaseModel):
    """Rolling long-term summary entity."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    namespace: Optional[str] = None
    summary_type: str = "long_term"
    content: str
    vector_id: Optional[str] = None
    vector_status: str = "pending"
    last_sync_error: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    source_l1_ids: list[int] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def identity(self) -> MemoryIdentity:
        return MemoryIdentity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            memory_namespace=(
                MemoryNamespace.from_key(self.namespace) if self.namespace else None
            ),
        )


class ConflictLog(BaseModel):
    """Reconciliation conflict audit entity."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    namespace: Optional[str] = None
    memory_type: str
    old_value: str
    new_value: str
    resolution: str
    reason: str = ""
    source_l1_ids: list[int] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DistillDiscardedItem(BaseModel):
    text: str
    reason: str


class DistillProfileUpdate(BaseModel):
    category: str
    key: str
    value: str
    confidence: float = 0.8
    evidence: str = ""


class DistillFactItem(BaseModel):
    subject: str
    relation: str
    object: str
    fact_type: str = "general"
    confidence: float = 0.8
    evidence: str = ""


class DistillEventItem(BaseModel):
    subject: str
    action: str
    object: str
    time_hint: str = "recent"
    importance: int = 5
    evidence: str = ""


class DistillConflictCandidate(BaseModel):
    memory_type: str
    old: str
    new: str
    reason: str = ""


class DistillExtractionResult(BaseModel):
    discarded: List[DistillDiscardedItem] = Field(default_factory=list)
    profile_updates: List[DistillProfileUpdate] = Field(default_factory=list)
    facts: List[DistillFactItem] = Field(default_factory=list)
    events: List[DistillEventItem] = Field(default_factory=list)
    conflict_candidates: List[DistillConflictCandidate] = Field(default_factory=list)
    long_term_summary: str = ""


@dataclass
class DistillSource:
    """A distillation input record independent of its storage layer."""

    tenant_id: Optional[str]
    user_id: Optional[str]
    agent_id: str
    session_id: Optional[str]
    namespace: Optional[str]
    content: str
    source_id: int
    created_at: datetime
    importance_score: float = 0.5
    l3_path: Optional[str] = None
    l3_line: Optional[int] = None

    @property
    def identity(self) -> MemoryIdentity:
        return MemoryIdentity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            memory_namespace=(
                MemoryNamespace.from_key(self.namespace) if self.namespace else None
            ),
        )

    @classmethod
    def from_episodic(cls, memory: Any) -> "DistillSource":
        """Build a source record from an L1 episodic aggregate."""
        return cls(
            tenant_id=memory.tenant_id,
            user_id=memory.user_id,
            agent_id=memory.agent_id,
            session_id=memory.session_id,
            namespace=memory.namespace,
            content=memory.content,
            source_id=memory.id or 0,
            created_at=memory.created_at,
            importance_score=memory.importance_score,
        )

    @classmethod
    def from_archive_record(
        cls, record: Dict[str, Any], path: str, line_no: int
    ) -> "DistillSource":
        """Build a source record from one L3 JSONL archive entry."""
        created_at = datetime.now(timezone.utc)
        raw_created = record.get("created_at")
        if isinstance(raw_created, str):
            try:
                created_at = datetime.fromisoformat(raw_created)
            except ValueError:
                pass
        return cls(
            tenant_id=record.get("tenant_id"),
            user_id=record.get("user_id"),
            agent_id=record.get("agent_id", ""),
            session_id=record.get("session_id"),
            namespace=record.get("namespace"),
            content=record.get("content", ""),
            source_id=int(record["id"]) if record.get("id") is not None else 0,
            created_at=created_at,
            importance_score=float(record.get("importance_score", 0.5)),
            l3_path=path,
            l3_line=line_no,
        )
