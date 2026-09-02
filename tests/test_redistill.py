from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mems.config import settings
from mems.infrastructure.persistence.models import (
    MemsL1Episodic,
    MemsL2ConflictLog,
    MemsL2Fact,
    MemsL2ProfileItem,
    MemsRedistillProgress,
)
from mems.interfaces.bootstrap import make_archive_service, trigger_redistill


def _write_and_archive(
    client: TestClient,
    agent_id: str,
    engine,
    content: str = "I like Python and long term memory systems.",
    importance_score: float = 0.5,
    days_old: int = 40,
) -> int:
    """Write one memory, backdate it, and archive it into L3."""
    response = client.post(
        "/v1/mems/write",
        json={
            "agent_id": agent_id,
            "importance_score": importance_score,
            "messages": [{"role": "user", "content": content}],
        },
    )
    assert response.status_code == 200

    with Session(engine) as session:
        record = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == agent_id)
        ).first()
        assert record is not None
        l1_id = record.id
        record.created_at = datetime.now(timezone.utc) - timedelta(days=days_old)
        session.add(record)
        session.commit()

    with Session(engine) as session:
        result = asyncio.run(
            make_archive_service(session).archive(agent_id=agent_id, days=30)
        )
        assert result.archived_count == 1
    return l1_id


def test_redistill_closed_loop(test_app, monkeypatch):
    """write -> archive -> redistill creates L2 and records progress.

    write → 归档 → 再蒸馏 的闭环：L2 生成且进度落库（带 L3 证据指针）。
    """
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        _write_and_archive(client, "agent_loop", engine)
        response = client.post("/v1/mems/redistill", json={})
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["processed"] == 1
        assert payload["created"] >= 1

    with Session(engine) as session:
        profiles = session.exec(
            select(MemsL2ProfileItem).where(MemsL2ProfileItem.agent_id == "agent_loop")
        ).all()
        facts = session.exec(
            select(MemsL2Fact).where(MemsL2Fact.agent_id == "agent_loop")
        ).all()
        assert len(profiles) >= 1
        assert len(facts) >= 1

        progress = session.exec(select(MemsRedistillProgress)).all()
        assert len(progress) == 1
        assert progress[0].l3_path
        assert progress[0].l3_line >= 1


def test_redistill_dedup_and_model_upgrade(test_app, monkeypatch):
    """Same content+model is skipped; a new model re-processes it.

    同内容 + 同模型去重；更换模型后重新处理（且 L1 删除后仍可从 L3 再蒸馏）。
    """
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        _write_and_archive(client, "agent_dedup", engine)
        original_model = settings.OPENAI_MODEL

        first = asyncio.run(trigger_redistill())
        assert first.processed == 1

        second = asyncio.run(trigger_redistill())
        assert second.processed == 0
        assert second.skipped >= 1

        # Delete archived L1 rows, then upgrade the model and re-distill.
        with Session(engine) as session:
            for row in session.exec(select(MemsL1Episodic)).all():
                session.delete(row)
            session.commit()

        monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-5-redistill")
        third = asyncio.run(trigger_redistill())
        assert third.processed == 1

    with Session(engine) as session:
        progress = session.exec(select(MemsRedistillProgress)).all()
        assert {item.model for item in progress} == {
            original_model,
            "gpt-5-redistill",
        }
        assert all(item.l3_path for item in progress)


def test_redistill_supersedes_old_values(test_app, monkeypatch):
    """A newer model producing a different value supersedes the old L2 item.

    新模型得到不同值时，旧 L2 条目被 superseded 并记录冲突日志。
    """
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        _write_and_archive(client, "agent_version", engine)
        first = asyncio.run(trigger_redistill())
        assert first.processed == 1

        async def upgraded_llm_chat(
            messages, model=None, temperature=0.7, json_mode=False
        ):
            _ = (model, temperature, json_mode)
            return json.dumps(
                {
                    "discarded": [],
                    "profile_updates": [
                        {
                            "category": "like",
                            "key": "technology",
                            "value": "Rust",
                            "confidence": 0.95,
                            "evidence": "upgraded model",
                        }
                    ],
                    "facts": [],
                    "events": [],
                    "conflict_candidates": [],
                    "long_term_summary": "Upgraded summary.",
                },
                ensure_ascii=False,
            )

        class UpgradedLLM:
            async def chat(
                self, messages, model=None, temperature=0.7, json_mode=False
            ):
                _ = (messages, model, temperature, json_mode)
                return json.dumps(
                    {
                        "discarded": [],
                        "profile_updates": [
                            {
                                "category": "like",
                                "key": "technology",
                                "value": "Rust",
                                "confidence": 0.95,
                                "evidence": "upgraded model",
                            }
                        ],
                        "facts": [],
                        "events": [],
                        "conflict_candidates": [],
                        "long_term_summary": "Upgraded summary.",
                    },
                    ensure_ascii=False,
                )

        monkeypatch.setattr(
            "mems.interfaces.bootstrap.get_llm_port",
            lambda: UpgradedLLM(),
        )
        monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-5-version")
        second = asyncio.run(trigger_redistill())
        assert second.processed == 1

    with Session(engine) as session:
        old = session.exec(
            select(MemsL2ProfileItem).where(
                MemsL2ProfileItem.agent_id == "agent_version",
                MemsL2ProfileItem.value == "Python",
            )
        ).first()
        new = session.exec(
            select(MemsL2ProfileItem).where(
                MemsL2ProfileItem.agent_id == "agent_version",
                MemsL2ProfileItem.value == "Rust",
                MemsL2ProfileItem.status == "active",
            )
        ).first()
        assert old is not None and old.status == "superseded"
        assert new is not None and new.version == old.version + 1
        conflicts = session.exec(
            select(MemsL2ConflictLog).where(
                MemsL2ConflictLog.agent_id == "agent_version"
            )
        ).all()
        assert len(conflicts) >= 1


def test_redistill_filters(test_app, monkeypatch):
    """Agent and min-importance filters restrict what gets re-distilled.

    agent 与重要度过滤限制再蒸馏范围。
    """
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        _write_and_archive(client, "agent_filter_a", engine, importance_score=0.9)
        _write_and_archive(client, "agent_filter_b", engine, importance_score=0.3)

        only_a = asyncio.run(trigger_redistill(agent_id="agent_filter_a"))
        assert only_a.processed == 1

        important = asyncio.run(trigger_redistill(min_importance=0.8))
        assert important.processed == 0

        all_remaining = asyncio.run(trigger_redistill())
        assert all_remaining.processed == 1

    with Session(engine) as session:
        progress = session.exec(select(MemsRedistillProgress)).all()
        assert len(progress) == 2


def test_redistill_llm_failure_does_not_mark_progress(test_app, monkeypatch):
    """A failing LLM run leaves no progress rows behind.

    LLM 失败时记录不会被标记为已处理，下次可重试。
    """
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app

    with TestClient(app) as client:
        _write_and_archive(client, "agent_fail", engine)

        class FailingLLM:
            async def chat(self, messages, model=None, temperature=0.7, json_mode=False):
                _ = (messages, model, temperature, json_mode)
                raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(
            "mems.interfaces.bootstrap.get_llm_port",
            lambda: FailingLLM(),
        )
        result = asyncio.run(trigger_redistill())
        assert result.success is True
        assert result.processed == 0

    with Session(engine) as session:
        assert len(session.exec(select(MemsRedistillProgress)).all()) == 0


def test_redistill_empty_archive_is_noop(test_app):
    """No archive batches means a no-op run.

    没有归档批次时再蒸馏直接返回空结果。
    """
    app, _, _, _ = test_app
    result = asyncio.run(trigger_redistill())
    assert result.success is True
    assert result.processed == 0
    assert result.message == "No archive batches found"
