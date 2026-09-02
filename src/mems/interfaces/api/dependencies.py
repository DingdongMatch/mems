"""FastAPI dependency composition root."""

from __future__ import annotations

from fastapi import Depends
from sqlmodel import Session

from mems.application.query_service import QueryMemoryService
from mems.application.status_service import StatusApplicationService
from mems.application.write_service import WriteMemoryService
from mems.config import settings
from mems.domain.ports import (
    Clock,
    EmbeddingPort,
    EpisodicRepository,
    HealthCheckPort,
    KnowledgeRepository,
    OutboxRepository,
    SchedulerPort,
    UnitOfWork,
    VectorStorePort,
    WorkingMemoryStore,
)
from mems.infrastructure.external.clock import system_clock
from mems.infrastructure.external.embedding import get_embedding_service
from mems.infrastructure.external.llm_client import get_llm_port
from mems.infrastructure.external.redis_service import redis_service
from mems.infrastructure.external.scheduler import scheduler_service
from mems.infrastructure.external.vector_service import vector_service
from mems.infrastructure.persistence.database import get_session
from mems.infrastructure.persistence.repositories import (
    SqlArchiveRepository,
    SqlEpisodicRepository,
    SqlKnowledgeRepository,
    SqlOutboxRepository,
    SqlRedistillProgressRepository,
)
from mems.infrastructure.persistence.uow import (
    SqlDatabaseHealthCheck,
    SqlUnitOfWork,
)


def get_episodic_repository(
    session: Session = Depends(get_session),
) -> EpisodicRepository:
    return SqlEpisodicRepository(session)


def get_knowledge_repository(
    session: Session = Depends(get_session),
) -> KnowledgeRepository:
    return SqlKnowledgeRepository(session)


def get_archive_repository(
    session: Session = Depends(get_session),
) -> SqlArchiveRepository:
    return SqlArchiveRepository(session)


def get_redistill_progress_repository(
    session: Session = Depends(get_session),
) -> SqlRedistillProgressRepository:
    return SqlRedistillProgressRepository(session)


def get_outbox_repository(
    session: Session = Depends(get_session),
) -> OutboxRepository:
    return SqlOutboxRepository(session)


def get_unit_of_work(
    session: Session = Depends(get_session),
) -> UnitOfWork:
    return SqlUnitOfWork(session)


def get_database_health(
    session: Session = Depends(get_session),
) -> HealthCheckPort:
    return SqlDatabaseHealthCheck(session)


async def get_working_memory_store() -> WorkingMemoryStore:
    return redis_service


async def get_vector_store() -> VectorStorePort:
    return vector_service


async def get_embedding_port() -> EmbeddingPort:
    return await get_embedding_service()


def get_scheduler_port() -> SchedulerPort:
    return scheduler_service


def get_clock_port() -> Clock:
    return system_clock


def get_write_service(
    store: WorkingMemoryStore = Depends(get_working_memory_store),
    episodic: EpisodicRepository = Depends(get_episodic_repository),
    outbox: OutboxRepository = Depends(get_outbox_repository),
    uow: UnitOfWork = Depends(get_unit_of_work),
    clock: Clock = Depends(get_clock_port),
) -> WriteMemoryService:
    return WriteMemoryService(
        store=store,
        episodic=episodic,
        outbox=outbox,
        uow=uow,
        clock=clock,
    )


def get_query_service(
    store: WorkingMemoryStore = Depends(get_working_memory_store),
    episodic: EpisodicRepository = Depends(get_episodic_repository),
    knowledge: KnowledgeRepository = Depends(get_knowledge_repository),
    vector: VectorStorePort = Depends(get_vector_store),
    embedding: EmbeddingPort = Depends(get_embedding_port),
    clock: Clock = Depends(get_clock_port),
) -> QueryMemoryService:
    return QueryMemoryService(
        store=store,
        episodic=episodic,
        knowledge=knowledge,
        vector=vector,
        embedding=embedding,
        clock=clock,
        l0_recall_max_snapshots=settings.L0_RECALL_MAX_SNAPSHOTS,
    )


def get_status_service(
    episodic: EpisodicRepository = Depends(get_episodic_repository),
    knowledge: KnowledgeRepository = Depends(get_knowledge_repository),
    store: WorkingMemoryStore = Depends(get_working_memory_store),
    vector: VectorStorePort = Depends(get_vector_store),
    scheduler: SchedulerPort = Depends(get_scheduler_port),
    clock: Clock = Depends(get_clock_port),
    health: HealthCheckPort = Depends(get_database_health),
) -> StatusApplicationService:
    return StatusApplicationService(
        episodic=episodic,
        knowledge=knowledge,
        store=store,
        vector=vector,
        scheduler=scheduler,
        clock=clock,
        health=health,
    )


__all__ = [
    "get_episodic_repository",
    "get_knowledge_repository",
    "get_archive_repository",
    "get_redistill_progress_repository",
    "get_outbox_repository",
    "get_unit_of_work",
    "get_database_health",
    "get_working_memory_store",
    "get_vector_store",
    "get_embedding_port",
    "get_scheduler_port",
    "get_clock_port",
    "get_write_service",
    "get_query_service",
    "get_status_service",
    "get_llm_port",
]
