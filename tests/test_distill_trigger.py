from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mems.config import settings
from mems.domain.outbox import OutboxEventType
from mems.infrastructure.persistence.models import (
    MemsL1Episodic,
    MemsL2Fact,
    MemsL2ProfileItem,
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
        assert repos.outbox.exists_active(OutboxEventType.DISTILL_DUE, "global")
