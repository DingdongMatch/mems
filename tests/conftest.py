from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
import json
from typing import Any

# Full test suite runs against a dedicated PostgreSQL database.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://mems:mems@localhost:5432/mems_test"
)

import pytest
from sqlmodel import Session, SQLModel, create_engine

from mems.config import settings
from mems.domain.memory import WorkingMemory
from mems.infrastructure.external.redis_service import build_l0_pipeline_event
from mems.infrastructure.persistence.database import SqlSessionFactory, get_session
from mems.interfaces.api import dependencies
from mems.main import app


class FakeLLMPort:
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> str:
        _ = (model, temperature, json_mode)
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


class FakeWorkingMemoryStore:
    def __init__(self) -> None:
        self._store: dict[
            tuple[str | None, str | None, str | None, str, str | None], WorkingMemory
        ] = {}
        self.pipeline_events: list[dict[str, str]] = []

    def _publish(self, l0: WorkingMemory, event_type: str) -> None:
        if not settings.L0_PIPELINE_ENABLED or not settings.L0_PIPELINE_STREAM:
            return
        self.pipeline_events.append(build_l0_pipeline_event(l0, event_type))

    async def ping(self) -> None:
        return None

    async def write(
        self,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str,
        session_id: str | None,
        messages: list[dict[str, str]],
        content: str | None = None,
        namespace: str | None = None,
        ttl_seconds: int = 1800,
        event_type: str = "write",
    ) -> WorkingMemory:
        l0 = WorkingMemory(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            namespace=namespace,
            content=content,
            short_term_buffer=messages,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )
        self._store[(tenant_id, user_id, namespace, agent_id, session_id)] = l0
        self._publish(l0, event_type)
        return l0

    async def read(
        self,
        agent_id: str,
        session_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> WorkingMemory | None:
        return self._store.get((tenant_id, user_id, namespace, agent_id, session_id))

    async def list_active(
        self,
        agent_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        namespace: str | None = None,
        limit: int = 10,
    ) -> list[WorkingMemory]:
        snapshots = []
        for (t, u, s, a, _session), l0 in self._store.items():
            if a != agent_id:
                continue
            if tenant_id is not None and t != tenant_id:
                continue
            if user_id is not None and u != user_id:
                continue
            if namespace is not None and s != namespace:
                continue
            snapshots.append(l0)
        snapshots.sort(key=lambda item: item.expires_at, reverse=True)
        return snapshots[:limit]

    async def delete(
        self,
        agent_id: str,
        session_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> bool:
        return (
            self._store.pop((tenant_id, user_id, namespace, agent_id, session_id), None)
            is not None
        )

    async def append_messages(
        self,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str,
        session_id: str | None,
        messages: list[dict[str, str]],
        content: str | None = None,
        ttl_seconds: int = 1800,
        max_buffer_size: int = 10,
        namespace: str | None = None,
    ) -> WorkingMemory:
        existing = self._store.get((tenant_id, user_id, namespace, agent_id, session_id))
        if existing is None:
            trimmed = messages[-max_buffer_size:] if max_buffer_size > 0 else messages
            return await self.write(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                messages=trimmed,
                content=content,
                namespace=namespace,
                ttl_seconds=ttl_seconds,
            )

        merged = existing.merge_append(messages, content, namespace, max_buffer_size)
        return await self.write(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            messages=merged.short_term_buffer,
            content=merged.content,
            namespace=merged.namespace,
            ttl_seconds=ttl_seconds,
            event_type="append",
        )


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

    async def upsert_episodic(self, collection_name: str, memory, vector) -> bool:
        from mems.infrastructure.external.vector_payload import episodic_payload

        return await self.upsert(
            collection_name,
            [
                {
                    "id": memory.vector_id,
                    "vector": vector,
                    "payload": episodic_payload(memory),
                }
            ],
        )

    async def upsert_summary(self, collection_name: str, summary, vector) -> bool:
        from mems.infrastructure.external.vector_payload import summary_payload

        if summary.vector_id is None:
            return False
        return await self.upsert(
            collection_name,
            [
                {
                    "id": summary.vector_id,
                    "vector": vector,
                    "payload": summary_payload(
                        tenant_id=summary.tenant_id,
                        user_id=summary.user_id,
                        agent_id=summary.agent_id,
                        namespace=summary.namespace,
                        vector_id=summary.vector_id,
                        content=summary.content,
                    ),
                }
            ],
        )

    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        top_k: int = 5,
        filter_agent_id: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        collection = self.collections.get(collection_name, {})
        results = []
        for point in collection.values():
            payload = point.get("payload", {})
            if filter_agent_id and payload.get("agent_id") != filter_agent_id:
                continue
            if filters and any(
                payload.get(key) != value for key, value in filters.items() if value
            ):
                continue
            score = 1.0 / (1.0 + abs(point["vector"][0] - query_vector[0]))
            if payload.get("memory_type") == "l2_summary":
                score += 0.1
            results.append({"id": point["id"], "score": score, "payload": payload})
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:top_k]

    async def delete_points(self, collection_name: str, point_ids: list[str]) -> bool:
        collection = self.collections.get(collection_name, {})
        for point_id in point_ids:
            collection.pop(str(point_id), None)
        return True


@pytest.fixture
def test_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OUTBOX_WORKER_ENABLED", False)

    import mems.infrastructure.persistence.models  # noqa: F401

    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    store = FakeWorkingMemoryStore()
    vector = FakeVectorService()
    embedding = FakeEmbeddingService()
    llm = FakeLLMPort()
    session_factory = SqlSessionFactory(engine)

    async def get_fake_store():
        return store

    async def get_fake_vector():
        return vector

    async def get_fake_embedding():
        return embedding

    def get_fake_llm():
        return llm

    monkeypatch.setattr(
        "mems.interfaces.bootstrap.sql_session_factory", session_factory
    )
    monkeypatch.setattr(
        "mems.interfaces.bootstrap.get_embedding_service", get_fake_embedding
    )
    monkeypatch.setattr(
        "mems.interfaces.bootstrap.get_vector_service", get_fake_vector
    )
    monkeypatch.setattr(
        "mems.interfaces.bootstrap.get_llm_port", get_fake_llm
    )

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[dependencies.get_working_memory_store] = get_fake_store
    app.dependency_overrides[dependencies.get_vector_store] = get_fake_vector
    app.dependency_overrides[dependencies.get_embedding_port] = get_fake_embedding

    try:
        yield app, engine, store, vector
    finally:
        app.dependency_overrides.clear()
