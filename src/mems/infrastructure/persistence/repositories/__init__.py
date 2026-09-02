"""SQLModel repository implementations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from mems.infrastructure.persistence.repositories.archive import SqlArchiveRepository
from mems.infrastructure.persistence.repositories.episodic import (
    SqlEpisodicRepository,
)
from mems.infrastructure.persistence.repositories.knowledge import (
    SqlKnowledgeRepository,
)
from mems.infrastructure.persistence.repositories.outbox import (
    SqlOutboxRepository,
)
from mems.infrastructure.persistence.repositories.redistill import (
    SqlRedistillProgressRepository,
)


@dataclass
class RepositoryBundle:
    """Convenience container for all SQL repositories bound to one session."""

    episodic: SqlEpisodicRepository
    knowledge: SqlKnowledgeRepository
    archive: SqlArchiveRepository
    redistill: SqlRedistillProgressRepository
    outbox: SqlOutboxRepository


def build_repositories(session: Session) -> RepositoryBundle:
    return RepositoryBundle(
        episodic=SqlEpisodicRepository(session),
        knowledge=SqlKnowledgeRepository(session),
        archive=SqlArchiveRepository(session),
        redistill=SqlRedistillProgressRepository(session),
        outbox=SqlOutboxRepository(session),
    )
