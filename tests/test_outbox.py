from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from sqlmodel import Session, select

from mems.domain.identity import MemoryIdentity
from mems.domain.memory import EpisodicMemory
from mems.domain.outbox import OutboxEvent, OutboxEventType, outbox_aggregate_key
from mems.infrastructure.persistence.models import MemsL1Episodic, MemsOutbox
from mems.infrastructure.persistence.repositories import build_repositories
from mems.infrastructure.persistence.repositories.outbox import SqlOutboxRepository
from mems.infrastructure.persistence.uow import SqlUnitOfWork


def _event(
    aggregate_id: str = "v1",
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = "agent_outbox",
    namespace: str | None = None,
) -> OutboxEvent:
    return OutboxEvent(
        event_type=OutboxEventType.VECTOR_SYNC,
        aggregate_id=aggregate_id,
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        namespace=namespace,
        payload={"kind": "episodic"},
    )


def _claim(outbox: SqlOutboxRepository, owner: str, now: datetime, **kwargs):
    return outbox.claim_ready(
        owner,
        10,
        now,
        lease_seconds=kwargs.get("lease_seconds", 300),
        max_attempts=kwargs.get("max_attempts", 3),
    )


def test_outbox_claim_and_done(test_app):
    _, engine, _, _ = test_app

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event())
        uow.commit()

        claimed = _claim(outbox, "owner1", datetime.now(timezone.utc))
        uow.commit()
        assert len(claimed) == 1
        assert claimed[0].status == "claimed"
        assert claimed[0].owner == "owner1"
        assert claimed[0].id is not None

        assert outbox.mark_done(claimed[0].id, "owner1") is True
        uow.commit()

    with Session(engine) as session:
        row = session.get(MemsOutbox, claimed[0].id)
        assert row is not None
        assert row.status == "done"
        assert row.owner is None
        assert row.created_at.tzinfo is not None


def test_outbox_claim_is_exclusive(test_app):
    _, engine, _, _ = test_app

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event())
        uow.commit()

        now = datetime.now(timezone.utc)
        first = _claim(outbox, "owner_a", now)
        second = _claim(outbox, "owner_b", now)
        uow.commit()

        assert len(first) == 1
        assert second == []


def test_outbox_claim_respects_limit_and_claims_next_batch(test_app):
    _, engine, _, _ = test_app
    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event("first"))
        outbox.add(_event("second"))
        uow.commit()
        now = datetime.now(timezone.utc)
        first = outbox.claim_ready("owner_a", 1, now, 300, 3)
        uow.commit()
        second = outbox.claim_ready("owner_b", 1, now, 300, 3)
        uow.commit()
        assert [item.aggregate_id for item in first] == ["first"]
        assert [item.aggregate_id for item in second] == ["second"]


def test_outbox_concurrent_claim_uses_real_postgres_sessions(test_app):
    _, engine, _, _ = test_app
    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        event = outbox.add(_event())
        uow.commit()

    barrier = Barrier(2)
    now = datetime.now(timezone.utc)

    def claim(owner: str):
        with Session(engine) as session:
            outbox = SqlOutboxRepository(session)
            barrier.wait(timeout=10)
            events = _claim(outbox, owner, now)
            SqlUnitOfWork(session).commit()
            return events

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, "owner_a"), pool.submit(claim, "owner_b")]
        results = [future.result(timeout=15) for future in futures]

    assert sum(len(items) for items in results) == 1
    winner = next(items[0] for items in results if items)
    assert winner.id == event.id
    with Session(engine) as session:
        row = session.get(MemsOutbox, event.id)
        assert row is not None
        assert row.owner == winner.owner


def test_outbox_expired_reclaim_rejects_old_owner(test_app):
    _, engine, _, _ = test_app
    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event("done-event"))
        outbox.add(_event("failed-event", namespace='["other"]'))
        uow.commit()
        now = datetime.now(timezone.utc)
        first = _claim(outbox, "old_owner", now, lease_seconds=1)
        uow.commit()
        assert len(first) == 2

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        second = _claim(outbox, "new_owner", now + timedelta(seconds=2))
        uow.commit()
        assert {item.id for item in second} == {item.id for item in first}
        assert outbox.mark_done(first[0].id, "old_owner") is False
        retry_at = now + timedelta(seconds=30)
        assert (
            outbox.mark_failed(
                first[1].id, "old_owner", "stale failure", retry_at, 3
            )
            is False
        )
        uow.commit()

    with Session(engine) as session:
        for item in second:
            row = session.get(MemsOutbox, item.id)
            assert row is not None
            assert row.status == "claimed"
            assert row.owner == "new_owner"
            assert row.last_error is None
            assert row.attempts == 2


def test_outbox_renew_extends_lease_and_rejects_stale_owner(test_app):
    _, engine, _, _ = test_app
    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        event = outbox.add(_event("renew"))
        uow.commit()
        now = datetime.now(timezone.utc)
        claimed = _claim(outbox, "owner", now, lease_seconds=60)
        uow.commit()
        assert outbox.renew(claimed[0].id, "wrong", now, 60) is False
        assert outbox.renew(claimed[0].id, "owner", now + timedelta(seconds=20), 60)
        uow.commit()

    with Session(engine) as session:
        row = session.get(MemsOutbox, event.id)
        assert row is not None
        assert row.available_at == now + timedelta(seconds=80)
        assert _claim(SqlOutboxRepository(session), "other", now + timedelta(seconds=61)) == []


def test_outbox_duplicate_active_insert_keeps_one_row(test_app):
    _, engine, _, _ = test_app
    identity = MemoryIdentity(
        tenant_id="tenant",
        user_id="user",
        agent_id="agent_outbox",
        namespace_segments=("project",),
    )
    key = outbox_aggregate_key(
        OutboxEventType.VECTOR_SYNC,
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        agent_id=identity.agent_id,
        namespace=identity.namespace_key,
        resource="v1",
    )
    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        first = outbox.add(
            _event(
                key,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                namespace=identity.namespace_key,
            )
        )
        duplicate = outbox.add(
            _event(
                key,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                namespace=identity.namespace_key,
            )
        )
        assert first.id == duplicate.id
        uow.commit()
        rows = session.exec(select(MemsOutbox)).all()
        assert len([row for row in rows if row.id == first.id]) == 1
        assert outbox.exists_active(
            OutboxEventType.VECTOR_SYNC,
            key,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            namespace=identity.namespace_key,
        )


def test_outbox_active_dedupe_distinguishes_complete_identity(test_app):
    _, engine, _, _ = test_app
    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        events = [
            outbox.add(_event("null-identity", agent_id=None)),
            outbox.add(_event("tenant-a", tenant_id="a")),
            outbox.add(_event("tenant-b", tenant_id="b")),
            outbox.add(_event("user-a", user_id="a")),
            outbox.add(_event("namespace-a", namespace='["a"]')),
        ]
        uow.commit()
        assert len({event.id for event in events}) == len(events)


def test_outbox_mark_failed_retries(test_app):
    _, engine, _, _ = test_app
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=5)

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event())
        uow.commit()
        claimed = _claim(outbox, "owner1", datetime.now(timezone.utc))
        uow.commit()
        assert (
            outbox.mark_failed(
                claimed[0].id, "owner1", "boom", retry_at, 3
            )
            is True
        )
        uow.commit()

    with Session(engine) as session:
        row = session.get(MemsOutbox, claimed[0].id)
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.owner is None
        assert row.last_error == "boom"
        assert row.available_at == retry_at


def test_outbox_dead_letter_after_max_attempts(test_app):
    _, engine, _, _ = test_app
    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        event = outbox.add(_event())
        uow.commit()
        now = datetime.now(timezone.utc)
        for attempt in range(2):
            claimed = _claim(outbox, f"owner-{attempt}", now)
            assert len(claimed) == 1
            uow.commit()
            assert (
                outbox.mark_failed(
                    event.id,
                    f"owner-{attempt}",
                    f"failure-{attempt}",
                    now,
                    2,
                )
                is True
            )
            uow.commit()
        assert _claim(outbox, "never", now + timedelta(seconds=1)) == []
        uow.commit()

    with Session(engine) as session:
        row = session.get(MemsOutbox, event.id)
        assert row.status == "dead"
        assert row.dead is True
        assert row.dead_at is not None
        assert row.attempts == 2
        assert row.owner is None
        assert row.last_error == "failure-1"


def test_outbox_exists_active(test_app):
    _, engine, _, _ = test_app

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        outbox.add(_event())
        uow.commit()
        assert outbox.exists_active(
            OutboxEventType.VECTOR_SYNC, "v1", agent_id="agent_outbox"
        )
        claimed = _claim(outbox, "owner1", datetime.now(timezone.utc))
        uow.commit()
        outbox.mark_done(claimed[0].id, "owner1")
        uow.commit()
        assert not outbox.exists_active(
            OutboxEventType.VECTOR_SYNC, "v1", agent_id="agent_outbox"
        )


def test_uow_rollback_discards_changes(test_app):
    _, engine, _, _ = test_app

    with Session(engine) as session:
        outbox = SqlOutboxRepository(session)
        uow = SqlUnitOfWork(session)
        event = outbox.add(_event("rolled-back"))
        uow.rollback()

    with Session(engine) as session:
        assert session.get(MemsOutbox, event.id) is None


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
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                namespace=identity.namespace_key,
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


def test_coalesced_wakeup_survives_claim_and_completion(test_app):
    _, engine, _, _ = test_app
    for kind in [OutboxEventType.DISTILL_AGENT, OutboxEventType.DISTILL_DUE]:
        event = OutboxEvent(event_type=kind, aggregate_id=str(kind), payload={"batch_size": 1})
        with Session(engine) as session:
            outbox = SqlOutboxRepository(session)
            outbox.add(event)
            outbox.add(event.model_copy(update={"id": None}))
            session.commit()
            claimed = _claim(outbox, "first", datetime.now(timezone.utc))[0]
            session.commit()
            assert "_rerun" not in claimed.payload
        # Mimic a producer committing after the worker took its SQL snapshot.
        with Session(engine) as producer:
            SqlOutboxRepository(producer).add(event.model_copy(update={"id": None}))
            producer.commit()
        with Session(engine) as session:
            outbox = SqlOutboxRepository(session)
            assert outbox.mark_done(claimed.id, "stale") is False
            assert outbox.mark_done(claimed.id, "first") is True
            session.commit()
            row = session.get(MemsOutbox, claimed.id)
            assert row.status == "pending"
            assert row.attempts == 0
            assert row.payload == {"batch_size": 1}
            second = _claim(outbox, "second", datetime.now(timezone.utc))[0]
            assert second.id == claimed.id
            assert second.attempts == 1
            assert outbox.mark_done(second.id, "second")
            session.commit()
            session.expire_all()
            assert session.get(MemsOutbox, second.id).status == "done"
