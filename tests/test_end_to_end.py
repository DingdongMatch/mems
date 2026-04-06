from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
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
    """Verify the core write, search, archive, and distill pipeline.

    验证核心写入、检索、归档与蒸馏流水线。
    """
    app, engine, _, vector_service = test_app

    with TestClient(app) as client:
        health = client.get("/v1/mems/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"

        monitor = client.get("/v1/mems/status")
        assert monitor.status_code == 200
        assert monitor.json()["status"] == "healthy"

        write = client.post(
            "/v1/mems/write",
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
            "/v1/mems/query",
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
        monitor_before_archive = client.get("/v1/mems/status")
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
            "/v1/mems/query",
            json={"agent_id": "agent_test", "query": "Python", "top_k": 5},
        )
        assert search_after_archive.status_code == 200
        after_archive_results = search_after_archive.json()["results"]
        assert all(item["source"] != "l1_episodic" for item in after_archive_results)

        monitor_after_archive = client.get("/v1/mems/status")
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
            "/v1/mems/query",
            json={"agent_id": "agent_test", "query": "Python preference", "top_k": 5},
        )
        assert search_after_distill.status_code == 200
        distilled_results = search_after_distill.json()["results"]
        assert any(item["source"] == "l2_profile" for item in distilled_results)
        assert any(item["source"] == "l2_fact" for item in distilled_results)
        assert distilled_results[0]["source"] == "l2_profile"

        summary_search = client.post(
            "/v1/mems/query",
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

        monitor_after_distill = client.get("/v1/mems/status")
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


def test_write_memory_keeps_l1_record_when_vector_sync_fails(
    test_app, monkeypatch: pytest.MonkeyPatch
):
    """Ensure SQL persistence succeeds even when vector sync fails.

    确保向量同步失败时，SQL 主记录仍然成功持久化。
    """
    app, engine, _, _ = test_app

    class FailingVectorService:
        async def upsert(self, collection_name: str, points):
            """Always fail vector upserts for resilience testing.

            始终让向量写入失败，用于验证容错逻辑。
            """
            raise RuntimeError("vector unavailable")

    async def get_failing_vector_service():
        """Return the failing vector service used in this test.

        返回本测试使用的失败向量服务。
        """
        return FailingVectorService()

    monkeypatch.setattr(
        "mems.services.l0_sync.get_vector_service", get_failing_vector_service
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_vector_fail",
                "session_id": "session_vector_fail",
                "messages": [{"role": "user", "content": "remember this"}],
            },
        )

    assert response.status_code == 200
    assert response.json()["persisted_to_l1"] is True

    with Session(engine) as session:
        record = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_vector_fail")
        ).first()
        assert record is not None
        assert record.vector_status == "failed"
        assert "vector unavailable" in (record.last_sync_error or "")


def test_distill_reconciliation_supersedes_profile_value(test_app):
    """Verify newer profile values supersede older active values.

    验证较新的画像值会覆盖较旧的活跃值。
    """
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


def test_context_write_and_l1_fallback(test_app):
    """Cover context reads, write endpoint behavior, and L1 fallback.

    覆盖上下文读取、统一写入接口行为和 L1 回退逻辑。
    """
    app, engine, redis_service, _ = test_app

    with TestClient(app) as client:
        turns = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_chat",
                "session_id": "session_chat",
                "messages": [
                    {"role": "user", "content": "Remember that I like Rust."},
                    {"role": "assistant", "content": "Got it, you like Rust."},
                ],
            },
        )
        assert turns.status_code == 200
        assert turns.json()["persisted_to_l1"] is True

        context = client.get(
            "/v1/mems/context",
            params={
                "agent_id": "agent_chat",
                "session_id": "session_chat",
                "limit": 10,
            },
        )
        assert context.status_code == 200
        context_json = context.json()
        assert context_json["source"] == "l0"
        assert context_json["page_type"] == "live"
        assert len(context_json["messages"]) == 2
        assert context_json["has_more"] is False

    with Session(engine) as session:
        l1_record = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_chat")
        ).first()
        assert l1_record is not None
        assert l1_record.metadata_json["messages"][0]["role"] == "user"

    redis_service._store.pop((None, None, None, "agent_chat", "session_chat"), None)

    with TestClient(app) as client:
        fallback_context = client.get(
            "/v1/mems/context",
            params={
                "agent_id": "agent_chat",
                "session_id": "session_chat",
                "limit": 10,
            },
        )
        assert fallback_context.status_code == 200
        fallback_json = fallback_context.json()
        assert fallback_json["source"] == "l1"
        assert fallback_json["page_type"] == "live"
        assert fallback_json["messages"][0]["content"] == "Remember that I like Rust."

    with Session(engine) as session:
        session_records = session.exec(
            select(MemsL1Episodic).where(
                MemsL1Episodic.agent_id == "agent_chat",
                MemsL1Episodic.session_id == "session_chat",
            )
        ).all()
        assert len(session_records) == 1


def test_context_history_paginates_l1_records_and_keeps_live_tail(test_app):
    """Verify live context keeps the tail while history pages older L1 data.

    验证实时上下文保留尾部消息，历史分页读取更早 L1 数据。
    """
    app, _, _, _ = test_app

    with TestClient(app) as client:
        for turn in range(3):
            response = client.post(
                "/v1/mems/write",
                json={
                    "agent_id": "agent_history",
                    "session_id": "session_history",
                    "messages": [
                        {"role": "user", "content": f"user-{turn}"},
                        {"role": "assistant", "content": f"assistant-{turn}"},
                    ],
                },
            )
            assert response.status_code == 200

        live_page = client.get(
            "/v1/mems/context",
            params={
                "agent_id": "agent_history",
                "session_id": "session_history",
                "limit": 2,
            },
        )
        assert live_page.status_code == 200
        live_json = live_page.json()
        assert live_json["page_type"] == "live"
        assert live_json["source"] == "l0"
        assert live_json["has_more"] is True
        assert live_json["next_before_id"] is not None
        assert [message["content"] for message in live_json["messages"]] == [
            "user-0",
            "assistant-0",
            "user-1",
            "assistant-1",
            "user-2",
            "assistant-2",
        ]

        history_page = client.get(
            "/v1/mems/context",
            params={
                "agent_id": "agent_history",
                "session_id": "session_history",
                "limit": 2,
                "before_id": live_json["next_before_id"],
            },
        )
        assert history_page.status_code == 200
        history_json = history_page.json()
        assert history_json["page_type"] == "history"
        assert history_json["source"] == "l1"
        assert history_json["has_more"] is False
        assert history_json["next_before_id"] is None
        assert [message["content"] for message in history_json["messages"]] == [
            "user-0",
            "assistant-0",
        ]


def test_identity_fields_isolate_multi_user_memory(test_app):
    """Verify tenant, user, and scope isolate memory visibility.

    验证 tenant、user 与 scope 字段能够隔离记忆可见性。
    """
    app, engine, redis_service, _ = test_app

    with TestClient(app) as client:
        user_a = client.post(
            "/v1/mems/write",
            json={
                "tenant_id": "tenant_1",
                "user_id": "user_a",
                "agent_id": "support_agent",
                "session_id": "session_shared",
                "scope": "private",
                "messages": [
                    {"role": "user", "content": "Remember I like Python."},
                    {"role": "assistant", "content": "Got it for user A."},
                ],
            },
        )
        user_b = client.post(
            "/v1/mems/write",
            json={
                "tenant_id": "tenant_1",
                "user_id": "user_b",
                "agent_id": "support_agent",
                "session_id": "session_shared",
                "scope": "private",
                "messages": [
                    {"role": "user", "content": "Remember I like Rust."},
                    {"role": "assistant", "content": "Got it for user B."},
                ],
            },
        )
        assert user_a.status_code == 200
        assert user_b.status_code == 200

        context_a = client.get(
            "/v1/mems/context",
            params={
                "tenant_id": "tenant_1",
                "user_id": "user_a",
                "agent_id": "support_agent",
                "session_id": "session_shared",
                "scope": "private",
            },
        )
        context_b = client.get(
            "/v1/mems/context",
            params={
                "tenant_id": "tenant_1",
                "user_id": "user_b",
                "agent_id": "support_agent",
                "session_id": "session_shared",
                "scope": "private",
            },
        )
        assert context_a.status_code == 200
        assert context_b.status_code == 200
        assert "Python" in context_a.text
        assert "Rust" not in context_a.text
        assert "Rust" in context_b.text
        assert "Python" not in context_b.text

        search_a = client.post(
            "/v1/mems/query",
            json={
                "tenant_id": "tenant_1",
                "user_id": "user_a",
                "agent_id": "support_agent",
                "scope": "private",
                "query": "Python",
                "top_k": 5,
            },
        )
        search_b = client.post(
            "/v1/mems/query",
            json={
                "tenant_id": "tenant_1",
                "user_id": "user_b",
                "agent_id": "support_agent",
                "scope": "private",
                "query": "Rust",
                "top_k": 5,
            },
        )
        assert search_a.status_code == 200
        assert search_b.status_code == 200
        assert any("Python" in item["content"] for item in search_a.json()["results"])
        assert all("Rust" not in item["content"] for item in search_a.json()["results"])
        assert any("Rust" in item["content"] for item in search_b.json()["results"])
        assert all(
            "Python" not in item["content"] for item in search_b.json()["results"]
        )

    with Session(engine) as session:
        records = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "support_agent")
        ).all()
        assert len(records) == 2
        assert {record.user_id for record in records} == {"user_a", "user_b"}
        assert {record.scope for record in records} == {"private"}

    assert (
        redis_service._store.get(
            ("tenant_1", "user_a", "private", "support_agent", "session_shared")
        )
        is not None
    )
    assert (
        redis_service._store.get(
            ("tenant_1", "user_b", "private", "support_agent", "session_shared")
        )
        is not None
    )
