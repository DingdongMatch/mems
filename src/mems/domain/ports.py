"""Ports (interfaces) for repositories and external services.

仓储与外部服务端口定义。领域层只依赖这些接口，具体实现位于基础设施层。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ContextManager, Optional, Protocol

from mems.domain.archive import ArchiveBatch
from mems.domain.identity import MemoryIdentity
from mems.domain.knowledge import (
    ConflictLog,
    Event,
    Fact,
    Profile,
    Summary,
)
from mems.domain.memory import EpisodicMemory, WorkingMemory
from mems.domain.outbox import OutboxEvent


class WorkingMemoryStore(Protocol):
    """Async L0 working-memory store."""

    async def write(
        self,
        tenant_id: Optional[str],
        user_id: Optional[str],
        agent_id: str,
        session_id: Optional[str],
        messages: list[dict[str, str]],
        content: Optional[str] = None,
        scope: Optional[str] = None,
        ttl_seconds: int = 1800,
        event_type: str = "write",
    ) -> WorkingMemory: ...

    async def read(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> Optional[WorkingMemory]: ...

    async def list_active(
        self,
        agent_id: str,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        scope: Optional[str] = None,
        limit: int = 10,
    ) -> list[WorkingMemory]: ...

    async def delete(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> bool: ...

    async def append_messages(
        self,
        tenant_id: Optional[str],
        user_id: Optional[str],
        agent_id: str,
        session_id: Optional[str],
        messages: list[dict[str, str]],
        content: Optional[str] = None,
        ttl_seconds: int = 1800,
        max_buffer_size: int = 10,
        scope: Optional[str] = None,
    ) -> WorkingMemory: ...

    async def ping(self) -> None: ...


class EpisodicRepository(Protocol):
    """Sync SQL repository for L1 episodic memories."""

    def add(self, memory: EpisodicMemory) -> Optional[int]: ...

    def save(self, memory: EpisodicMemory) -> None: ...

    def get_by_vector_ids(
        self,
        agent_id: str,
        vector_ids: list[str],
        identity: Optional[MemoryIdentity] = None,
    ) -> list[EpisodicMemory]: ...

    def find_for_distill(
        self,
        agent_id: str,
        batch_size: int,
        force: bool,
        min_importance: float,
    ) -> list[EpisodicMemory]: ...

    def list_expired(
        self,
        agent_id: str,
        tenant_id: Optional[str],
        user_id: Optional[str],
        scope: Optional[str],
        cutoff: datetime,
    ) -> list[EpisodicMemory]: ...

    def list_archive_identities(
        self, cutoff: datetime
    ) -> list[tuple[Optional[str], Optional[str], str, Optional[str]]]: ...

    def count_pending_distill(self) -> int: ...

    def count_pending_archive(self, cutoff: datetime) -> int: ...

    def list_agents_with_pending_distill(self) -> list[str]: ...

class KnowledgeRepository(Protocol):
    """Sync SQL repository for L2 knowledge entities."""

    def list_active_profiles(self, identity: MemoryIdentity) -> list[Profile]: ...

    def list_active_facts(self, identity: MemoryIdentity) -> list[Fact]: ...

    def list_summaries(self, identity: MemoryIdentity) -> list[Summary]: ...

    def list_profiles_for_query(
        self, identity: MemoryIdentity, limit: int
    ) -> list[Profile]: ...

    def list_facts_for_query(
        self, identity: MemoryIdentity, limit: int
    ) -> list[Fact]: ...

    def list_events_for_query(
        self, identity: MemoryIdentity, limit: int
    ) -> list[Event]: ...

    def list_summaries_for_query(
        self, identity: MemoryIdentity, limit: int
    ) -> list[Summary]: ...

    def get_active_profile(
        self, identity: MemoryIdentity, category: str, key: str
    ) -> Optional[Profile]: ...

    def get_exact_fact(
        self,
        identity: MemoryIdentity,
        subject: str,
        predicate: str,
        object: str,
    ) -> Optional[Fact]: ...

    def get_conflicting_facts(
        self, identity: MemoryIdentity, subject: str, predicate: str
    ) -> list[Fact]: ...

    def save_profile(self, profile: Profile) -> Profile: ...

    def save_fact(self, fact: Fact) -> Fact: ...

    def add_event(self, event: Event) -> Event: ...

    def add_conflict(self, conflict: ConflictLog) -> ConflictLog: ...

    def save_summary(self, summary: Summary) -> Summary: ...

    def count_active_profiles(self) -> int: ...

    def count_active_facts(self) -> int: ...

    def count_summaries(self) -> int: ...

    def count_conflicts(self) -> int: ...

    def count_stale_profiles(self, cutoff: datetime) -> int: ...

    def count_stale_facts(self, cutoff: datetime) -> int: ...

    def count_stale_summaries(self, cutoff: datetime) -> int: ...

class ArchiveRepository(Protocol):
    """Sync SQL repository for L3 archive metadata."""

    def add(self, batch: ArchiveBatch) -> ArchiveBatch: ...

    def list(self, agent_id: Optional[str] = None) -> list[ArchiveBatch]: ...


class RedistillProgressRepository(Protocol):
    """Sync SQL repository for re-distillation progress."""

    def exists(self, content_hash: str, model: str) -> bool: ...

    def add(
        self,
        content_hash: str,
        model: str,
        l3_path: Optional[str],
        l3_line: Optional[int],
    ) -> None: ...


class VectorStorePort(Protocol):
    """Async vector store adapter."""

    async def upsert(self, collection_name: str, points: list[dict[str, Any]]) -> bool: ...

    async def upsert_episodic(
        self, collection_name: str, memory: EpisodicMemory, vector: list[float]
    ) -> bool: ...

    async def upsert_summary(
        self, collection_name: str, summary: Summary, vector: list[float]
    ) -> bool: ...

    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        top_k: int = 5,
        filter_agent_id: Optional[str] = None,
        filters: Optional[dict[str, str]] = None,
    ) -> list[dict[str, Any]]: ...

    async def get_collections(self) -> list[str]: ...

    async def delete_points(self, collection_name: str, point_ids: list[str]) -> bool: ...


class EmbeddingPort(Protocol):
    """Async embedding provider."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class LLMPort(Protocol):
    """Async OpenAI-compatible LLM client."""

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> str: ...


class SchedulerPort(Protocol):
    """Sync scheduler adapter for background jobs."""

    def start(self) -> None: ...

    def shutdown(self) -> None: ...

    def add_archive_job(self, func: Any, hour: int = 3, minute: int = 0) -> None: ...

    def is_running(self) -> bool: ...

    def has_archive_job(self) -> bool: ...


class Clock(Protocol):
    """Time source for deterministic domain behavior."""

    def now(self) -> datetime: ...


class SessionFactory(Protocol):
    """Factory for request-independent database sessions."""

    def create(self) -> ContextManager[Any]: ...


class UnitOfWork(Protocol):
    """Single transaction boundary shared by repositories bound to one session."""

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class HealthCheckPort(Protocol):
    """Database health check adapter."""

    def healthcheck(self) -> None: ...


class OutboxRepository(Protocol):
    """Sync SQL repository for transactional outbox events."""

    def add(self, event: OutboxEvent) -> OutboxEvent: ...

    def exists_active(self, event_type: str, aggregate_id: str) -> bool: ...

    def claim_ready(
        self, owner: str, limit: int, now: datetime
    ) -> list[OutboxEvent]: ...

    def mark_done(self, event_id: int) -> None: ...

    def mark_failed(
        self, event_id: int, error: str, available_at: datetime
    ) -> None: ...
