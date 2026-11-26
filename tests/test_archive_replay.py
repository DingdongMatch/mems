from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from mems.application.archive_service import ArchiveApplicationService
from mems.config import settings
from mems.domain.identity import MemoryIdentity
from mems.domain.memory import EpisodicMemory
from mems.infrastructure.external.clock import system_clock
from mems.infrastructure.persistence.models import MemsL1Episodic, MemsL3Archive
from mems.infrastructure.persistence.repositories import build_repositories
from mems.infrastructure.persistence.uow import SqlUnitOfWork


def test_archive_replay_is_deterministic_and_identity_exact(test_app, tmp_path, monkeypatch):
    _, engine, _, _ = test_app
    monkeypatch.setattr(settings, "ARCHIVE_STORAGE_PATH", str(tmp_path))
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        repos = build_repositories(session)
        for tenant_id in (None, "tenant-other"):
            identity = MemoryIdentity(tenant_id=tenant_id, user_id=None, agent_id="agent-a")
            repos.episodic.add(
                EpisodicMemory.create(
                    identity=identity,
                    content="old memory",
                    messages=[],
                    importance_score=0.5,
                    now=now - timedelta(days=60),
                )
            )
        SqlUnitOfWork(session).commit()

    with Session(engine) as session:
        service = ArchiveApplicationService(
            episodic=build_repositories(session).episodic,
            archive=build_repositories(session).archive,
            outbox=build_repositories(session).outbox,
            clock=system_clock,
            uow=SqlUnitOfWork(session),
        )
        first = asyncio.run(
            service.archive(
                agent_id="agent-a",
                tenant_id=None,
                user_id=None,
                namespace=MemoryIdentity(agent_id="agent-a").namespace_key,
                days=30,
                batch_id="outbox-42",
            )
        )
        assert first.success is True
        assert first.archived_count == 1
        expected_suffix = hashlib.sha256(b"outbox-42").hexdigest()[:16]
        assert first.file_path.endswith(f"_{expected_suffix}.jsonl")

        replay = asyncio.run(
            service.archive(
                agent_id="agent-a",
                tenant_id=None,
                user_id=None,
                namespace=MemoryIdentity(agent_id="agent-a").namespace_key,
                days=30,
                batch_id="outbox-42",
            )
        )
        assert replay.success is True
        assert replay.archived_count == 0

    with Session(engine) as session:
        batches = session.exec(select(MemsL3Archive)).all()
        rows = session.exec(select(MemsL1Episodic)).all()
        assert len(batches) == 1
        assert sum(row.is_archived for row in rows) == 1
        other = next(row for row in rows if row.tenant_id == "tenant-other")
        assert other.is_archived is False


def test_archived_vectors_are_deleted_durably_and_do_not_starve_recall(test_app, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from mems.domain.outbox import OutboxEvent, OutboxEventType
    from mems.infrastructure.persistence.models import MemsOutbox
    from mems.interfaces.bootstrap import (
        build_outbox_worker,
        build_vector_sync_service,
        make_archive_service,
    )

    app, engine, _, vector = test_app
    monkeypatch.setattr(settings, "ARCHIVE_STORAGE_PATH", str(tmp_path))
    identity = MemoryIdentity(agent_id="archive_vectors")
    memories = []
    with Session(engine) as session:
        repos = build_repositories(session)
        # Enough stale hits to require several pages beyond the old top_k * 3.
        for index in range(16):
            memory = EpisodicMemory.create(
                identity=identity, content=f"Python memory {index}", messages=[],
                importance_score=0.5,
                now=datetime.now(timezone.utc) - timedelta(days=60 if index < 15 else 0),
            )
            repos.episodic.add(memory)
            memories.append(memory)
        session.commit()
    for index, memory in enumerate(memories):
        asyncio.run(vector.upsert_episodic(
            f"agent_{identity.agent_id}", memory, [6.0 if index < 15 else 7.0],
        ))
    with Session(engine) as session:
        result = asyncio.run(make_archive_service(session).archive(
            identity.agent_id, namespace=identity.namespace_key,
        ))
        assert result.archived_count == 15
        pending = session.exec(select(MemsOutbox)).all()
        assert len(pending) == 15
        assert all(item.event_type == OutboxEventType.VECTOR_DELETE for item in pending)

    with TestClient(app) as client:
        response = client.post("/v1/mems/query", json={
            "agent_id": identity.agent_id, "query": "Python", "top_k": 1,
        })
        assert response.status_code == 200
        assert response.json()["results"][0]["content"] == "Python memory 15"

    asyncio.run(build_outbox_worker().run_once())
    assert set(vector.collections[f"agent_{identity.agent_id}"]) == {memories[-1].vector_id}

    class UnexpectedEmbedding:
        async def embed(self, texts):
            raise AssertionError("An archived source must not be embedded again")

    # A delayed/replayed vector_sync must not resurrect an archived replica.
    with Session(engine) as session:
        assert asyncio.run(build_vector_sync_service(session, UnexpectedEmbedding(), vector).sync(
            OutboxEvent(
                event_type=OutboxEventType.VECTOR_SYNC, aggregate_id="replay",
                agent_id=identity.agent_id, namespace=identity.namespace_key,
                payload={"kind": "episodic", "vector_id": memories[0].vector_id,
                         "content": memories[0].content, "agent_id": identity.agent_id},
            )
        ))
    assert memories[0].vector_id not in vector.collections[f"agent_{identity.agent_id}"]


def test_vector_sync_holds_sql_lock_through_remote_upsert(test_app, monkeypatch):
    import pytest
    from sqlalchemy.exc import OperationalError

    from mems.domain.outbox import OutboxEvent, OutboxEventType
    from mems.interfaces.bootstrap import build_vector_sync_service

    _, engine, _, vector = test_app
    identity = MemoryIdentity(agent_id="sync_lock")
    memory = EpisodicMemory.create(
        identity=identity, content="Python", messages=[], importance_score=0.5,
        now=datetime.now(timezone.utc),
    )
    with Session(engine) as session:
        build_repositories(session).episodic.add(memory)
        session.commit()

    original = vector.upsert_episodic

    async def upsert(collection_name, record, embedding):
        with Session(engine) as competing:
            with pytest.raises(OperationalError) as error:
                competing.exec(select(MemsL1Episodic).where(
                    MemsL1Episodic.id == memory.id
                ).with_for_update(nowait=True)).one()
            assert error.value.orig.sqlstate == "55P03"
        return await original(collection_name, record, embedding)

    class Embedding:
        async def embed(self, texts):
            return [[1.0]]

    monkeypatch.setattr(vector, "upsert_episodic", upsert)
    with Session(engine) as session:
        assert asyncio.run(build_vector_sync_service(session, Embedding(), vector).sync(
            OutboxEvent(
                event_type=OutboxEventType.VECTOR_SYNC, aggregate_id="sync",
                agent_id=identity.agent_id, namespace=identity.namespace_key,
                payload={"kind": "episodic", "vector_id": memory.vector_id,
                         "agent_id": identity.agent_id, "content": memory.content},
            )
        ))
