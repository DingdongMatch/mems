from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from mems.config import settings
from mems.domain.outbox import OutboxEvent, OutboxEventType
from mems.infrastructure.persistence.database import SqlSessionFactory
from mems.infrastructure.persistence.models import MemsOutbox
from mems.infrastructure.persistence.repositories import build_repositories
from mems.infrastructure.persistence.uow import SqlUnitOfWork
from mems.interfaces import bootstrap
from mems.interfaces.bootstrap import OutboxWorker


def _add_event(engine, aggregate_id: str) -> int:
    with Session(engine) as session:
        repos = build_repositories(session)
        event = repos.outbox.add(
            OutboxEvent(
                event_type=OutboxEventType.VECTOR_SYNC,
                aggregate_id=aggregate_id,
                payload={"kind": "episodic"},
            )
        )
        SqlUnitOfWork(session).commit()
        assert event.id is not None
        return event.id


def test_active_unique_key_preserves_distinct_vector_syncs(test_app):
    _, engine, _, _ = test_app
    first_id = _add_event(engine, "vector-1")
    second_id = _add_event(engine, "vector-2")

    assert first_id != second_id
    with Session(engine) as session:
        rows = session.exec(select(MemsOutbox)).all()
        assert {row.aggregate_id for row in rows} == {"vector-1", "vector-2"}
        assert all(row.status == "pending" for row in rows)


@pytest.mark.asyncio
async def test_worker_retries_business_failure_and_continues(test_app, monkeypatch):
    _, engine, _, _ = test_app
    failed_id = _add_event(engine, "failed")
    success_id = _add_event(engine, "success")
    worker = OutboxWorker(SqlSessionFactory(engine))
    dispatched: list[int] = []

    async def dispatch(_session, event):
        dispatched.append(event.id)
        return event.id != failed_id

    monkeypatch.setattr(worker, "_dispatch", dispatch)

    assert await worker.run_once() == 2
    assert dispatched == [failed_id, success_id]

    with Session(engine) as session:
        failed = session.get(MemsOutbox, failed_id)
        succeeded = session.get(MemsOutbox, success_id)
        assert failed is not None and failed.status == "pending"
        assert failed.attempts == 1
        assert failed.owner is None
        assert succeeded is not None and succeeded.status == "done"


@pytest.mark.asyncio
async def test_worker_moves_exhausted_event_to_dead(test_app, monkeypatch):
    _, engine, _, _ = test_app
    event_id = _add_event(engine, "poison")
    monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 1)
    worker = OutboxWorker(SqlSessionFactory(engine))

    async def dispatch(_session, _event):
        raise ValueError("permanent failure")

    monkeypatch.setattr(worker, "_dispatch", dispatch)

    assert await worker.run_once() == 1
    with Session(engine) as session:
        row = session.get(MemsOutbox, event_id)
        assert row is not None
        assert row.status == "dead"
        assert row.attempts == 1
        assert row.owner is None
        assert row.dead_at is not None


@pytest.mark.asyncio
async def test_worker_claims_one_event_at_a_time(test_app, monkeypatch):
    _, engine, _, _ = test_app
    first_id = _add_event(engine, "first")
    second_id = _add_event(engine, "second")
    worker = OutboxWorker(SqlSessionFactory(engine))

    async def dispatch(_session, event):
        with Session(engine) as verification:
            claimed = verification.exec(
                select(MemsOutbox).where(MemsOutbox.status == "claimed")
            ).all()
            assert len(claimed) == 1
            assert claimed[0].id == event.id
        return True

    monkeypatch.setattr(worker, "_dispatch", dispatch)

    assert await worker.run_once() == 2
    with Session(engine) as session:
        rows = session.exec(
            select(MemsOutbox).where(MemsOutbox.id.in_([first_id, second_id]))
        ).all()
        assert {row.status for row in rows} == {"done"}


@pytest.mark.asyncio
async def test_two_workers_process_events_without_duplicates(test_app, monkeypatch):
    _, engine, _, _ = test_app
    event_ids = [_add_event(engine, f"event-{index}") for index in range(6)]
    factory = SqlSessionFactory(engine)
    workers = [OutboxWorker(factory), OutboxWorker(factory)]
    dispatched: list[int] = []
    lock = asyncio.Lock()

    async def dispatch(_session, event):
        async with lock:
            dispatched.append(event.id)
        await asyncio.sleep(0)
        return True

    for worker in workers:
        monkeypatch.setattr(worker, "_dispatch", dispatch)

    processed = await asyncio.gather(*(worker.run_once() for worker in workers))
    assert sum(processed) == len(event_ids)
    assert sorted(dispatched) == sorted(event_ids)
    assert len(dispatched) == len(set(dispatched))

    with Session(engine) as session:
        rows = session.exec(select(MemsOutbox)).all()
        assert len(rows) == len(event_ids)
        assert all(row.status == "done" for row in rows)
        assert all(row.attempts == 1 for row in rows)


@pytest.mark.asyncio
async def test_killed_worker_event_is_reclaimed_after_lease(test_app, monkeypatch):
    _, engine, _, _ = test_app
    event_id = _add_event(engine, "killed-worker")
    factory = SqlSessionFactory(engine)
    first = OutboxWorker(factory)
    second = OutboxWorker(factory)
    now = datetime.now(timezone.utc)

    class Clock:
        def now(self):
            return now

    monkeypatch.setattr(bootstrap, "get_clock", Clock)
    monkeypatch.setattr(settings, "OUTBOX_LEASE_SECONDS", 60)
    started = asyncio.Event()

    async def interrupted_dispatch(_session, _event):
        started.set()
        raise asyncio.CancelledError

    async def successful_dispatch(_session, _event):
        return True

    monkeypatch.setattr(first, "_dispatch", interrupted_dispatch)
    monkeypatch.setattr(second, "_dispatch", successful_dispatch)

    with pytest.raises(asyncio.CancelledError):
        await first.run_once()
    assert started.is_set()

    now = now + timedelta(seconds=61)
    assert await second.run_once() == 1
    with Session(engine) as session:
        row = session.get(MemsOutbox, event_id)
        assert row is not None
        assert row.status == "done"
        assert row.attempts == 2


@pytest.mark.asyncio
async def test_dispatch_heartbeat_keeps_claim_alive(test_app, monkeypatch):
    _, engine, _, _ = test_app
    event_id = _add_event(engine, "heartbeat")
    factory = SqlSessionFactory(engine)
    worker = OutboxWorker(factory)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        claimed = build_repositories(session).outbox.claim_ready("owner", 1, now, 60, 3)
        SqlUnitOfWork(session).commit()
        assert len(claimed) == 1
        release = asyncio.Event()

        async def dispatch(_session, _event):
            await release.wait()
            return True

        monkeypatch.setattr(worker, "_dispatch", dispatch)
        task = asyncio.create_task(
            worker._dispatch_with_heartbeat(session, claimed[0], renew_interval=0.01)
        )
        try:
            await asyncio.sleep(0.05)
            with Session(engine) as verification:
                row = verification.get(MemsOutbox, event_id)
                assert row is not None
                assert row.available_at > now + timedelta(seconds=30)
        finally:
            release.set()
            assert await task is True


async def test_dispatch_is_cancelled_when_lease_owner_changes(test_app, monkeypatch):
    _, engine, _, _ = test_app
    event_id = _add_event(engine, "heartbeat-lost")
    worker = OutboxWorker(SqlSessionFactory(engine))
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        claimed = build_repositories(session).outbox.claim_ready("old", 1, now, 60, 3)
        SqlUnitOfWork(session).commit()
        assert len(claimed) == 1
    with Session(engine) as session:
        replacement = build_repositories(session).outbox.claim_ready(
            "new", 1, now + timedelta(seconds=61), 60, 3
        )
        SqlUnitOfWork(session).commit()
        assert len(replacement) == 1
        cancelled = asyncio.Event()

        async def dispatch(_session, _event):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(worker, "_dispatch", dispatch)
        with pytest.raises(RuntimeError, match="lease lost"):
            await worker._dispatch_with_heartbeat(
                session, claimed[0], renew_interval=0.01
            )
        assert cancelled.is_set()
    with Session(engine) as session:
        row = session.get(MemsOutbox, event_id)
        assert row is not None and row.owner == "new"


async def test_crashed_worker_attempts_exhaust_to_dead(test_app, monkeypatch):
    _, engine, _, _ = test_app
    event_id = _add_event(engine, "crash-budget")
    factory = SqlSessionFactory(engine)
    now = datetime.now(timezone.utc)

    class Clock:
        def now(self):
            return now

    monkeypatch.setattr(bootstrap, "get_clock", Clock)
    monkeypatch.setattr(settings, "OUTBOX_LEASE_SECONDS", 60)
    monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 2)

    for _ in range(2):
        worker = OutboxWorker(factory)

        async def interrupted_dispatch(_session, _event):
            raise asyncio.CancelledError

        monkeypatch.setattr(worker, "_dispatch", interrupted_dispatch)
        with pytest.raises(asyncio.CancelledError):
            await worker.run_once()
        now = now + timedelta(seconds=61)

    assert await OutboxWorker(factory).run_once() == 0
    with Session(engine) as session:
        row = session.get(MemsOutbox, event_id)
        assert row is not None
        assert row.status == "dead"
        assert row.dead is True
        assert row.attempts == 2
