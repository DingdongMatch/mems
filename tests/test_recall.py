from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from mems.services.ranking import (
    l1_base,
    l2_entity_base,
    l2_event_base,
    l2_summary_base,
    lexical_overlap,
    sort_results,
)
from mems.schemas import QueryResultItem


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
    older = QueryResultItem(
        source="l1_episodic",
        content="older",
        score=1.0,
        created_at=now - timedelta(days=1),
    )
    newer = QueryResultItem(
        source="l2_fact",
        content="newer",
        score=1.0,
        created_at=now,
    )
    no_date = QueryResultItem(source="l0_hot", content="nodate", score=1.0)

    sorted_once = sort_results([no_date, older, newer])
    sorted_twice = sort_results([no_date, older, newer])
    assert [item.content for item in sorted_once] == ["newer", "older", "nodate"]
    assert sorted_once == sorted_twice


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
