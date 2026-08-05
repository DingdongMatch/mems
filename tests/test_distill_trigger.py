from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mems.config import settings
from mems.models import MemsL1Episodic, MemsL2Fact, MemsL2ProfileItem
from mems.services.distill import (
    DistillService,
    distill_high_value_if_idle,
    trigger_distill_if_due,
)


def test_write_triggers_distill_when_threshold_reached(test_app, monkeypatch):
    """A persisted write fires background distillation once the threshold is met.

    达到阈值时，写入会在后台触发蒸馏（BackgroundTasks 在响应返回前完成）。
    """
    monkeypatch.setattr(settings, "DISTILL_THRESHOLD", 1)
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_distill",
                "messages": [
                    {
                        "role": "user",
                        "content": "I like Python and long term memory systems.",
                    }
                ],
            },
        )
        assert response.status_code == 200
        assert response.json()["persisted_to_l1"] is True

    with Session(engine) as session:
        l1 = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_distill")
        ).first()
        assert l1 is not None
        assert l1.is_distilled is True
        assert (
            len(
                session.exec(
                    select(MemsL2ProfileItem).where(
                        MemsL2ProfileItem.agent_id == "agent_distill"
                    )
                ).all()
            )
            >= 1
        )
        assert (
            len(
                session.exec(
                    select(MemsL2Fact).where(
                        MemsL2Fact.agent_id == "agent_distill"
                    )
                ).all()
            )
            >= 1
        )


def test_write_does_not_distill_below_threshold(test_app):
    """Below the threshold, background distillation is a no-op.

    未达到阈值时，后台蒸馏直接跳过，L1 保持未蒸馏状态。
    """
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

    with Session(engine) as session:
        l1 = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_quiet")
        ).first()
        assert l1 is not None
        assert l1.is_distilled is False
        assert (
            len(
                session.exec(
                    select(MemsL2ProfileItem).where(
                        MemsL2ProfileItem.agent_id == "agent_quiet"
                    )
                ).all()
            )
            == 0
        )


def test_high_importance_write_distills_immediately(test_app, monkeypatch):
    """A high-importance write is distilled right away, below the global threshold.

    高价值写入即使全局未达阈值，也会立即蒸馏该条记录。
    """
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 0.8)
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_important",
                "importance_score": 0.9,
                "messages": [
                    {
                        "role": "user",
                        "content": "I like Python and long term memory systems.",
                    }
                ],
            },
        )
        assert response.status_code == 200
        assert response.json()["importance_score"] == 0.9

    with Session(engine) as session:
        l1 = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_important")
        ).first()
        assert l1 is not None
        assert l1.importance_score == 0.9
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


def test_distill_batch_orders_by_importance(test_app, monkeypatch):
    """batch=1 distillation picks the highest-importance pending record first.

    batch=1 蒸馏优先处理同 agent 中重要度最高的未蒸馏记录。
    """
    # Keep both writes on the threshold path so setup does not distill early.
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
        result = asyncio.run(
            DistillService(session).distill(
                agent_id="agent_order", batch_size=1, force=False
            )
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


def test_trigger_distill_if_due_skips_when_running(monkeypatch):
    """A second trigger while one is in flight is a no-op.

    已有蒸馏运行中时，新的触发请求应直接跳过。
    """
    from mems.services import distill as distill_module

    calls = []

    async def fake_trigger(agent_id=None):
        calls.append(agent_id)
        return {"triggered": True, "processed": 0}

    monkeypatch.setattr(distill_module, "trigger_distill_automatically", fake_trigger)
    distill_module._distill_in_progress = True
    try:
        result = asyncio.run(trigger_distill_if_due())
        assert result["triggered"] is False
        assert result["reason"] == "distill already running"
        assert calls == []
    finally:
        distill_module._distill_in_progress = False


def test_distill_high_value_if_idle_skips_when_running(monkeypatch):
    """High-value trigger is also skipped while another distill is in flight.

    已有蒸馏运行中时，高价值触发同样直接跳过。
    """
    from mems.services import distill as distill_module

    distill_module._distill_in_progress = True
    try:
        result = asyncio.run(distill_high_value_if_idle("agent_x"))
        assert result["triggered"] is False
        assert result["reason"] == "distill already running"
    finally:
        distill_module._distill_in_progress = False
