from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from mems.config import settings
from mems.domain.identity import MemoryIdentity
from mems.domain.services.ranking import (
    ScoredMemory,
    detect_query_intents,
    l1_base,
    l2_entity_base,
    l2_event_base,
    l2_summary_base,
    lexical_overlap,
    sort_results,
)
from mems.infrastructure.persistence.models import MemsL2Fact
from mems.interfaces.api import dependencies


def test_lexical_overlap_supports_chinese():
    """CJK bigram tokenization keeps Chinese lexical matching working.

    CJK bigram 分词保证中文词法匹配有效。
    """
    assert lexical_overlap("喜欢", "用户喜欢Rust") == 1.0
    assert lexical_overlap("Rust", "用户喜欢Rust") == 1.0
    assert lexical_overlap("中文", "用户喜欢用中文交流") == 1.0
    assert lexical_overlap("Rust", "The user likes Rust") == 1.0
    assert lexical_overlap("python", "I like Rust") == 0.0
    assert lexical_overlap("", "anything") == 0.0


def test_l0_recall_across_sessions(test_app):
    """Query recalls active L0 snapshots from multiple sessions.

    查询能召回同 agent 多个活跃会话的 L0 快照。
    """
    app, _, _, _ = test_app

    with TestClient(app) as client:
        for session_id in ("session_a", "session_b"):
            response = client.post(
                "/v1/mems/write",
                json={
                    "agent_id": "agent_l0",
                    "session_id": session_id,
                    "content": "The user likes Rust.",
                },
            )
            assert response.status_code == 200

        search = client.post(
            "/v1/mems/query",
            json={"agent_id": "agent_l0", "query": "Rust", "top_k": 10},
        )
        assert search.status_code == 200
        l0_results = [
            item for item in search.json()["results"] if item["source"] == "l0_hot"
        ]
        assert {item["session_id"] for item in l0_results} == {
            "session_a",
            "session_b",
        }


def test_chinese_query_recalls_l0(test_app):
    """A Chinese query can recall L0 hot memory via bigram overlap.

    中文查询通过 bigram 词法重叠召回 L0 热记忆。
    """
    app, _, _, _ = test_app

    with TestClient(app) as client:
        write = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_zh",
                "content": "用户喜欢Rust",
            },
        )
        assert write.status_code == 200

        search = client.post(
            "/v1/mems/query",
            json={"agent_id": "agent_zh", "query": "喜欢", "top_k": 5},
        )
        assert search.status_code == 200
        assert any(
            item["source"] == "l0_hot" for item in search.json()["results"]
        )


def test_normalized_bases_are_bounded():
    """All normalized base scores stay within 0-1.

    各来源归一化 base 分均在 0-1 区间。
    """
    lexical = 0.5
    for base in (
        l1_base(vector_score=0.9, lexical=lexical),
        l1_base(vector_score=-0.2, lexical=0.0),
        l1_base(vector_score=1.8, lexical=lexical),
        l2_entity_base(confidence=1.0, lexical=lexical),
        l2_event_base(importance=8, lexical=lexical),
        l2_summary_base(lexical=lexical, vector_score=0.6),
    ):
        assert 0.0 <= base <= 1.0


def test_sort_results_is_deterministic():
    """Equal scores break ties by created_at, newest first.

    同分结果按创建时间倒序，顺序确定。
    """
    now = datetime.now(timezone.utc)
    older = ScoredMemory(
        source="l1_episodic",
        content="older",
        score=1.0,
        created_at=now - timedelta(days=1),
    )
    newer = ScoredMemory(
        source="l2_fact",
        content="newer",
        score=1.0,
        created_at=now,
    )
    no_date = ScoredMemory(source="l0_hot", content="nodate", score=1.0)

    sorted_once = sort_results([no_date, older, newer])
    sorted_twice = sort_results([no_date, older, newer])
    assert [item.content for item in sorted_once] == ["newer", "older", "nodate"]
    assert sorted_once == sorted_twice


def test_sort_results_normalizes_mixed_timezones():
    """Naive values mean UTC; aware values are compared by their UTC instant."""
    same_instant_naive = ScoredMemory(
        source="l1_episodic",
        content="naive",
        score=1.0,
        created_at=datetime(2026, 1, 1, 12, 0),
    )
    same_instant_aware = ScoredMemory(
        source="l2_fact",
        content="aware",
        score=1.0,
        created_at=datetime(
            2026, 1, 1, 20, 0, tzinfo=timezone(timedelta(hours=8))
        ),
    )
    newest = ScoredMemory(
        source="l2_event",
        content="newest",
        score=1.0,
        created_at=datetime(
            2026, 1, 1, 7, 30, tzinfo=timezone(timedelta(hours=-5))
        ),
    )
    assert [
        item.content
        for item in sort_results([same_instant_naive, same_instant_aware, newest])
    ] == ["newest", "naive", "aware"]


def test_detect_query_intents_supports_ascii_and_cjk():
    assert detect_query_intents("What's the latest project progress?") == {
        "fact",
        "event",
    }
    assert detect_query_intents("最近喜欢什么") == {"profile", "event"}
    assert detect_query_intents("项目进展") == {"fact", "event"}


@pytest.mark.parametrize(
    "embedding_result", ["error", "empty", "empty_vector", "vector_error"]
)
def test_query_degrades_when_vector_pipeline_is_unavailable(
    test_app, monkeypatch, embedding_result
):
    """Embedding/search failures and empty vectors keep L0 and SQL-backed L2."""
    app, engine, _, vector = test_app

    class UnavailableEmbedding:
        async def embed(self, texts):
            if embedding_result == "error":
                raise RuntimeError("embedding unavailable")
            if embedding_result == "empty":
                return []
            if embedding_result == "empty_vector":
                return [[]]
            return [[1.0, 0.1, 1.0]]

    async def get_unavailable_embedding():
        return UnavailableEmbedding()

    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_degraded",
                "content": "I like Python",
                "importance_score": 0.9,
            },
        )
        assert response.status_code == 200

    with Session(engine) as session:
        session.add(
            MemsL2Fact(
                agent_id="agent_degraded",
                namespace=MemoryIdentity(agent_id="agent_degraded").namespace_key,
                subject="user",
                predicate="likes",
                object="Python",
                confidence=1.0,
            )
        )
        session.commit()

    async def unexpected_search(*args, **kwargs):
        if embedding_result == "vector_error":
            raise RuntimeError("vector search unavailable")
        raise AssertionError("vector search must not run without a query vector")

    monkeypatch.setattr(vector, "search", unexpected_search)
    app.dependency_overrides[dependencies.get_embedding_port] = get_unavailable_embedding
    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/query",
            json={"agent_id": "agent_degraded", "query": "Python", "top_k": 10},
        )
    assert response.status_code == 200
    sources = {item["source"] for item in response.json()["results"]}
    assert "l0_hot" in sources
    assert "l2_fact" in sources


def test_fake_vector_does_not_boost_summary_scores(test_app):
    _, _, _, vector = test_app

    async def run():
        await vector.upsert(
            "agent_scores",
            [
                {
                    "id": "summary",
                    "vector": [1.0],
                    "payload": {"memory_type": "l2_summary", "agent_id": "a"},
                },
                {
                    "id": "fact",
                    "vector": [1.0],
                    "payload": {"memory_type": "l2_fact", "agent_id": "a"},
                },
            ],
        )
        results = await vector.search("agent_scores", [1.0], filter_agent_id="a")
        return {item["id"]: item["score"] for item in results}

    scores = asyncio.run(run())
    assert scores["summary"] == scores["fact"]


def test_oversized_request_body_returns_413(test_app):
    app, _, _, _ = test_app
    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/write",
            json={"agent_id": "agent_large", "content": "x" * (1024 * 1024)},
        )
    assert response.status_code == 413


def test_query_length_limit_returns_422(test_app):
    app, _, _, _ = test_app
    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/query",
            json={"agent_id": "agent_large", "query": "x" * (settings.QUERY_MAX_CHARS + 1)},
        )
    assert response.status_code == 422


def test_blank_query_rejected(test_app):
    """Blank search queries return 422.

    空白检索查询返回 422。
    """
    app, _, _, _ = test_app

    with TestClient(app) as client:
        for query in ("", "   "):
            response = client.post(
                "/v1/mems/query",
                json={"agent_id": "agent_x", "query": query},
            )
            assert response.status_code == 422


def test_query_degrades_when_redis_is_unavailable(test_app, monkeypatch):
    app, engine, store, _ = test_app
    identity = MemoryIdentity(agent_id="redis_down")
    with Session(engine) as session:
        session.add(MemsL2Fact(
            agent_id=identity.agent_id, namespace=identity.namespace_key,
            subject="user", predicate="likes", object="Python",
        ))
        session.commit()

    async def unavailable(*args, **kwargs):
        raise ConnectionError("redis is offline")

    monkeypatch.setattr(store, "list_active", unavailable)
    with TestClient(app) as client:
        response = client.post("/v1/mems/query", json={"agent_id": identity.agent_id, "query": "Python"})
    assert response.status_code == 200
    assert any(item["source"] == "l2_fact" for item in response.json()["results"])
