from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mems.models import (
    MemsL1Episodic,
    MemsL2ConflictLog,
    MemsL2Event,
    MemsL2Fact,
    MemsL2ProfileItem,
    MemsL2Summary,
)
from mems.services.archive import ArchiveService
from mems.services.distill import DistillService


def test_memory_pipeline_end_to_end(test_app):
    app, engine, _, vector_service = test_app

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"

        monitor = client.get("/monitor/status")
        assert monitor.status_code == 200
        assert monitor.json()["status"] == "healthy"

        write = client.post(
            "/memories/write",
            json={
                "agent_id": "agent_test",
                "session_id": "session_test",
                "messages": [
                    {
                        "role": "user",
                        "content": "I like Python and long term memory systems.",
                    },
                    {"role": "assistant", "content": "Got it."},
                ],
                "active_plan": "verify pipeline",
                "temp_variables": {"case": "pytest"},
                "metadata": {"source": "test"},
            },
        )
        assert write.status_code == 200
        assert write.json()["persisted_to_l1"] is True

        search_before = client.post(
            "/memories/search",
            json={"agent_id": "agent_test", "query": "Python", "top_k": 5},
        )
        assert search_before.status_code == 200
        before_results = search_before.json()["results"]
        assert any(item["source"] == "l1_episodic" for item in before_results)

    with Session(engine) as session:
        record = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_test")
        ).first()
        assert record is not None
        assert record.is_archived is False
        assert record.is_distilled is False
        record.created_at = datetime.now(timezone.utc) - timedelta(days=45)
        session.add(record)
        session.commit()

    with TestClient(app) as client:
        monitor_before_archive = client.get("/monitor/status")
        assert monitor_before_archive.status_code == 200
        assert monitor_before_archive.json()["pipeline"]["pending_archive"] == 1

    with Session(engine) as session:
        archive_result = __import__("asyncio").run(
            ArchiveService(session).archive(agent_id="agent_test", days=30)
        )
        assert archive_result.success is True
        assert archive_result.archived_count == 1

    with Session(engine) as session:
        record = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_test")
        ).first()
        assert record is not None
        assert record.is_archived is True

    with TestClient(app) as client:
        search_after_archive = client.post(
            "/memories/search",
            json={"agent_id": "agent_test", "query": "Python", "top_k": 5},
        )
        assert search_after_archive.status_code == 200
        after_archive_results = search_after_archive.json()["results"]
        assert any(item["source"] == "l1_episodic" for item in after_archive_results)

        monitor_after_archive = client.get("/monitor/status")
        assert monitor_after_archive.status_code == 200
        assert monitor_after_archive.json()["pipeline"]["pending_archive"] == 0

    with Session(engine) as session:
        distill_result = __import__("asyncio").run(
            DistillService(session).distill(
                agent_id="agent_test", batch_size=10, force=False
            )
        )
        assert distill_result.success is True
        assert distill_result.distilled_count == 1
        assert distill_result.l2_created >= 1

    with Session(engine) as session:
        l1_record = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_test")
        ).first()
        profile_items = session.exec(
            select(MemsL2ProfileItem).where(MemsL2ProfileItem.agent_id == "agent_test")
        ).all()
        fact_items = session.exec(
            select(MemsL2Fact).where(MemsL2Fact.agent_id == "agent_test")
        ).all()
        event_items = session.exec(
            select(MemsL2Event).where(MemsL2Event.agent_id == "agent_test")
        ).all()
        summary_items = session.exec(
            select(MemsL2Summary).where(MemsL2Summary.agent_id == "agent_test")
        ).all()
        conflict_items = session.exec(
            select(MemsL2ConflictLog).where(MemsL2ConflictLog.agent_id == "agent_test")
        ).all()
        assert l1_record is not None
        assert l1_record.is_distilled is True
        assert len(profile_items) >= 1
        assert len(fact_items) >= 1
        assert len(event_items) >= 1
        assert len(summary_items) >= 1
        assert summary_items[0].vector_id is not None
        assert len(conflict_items) == 0

    with TestClient(app) as client:
        search_after_distill = client.post(
            "/memories/search",
            json={"agent_id": "agent_test", "query": "Python preference", "top_k": 5},
        )
        assert search_after_distill.status_code == 200
        distilled_results = search_after_distill.json()["results"]
        assert any(item["source"] == "l2_profile" for item in distilled_results)
        assert any(item["source"] == "l2_fact" for item in distilled_results)
        assert distilled_results[0]["source"] == "l2_profile"

        summary_search = client.post(
            "/memories/search",
            json={
                "agent_id": "agent_test",
                "query": "Give me a summary of recent focus",
                "top_k": 5,
            },
        )
        assert summary_search.status_code == 200
        summary_results = summary_search.json()["results"]
        assert any(item["source"] == "l2_summary" for item in summary_results)
        assert summary_results[0]["source"] == "l2_summary"

        monitor_after_distill = client.get("/monitor/status")
        assert monitor_after_distill.status_code == 200
        pipeline = monitor_after_distill.json()["pipeline"]
        assert pipeline["pending_distill"] == 0
        assert pipeline["profile_items"] >= 1
        assert pipeline["fact_items"] >= 1
        assert pipeline["summary_items"] >= 1
        assert pipeline["conflict_count"] == 0
        assert pipeline["stale_profile_items"] == 0
        assert pipeline["stale_fact_items"] == 0
        assert pipeline["stale_summary_items"] == 0

    assert "agent_agent_test" in vector_service.collections


def test_distill_reconciliation_supersedes_profile_value(test_app):
    _, engine, _, _ = test_app

    with Session(engine) as session:
        session.add(
            MemsL1Episodic(
                agent_id="agent_conflict",
                session_id="session_a",
                content="user: I like Python.",
                vector_id="vec_python",
                importance_score=0.8,
            )
        )
        session.add(
            MemsL1Episodic(
                agent_id="agent_conflict",
                session_id="session_b",
                content="user: I prefer Rust now.",
                vector_id="vec_rust",
                importance_score=0.8,
            )
        )
        session.commit()

    with Session(engine) as session:
        result = __import__("asyncio").run(
            DistillService(session).distill(
                agent_id="agent_conflict", batch_size=10, force=False
            )
        )
        assert result.success is True
        assert result.distilled_count == 2

    with Session(engine) as session:
        profile_items = session.exec(
            select(MemsL2ProfileItem).where(
                MemsL2ProfileItem.agent_id == "agent_conflict",
                MemsL2ProfileItem.key == "technology",
            )
        ).all()
        active_profiles = [item for item in profile_items if item.status == "active"]
        conflict_logs = session.exec(
            select(MemsL2ConflictLog).where(
                MemsL2ConflictLog.agent_id == "agent_conflict"
            )
        ).all()

        assert len(profile_items) == 2
        assert len(active_profiles) == 1
        assert active_profiles[0].value == "Rust"
        assert active_profiles[0].version == 2
        assert len(conflict_logs) >= 1
