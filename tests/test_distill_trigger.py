from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mems.config import settings
from mems.domain.outbox import OutboxEventType, outbox_aggregate_key
from mems.infrastructure.persistence.models import (
    MemsL1Episodic,
    MemsL2Fact,
    MemsL2ProfileItem,
    MemsOutbox,
)
from mems.infrastructure.persistence.repositories import build_repositories
from mems.interfaces.bootstrap import build_outbox_worker, make_distill_service


async def _drain_worker(worker, max_passes: int = 10) -> int:
    processed = 0
    for _ in range(max_passes):
        count = await worker.run_once()
        processed += count
        if count == 0:
            break
    return processed


def test_high_importance_write_distills_immediately(test_app, monkeypatch):
    """A high-importance write is distilled via the outbox worker."""
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 0.8)
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_important",
                "importance_score": 0.9,
                "messages": [
                    {"role": "user", "content": "I like Python and long term memory systems."}
                ],
            },
        )
        assert response.status_code == 200

    worker = build_outbox_worker()
    asyncio.run(_drain_worker(worker))

    with Session(engine) as session:
        l1 = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_important")
        ).first()
        assert l1 is not None
        assert l1.is_distilled is True
        assert (
            len(
                session.exec(
                    select(MemsL2ProfileItem).where(
                        MemsL2ProfileItem.agent_id == "agent_important"
                    )
                ).all()
            )
            >= 1
        )


def test_write_does_not_distill_below_threshold(test_app):
    """Below the global threshold, the worker does not enqueue distillation."""
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_quiet",
                "importance_score": 0.5,
                "messages": [{"role": "user", "content": "Hello, nice day."}],
            },
        )
        assert response.status_code == 200

    worker = build_outbox_worker()
    asyncio.run(_drain_worker(worker))

    with Session(engine) as session:
        l1 = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_quiet")
        ).first()
        assert l1 is not None
        assert l1.is_distilled is False


def test_write_triggers_distill_when_threshold_reached(test_app, monkeypatch):
    """A persisted write is distilled once the global threshold is met."""
    monkeypatch.setattr(settings, "DISTILL_THRESHOLD", 1)
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_distill",
                "messages": [
                    {"role": "user", "content": "I like Python and long term memory systems."}
                ],
            },
        )
        assert response.status_code == 200

    worker = build_outbox_worker()
    asyncio.run(_drain_worker(worker))

    with Session(engine) as session:
        l1 = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_distill")
        ).first()
        assert l1 is not None
        assert l1.is_distilled is True
        assert (
            len(
                session.exec(
                    select(MemsL2Fact).where(MemsL2Fact.agent_id == "agent_distill")
                ).all()
            )
            >= 1
        )


def test_distill_batch_orders_by_importance(test_app, monkeypatch):
    """batch=1 distillation picks the highest-importance pending record first."""
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_order",
                "importance_score": 0.5,
                "messages": [{"role": "user", "content": "I prefer Rust."}],
            },
        )
        client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_order",
                "importance_score": 0.9,
                "messages": [{"role": "user", "content": "I like Python."}],
            },
        )

    with Session(engine) as session:
        service = asyncio.run(make_distill_service(session))
        result = asyncio.run(
            service.distill(agent_id="agent_order", batch_size=1, force=False)
        )
        assert result.distilled_count == 1
        records = session.exec(
            select(MemsL1Episodic)
            .where(MemsL1Episodic.agent_id == "agent_order")
            .order_by(MemsL1Episodic.importance_score.desc())
        ).all()
        assert records[0].importance_score == 0.9
        assert records[0].is_distilled is True
        assert records[1].is_distilled is False


def test_failed_distill_event_is_retried_instead_of_marked_done(test_app, monkeypatch):
    app, engine, _, _ = test_app

    class FailingLLM:
        async def chat(self, **kwargs):
            raise RuntimeError("LLM unavailable")

    monkeypatch.setattr("mems.interfaces.bootstrap.get_llm_port", lambda: FailingLLM())
    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_retry",
                "importance_score": 0.9,
                "content": "The user strongly prefers Python for backend development.",
            },
        )
        assert response.status_code == 200

    worker = build_outbox_worker()
    assert asyncio.run(worker.run_once()) >= 1
    with Session(engine) as session:
        event = session.exec(
            select(MemsOutbox).where(MemsOutbox.event_type == "distill_agent")
        ).one()
        memory = session.exec(select(MemsL1Episodic)).one()
        assert event.status == "pending"
        assert event.attempts == 1
        assert memory.is_distilled is False


def test_outbox_deduplicates_distill_due(test_app):
    """Multiple writes below threshold keep only one pending distill_due event."""
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_dedupe",
                "messages": [{"role": "user", "content": "first memory"}],
            },
        )
        client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_dedupe",
                "messages": [{"role": "user", "content": "second memory"}],
            },
        )

    with Session(engine) as session:
        repos = build_repositories(session)
        assert repos.outbox.exists_active(
            OutboxEventType.DISTILL_DUE,
            outbox_aggregate_key(OutboxEventType.DISTILL_DUE),
        )


def test_coalesced_distill_drains_more_batches_than_retry_limit(test_app, monkeypatch):
    app, engine, _, _ = test_app
    monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "DISTILL_IMMEDIATE_BATCH_SIZE", 1)
    with TestClient(app) as client:
        for index in range(7):
            response = client.post("/v1/mems/write", json={
                "agent_id": "burst", "importance_score": 0.9,
                "content": f"I like Python, preference {index}",
            })
            assert response.status_code == 200
    asyncio.run(build_outbox_worker().run_once())
    with Session(engine) as session:
        records = session.exec(select(MemsL1Episodic)).all()
        assert len(records) == 7
        assert all(item.is_distilled for item in records)
        triggers = session.exec(select(MemsOutbox).where(
            MemsOutbox.event_type == OutboxEventType.DISTILL_AGENT
        )).all()
        assert len(triggers) == 1
        assert triggers[0].status == "done"
        assert triggers[0].attempts == 1


def test_disabled_llm_does_not_enqueue_or_retry_distillation(test_app, monkeypatch):
    app, engine, _, _ = test_app
    # A shared key can be configured solely for embedding.
    monkeypatch.setattr(settings, "LLM_PROVIDER", None)
    with TestClient(app) as client:
        for importance in [0.5, 0.9]:
            assert client.post("/v1/mems/write", json={
                "agent_id": "embedding_only", "importance_score": importance,
                "content": "I like Python",
            }).status_code == 200
    with Session(engine) as session:
        repos = build_repositories(session)
        assert all(item.event_type == OutboxEventType.VECTOR_SYNC for item in session.exec(select(MemsOutbox)).all())
        # Previously enqueued jobs also become harmless no-ops when disabled.
        from mems.domain.outbox import OutboxEvent
        repos.outbox.add(OutboxEvent(
            event_type=OutboxEventType.DISTILL_AGENT, aggregate_id="old_job",
            agent_id="embedding_only", payload={"agent_id": "embedding_only"},
        ))
        session.commit()
    asyncio.run(build_outbox_worker().run_once())
    with Session(engine) as session:
        assert all(item.status == "done" for item in session.exec(select(MemsOutbox)).all())
        assert not any(item.is_distilled for item in session.exec(select(MemsL1Episodic)).all())


def test_write_during_claimed_trigger_is_not_lost(test_app, monkeypatch):
    app, engine, _, _ = test_app
    monkeypatch.setattr(settings, "DISTILL_THRESHOLD", 2)
    with TestClient(app) as client:
        for agent, importance, handler_name in [
            ("claimed_agent", 0.9, "_handle_distill_agent"),
            ("claimed_global", 0.5, "_handle_distill_due"),
        ]:
            payload = {"agent_id": agent, "importance_score": importance, "content": "I use Python"}
            assert client.post("/v1/mems/write", json=payload).status_code == 200
            worker = build_outbox_worker()
            original = getattr(worker, handler_name)
            injected = False

            async def handle(session, event):
                nonlocal injected
                await original(session, event)
                if not injected:
                    injected = True
                    # Commit between the handler's final query and mark_done.
                    assert client.post("/v1/mems/write", json={
                        **payload, "content": "I use Rust now",
                    }).status_code == 200

            monkeypatch.setattr(worker, handler_name, handle)
            asyncio.run(worker.run_once())
            assert injected
            with Session(engine) as session:
                rows = session.exec(select(MemsL1Episodic).where(MemsL1Episodic.agent_id == agent)).all()
                assert len(rows) == 2
                assert all(row.is_distilled for row in rows)
