from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from mems.domain.identity import MemoryIdentity
from mems.domain.memory import EpisodicMemory
from mems.domain.outbox import OutboxEvent, OutboxEventType
from mems.infrastructure.persistence.models import MemsL1Episodic, MemsOutbox
from mems.infrastructure.persistence.repositories import build_repositories
from mems.infrastructure.persistence.repositories.outbox import SqlOutboxRepository
from mems.infrastructure.persistence.uow import SqlUnitOfWork


def _event(aggregate_id: str = "v1") -> OutboxEvent:
    return OutboxEvent(
        event_type=OutboxEventType.VECTOR_SYNC,
        aggregate_id=aggregate_id,
        payload={"kind": "episodic"},
    )


def test_outbox_claim_and_done(test_app):
    _, engine, _, _ = test_app

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event())
        uow.commit()

        claimed = outbox.claim_ready("owner1", 10, datetime.now(timezone.utc))
        uow.commit()
        assert len(claimed) == 1
        assert claimed[0].status == "claimed"
        assert claimed[0].owner == "owner1"

        outbox.mark_done(claimed[0].id)
        uow.commit()

    with Session(engine) as session:
        row = session.get(MemsOutbox, claimed[0].id)
        assert row is not None
        assert row.status == "done"
        assert row.owner is None


def test_outbox_claim_is_exclusive(test_app):
    _, engine, _, _ = test_app

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event())
        uow.commit()

        now = datetime.now(timezone.utc)
        first = outbox.claim_ready("owner_a", 10, now)
        second = outbox.claim_ready("owner_b", 10, now)
        uow.commit()

        assert len(first) == 1
        assert second == []


def test_outbox_mark_failed_retries(test_app):
    _, engine, _, _ = test_app
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=5)

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event())
        uow.commit()
        claimed = outbox.claim_ready("owner1", 10, datetime.now(timezone.utc))
        uow.commit()
        outbox.mark_failed(claimed[0].id, "boom", retry_at)
        uow.commit()

    with Session(engine) as session:
        row = session.get(MemsOutbox, claimed[0].id)
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.owner is None
        assert row.last_error == "boom"
        assert row.available_at == retry_at.replace(tzinfo=None)


def test_outbox_exists_active(test_app):
    _, engine, _, _ = test_app

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event())
        uow.commit()
        assert outbox.exists_active(OutboxEventType.VECTOR_SYNC, "v1")
        claimed = outbox.claim_ready("owner1", 10, datetime.now(timezone.utc))
        uow.commit()
        outbox.mark_done(claimed[0].id)
        uow.commit()
        assert not outbox.exists_active(OutboxEventType.VECTOR_SYNC, "v1")


def test_uow_rollback_discards_changes(test_app):
    _, engine, _, _ = test_app

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event())
        uow.rollback()

    with Session(engine) as session:
        assert session.exec(select(MemsOutbox)).first() is None


def test_uow_shares_transaction_across_repositories(test_app):
    _, engine, _, _ = test_app
    identity = MemoryIdentity(agent_id="agent_uow")

    with Session(engine) as session:
        repos = build_repositories(session)
        uow = SqlUnitOfWork(session)
        memory = EpisodicMemory.create(
            identity=identity,
            content="user: remember this",
            messages=[],
            importance_score=0.5,
            now=datetime.now(timezone.utc),
        )
        repos.episodic.add(memory)
        repos.outbox.add(
            OutboxEvent(
                event_type=OutboxEventType.VECTOR_SYNC,
                aggregate_id=memory.vector_id,
                payload={"kind": "episodic"},
            )
        )
        uow.commit()

    with Session(engine) as session:
        assert (
            session.exec(
                select(MemsL1Episodic).where(
                    MemsL1Episodic.agent_id == "agent_uow"
                )
            ).first()
            is not None
        )
        assert session.exec(select(MemsOutbox)).first() is not None
