from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from mems.database import get_session
from mems.main import app


@dataclass
class FakeRedisClient:
    service: "FakeRedisService"

    async def ping(self) -> bool:
        return True


class FakeRedisService:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], Any] = {}
        self._client = FakeRedisClient(self)

    async def get_client(self) -> FakeRedisClient:
        return self._client

    async def write(
        self,
        agent_id: str,
        session_id: str,
        messages: list[dict[str, str]],
        active_plan: str | None = None,
        temp_variables: dict[str, Any] | None = None,
        ttl_seconds: int = 1800,
    ):
        from mems.schemas import MemsL0Working

        l0 = MemsL0Working(
            agent_id=agent_id,
            session_id=session_id,
            short_term_buffer=messages,
            active_plan=active_plan,
            temp_variables=temp_variables or {},
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )
        self._store[(agent_id, session_id)] = l0
        return l0

    async def read(self, agent_id: str, session_id: str):
        return self._store.get((agent_id, session_id))

    async def delete(self, agent_id: str, session_id: str) -> bool:
        return self._store.pop((agent_id, session_id), None) is not None


class FakeEmbeddingService:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            score = float(len(text))
            vectors.append([score, score / 10.0, 1.0])
        return vectors


class FakeVectorService:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    async def get_collections(self) -> list[str]:
        return list(self.collections.keys())

    async def upsert(self, collection_name: str, points: list[dict[str, Any]]) -> bool:
        collection = self.collections.setdefault(collection_name, {})
        for point in points:
            collection[str(point["id"])] = {
                "id": str(point["id"]),
                "vector": point["vector"],
                "payload": point.get("payload", {}),
            }
        return True

    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        top_k: int = 5,
        filter_agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        collection = self.collections.get(collection_name, {})
        results = []
        for point in collection.values():
            payload = point.get("payload", {})
            if filter_agent_id and payload.get("agent_id") != filter_agent_id:
                continue
            score = 1.0 / (1.0 + abs(point["vector"][0] - query_vector[0]))
            if payload.get("memory_type") == "l2_summary":
                score += 0.1
            results.append({"id": point["id"], "score": score, "payload": payload})
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:top_k]


@pytest.fixture
def test_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    redis_service = FakeRedisService()
    vector_service = FakeVectorService()
    embedding_service = FakeEmbeddingService()

    async def get_fake_redis_service():
        return redis_service

    async def get_fake_vector_service():
        return vector_service

    async def get_fake_embedding_service():
        return embedding_service

    async def fake_llm_chat(
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
    ) -> str:
        _ = (model, temperature)
        content = messages[-1]["content"]
        if "New Candidates:" in content:
            start = content.index("New Candidates:") + len("New Candidates:")
            end = content.index("Existing L2 Context:", start)
            return content[start:end].strip()

        payload = {
            "discarded": [],
            "profile_updates": [],
            "facts": [],
            "events": [],
            "conflict_candidates": [],
            "long_term_summary": "User preferences and long term interests were updated.",
        }
        if "Python" in content:
            payload["profile_updates"].append(
                {
                    "category": "like",
                    "key": "technology",
                    "value": "Python",
                    "confidence": 0.9,
                    "evidence": "I like Python",
                }
            )
            payload["facts"].append(
                {
                    "subject": "用户",
                    "relation": "喜欢",
                    "object": "Python",
                    "fact_type": "tech",
                    "confidence": 0.9,
                    "evidence": "I like Python",
                }
            )
        if "Rust" in content:
            payload["profile_updates"].append(
                {
                    "category": "like",
                    "key": "technology",
                    "value": "Rust",
                    "confidence": 0.9,
                    "evidence": "I prefer Rust now",
                }
            )
            payload["facts"].append(
                {
                    "subject": "用户",
                    "relation": "主要使用",
                    "object": "Rust",
                    "fact_type": "tech",
                    "confidence": 0.9,
                    "evidence": "I prefer Rust now",
                }
            )
        if "memory systems" in content or "长期记忆系统" in content:
            payload["facts"].append(
                {
                    "subject": "用户",
                    "relation": "喜欢",
                    "object": "长期记忆系统",
                    "fact_type": "project",
                    "confidence": 0.9,
                    "evidence": "long term memory systems",
                }
            )
            payload["events"].append(
                {
                    "subject": "用户",
                    "action": "关注",
                    "object": "长期记忆系统",
                    "time_hint": "recent",
                    "importance": 8,
                    "evidence": "long term memory systems",
                }
            )
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(
        "mems.routers.memories.get_redis_service", get_fake_redis_service
    )
    monkeypatch.setattr(
        "mems.routers.monitor.get_redis_service", get_fake_redis_service
    )
    monkeypatch.setattr(
        "mems.services.l0_sync.get_redis_service", get_fake_redis_service, raising=False
    )

    monkeypatch.setattr(
        "mems.routers.memories.get_vector_service", get_fake_vector_service
    )
    monkeypatch.setattr(
        "mems.routers.monitor.get_vector_service", get_fake_vector_service
    )
    monkeypatch.setattr(
        "mems.services.l0_sync.get_vector_service", get_fake_vector_service
    )

    monkeypatch.setattr(
        "mems.routers.memories.get_embedding_service", get_fake_embedding_service
    )
    monkeypatch.setattr(
        "mems.services.l0_sync.get_embedding_service", get_fake_embedding_service
    )

    monkeypatch.setattr("mems.services.distill.llm_chat", fake_llm_chat)
    monkeypatch.setattr("mems.services.distill.engine", engine)
    monkeypatch.setattr("mems.routers.monitor.engine", engine)

    app.dependency_overrides[get_session] = override_get_session

    try:
        yield app, engine, redis_service, vector_service
    finally:
        app.dependency_overrides.clear()
