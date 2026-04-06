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
from mems.services.redis_service import get_redis_service


@dataclass
class FakeRedisClient:
    service: "FakeRedisService"

    async def ping(self) -> bool:
        """Return a successful ping for monitor tests.

        为监控测试返回成功的 ping 结果。
        """
        return True


class FakeRedisService:
    def __init__(self) -> None:
        """Initialize the in-memory Redis replacement used in tests.

        初始化测试环境使用的内存版 Redis 替身。
        """
        self._store: dict[tuple[str | None, str | None, str | None, str, str], Any] = {}
        self._client = FakeRedisClient(self)

    async def get_client(self) -> FakeRedisClient:
        """Return the fake Redis client object.

        返回伪造的 Redis 客户端对象。
        """
        return self._client

    async def write(
        self,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str,
        session_id: str,
        messages: list[dict[str, str]],
        scope: str | None = None,
        active_plan: str | None = None,
        temp_variables: dict[str, Any] | None = None,
        ttl_seconds: int = 1800,
    ):
        """Store one L0 snapshot in the in-memory test backend.

        在测试用内存后端中保存一条 L0 快照。
        """
        from mems.schemas import MemsL0Working

        l0 = MemsL0Working(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            scope=scope,
            short_term_buffer=messages,
            active_plan=active_plan,
            temp_variables=temp_variables or {},
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )
        self._store[(tenant_id, user_id, scope, agent_id, session_id)] = l0
        return l0

    async def read(
        self,
        agent_id: str,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        scope: str | None = None,
    ):
        """Read one L0 snapshot from the in-memory test backend.

        从测试用内存后端读取一条 L0 快照。
        """
        return self._store.get((tenant_id, user_id, scope, agent_id, session_id))

    async def delete(
        self,
        agent_id: str,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        scope: str | None = None,
    ) -> bool:
        """Delete one L0 snapshot from the in-memory test backend.

        从测试用内存后端删除一条 L0 快照。
        """
        return (
            self._store.pop((tenant_id, user_id, scope, agent_id, session_id), None)
            is not None
        )

    async def append_messages(
        self,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str,
        session_id: str,
        messages: list[dict[str, str]],
        ttl_seconds: int = 1800,
        max_buffer_size: int = 10,
        scope: str | None = None,
        active_plan: str | None = None,
        temp_variables: dict[str, Any] | None = None,
    ):
        """Append multiple messages in the in-memory test backend.

        在测试用内存后端中追加多条消息。
        """
        existing = self._store.get((tenant_id, user_id, scope, agent_id, session_id))
        if existing is None:
            return await self.write(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                messages=messages[-max_buffer_size:],
                scope=scope,
                active_plan=active_plan,
                temp_variables=temp_variables,
                ttl_seconds=ttl_seconds,
            )

        merged_variables = dict(existing.temp_variables)
        if temp_variables:
            merged_variables.update(temp_variables)

        return await self.write(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            messages=(existing.short_term_buffer + messages)[-max_buffer_size:],
            scope=scope if scope is not None else existing.scope,
            active_plan=active_plan
            if active_plan is not None
            else existing.active_plan,
            temp_variables=merged_variables,
            ttl_seconds=ttl_seconds,
        )


class FakeEmbeddingService:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return deterministic vectors derived from text length.

        根据文本长度返回可预测的测试向量。
        """
        vectors = []
        for text in texts:
            score = float(len(text))
            vectors.append([score, score / 10.0, 1.0])
        return vectors


class FakeVectorService:
    def __init__(self) -> None:
        """Initialize the in-memory vector collection store.

        初始化内存版向量集合存储。
        """
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    async def get_collections(self) -> list[str]:
        """List fake vector collection names.

        列出伪造向量集合名称。
        """
        return list(self.collections.keys())

    async def upsert(self, collection_name: str, points: list[dict[str, Any]]) -> bool:
        """Insert or update fake vector points in memory.

        在内存中插入或更新伪造向量点。
        """
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
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a simple deterministic search over fake vectors.

        对伪造向量执行一个简单且确定性的搜索。
        """
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


@pytest.fixture
def test_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build an isolated FastAPI app with fake infra dependencies.

    构建一个使用伪造基础设施依赖的隔离 FastAPI 测试应用。
    """
    db_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        """Yield sessions bound to the temporary test database.

        提供绑定到临时测试数据库的会话。
        """
        with Session(engine) as session:
            yield session

    redis_service = FakeRedisService()
    vector_service = FakeVectorService()
    embedding_service = FakeEmbeddingService()

    async def get_fake_redis_service():
        """Return the fake Redis service singleton for tests.

        返回测试环境中的伪造 Redis 服务单例。
        """
        return redis_service

    async def get_fake_vector_service():
        """Return the fake vector service singleton for tests.

        返回测试环境中的伪造向量服务单例。
        """
        return vector_service

    async def get_fake_embedding_service():
        """Return the fake embedding service singleton for tests.

        返回测试环境中的伪造 embedding 服务单例。
        """
        return embedding_service

    async def fake_llm_chat(
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
    ) -> str:
        """Return deterministic extraction JSON for distillation tests.

        为蒸馏测试返回确定性的提取结果 JSON。
        """
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
        "mems.routers.memories.get_vector_service", get_fake_vector_service
    )
    monkeypatch.setattr(
        "mems.services.l0_sync.get_vector_service", get_fake_vector_service
    )
    monkeypatch.setattr(
        "mems.services.distill.get_vector_service", get_fake_vector_service
    )

    monkeypatch.setattr(
        "mems.routers.memories.get_embedding_service", get_fake_embedding_service
    )
    monkeypatch.setattr(
        "mems.services.l0_sync.get_embedding_service", get_fake_embedding_service
    )

    monkeypatch.setattr("mems.services.distill.llm_chat", fake_llm_chat)
    monkeypatch.setattr("mems.services.distill.engine", engine)
    monkeypatch.setattr("mems.routers.memories.engine", engine)

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_redis_service] = get_fake_redis_service

    try:
        yield app, engine, redis_service, vector_service
    finally:
        app.dependency_overrides.clear()
