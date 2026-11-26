from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import Session, select

from mems.application.distill_service import DistillApplicationService
from mems.config import settings
from mems.domain.identity import MemoryIdentity
from mems.domain.knowledge import (
    DistillEventItem,
    DistillFactItem,
    DistillSource,
    Fact,
    Summary,
)
from mems.domain.memory import EpisodicMemory
from mems.domain.services.reconciliation import ReconciliationService
from mems.infrastructure.persistence import models as orm
from mems.infrastructure.persistence.mappers import episodic_from_orm
from mems.infrastructure.persistence.repositories import (
    RepositoryBundle,
    build_repositories,
)
from mems.infrastructure.persistence.repositories.knowledge import (
    SqlKnowledgeRepository,
)
from mems.infrastructure.persistence.uow import SqlUnitOfWork

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class PayloadLLM:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def chat(
        self,
        messages,
        model=None,
        temperature=0.7,
        json_mode=False,
    ) -> str:
        _ = (messages, model, temperature, json_mode)
        return json.dumps(self._payload, ensure_ascii=False)


def _identity(agent_id: str) -> MemoryIdentity:
    return MemoryIdentity(
        tenant_id="tenant",
        user_id="user",
        agent_id=agent_id,
        session_id="session",
    )


def _memory(agent_id: str, content: str = "I use Python") -> EpisodicMemory:
    return EpisodicMemory.create(
        identity=_identity(agent_id),
        content=content,
        messages=[{"role": "user", "content": content}],
        importance_score=0.9,
        now=NOW,
    )


def _add_memory(engine, agent_id: str, content: str) -> int:
    with Session(engine) as session:
        memory = _memory(agent_id, content)
        memory_id = build_repositories(session).episodic.add(memory)
        session.commit()
        assert memory_id is not None
        return memory_id


def _source(memory: EpisodicMemory) -> DistillSource:
    return DistillSource.from_episodic(memory)


def _service(
    session: Session,
    payload: dict,
    knowledge: SqlKnowledgeRepository | None = None,
) -> tuple[DistillApplicationService, RepositoryBundle, SqlUnitOfWork]:
    repos = build_repositories(session)
    uow = SqlUnitOfWork(session)
    service = DistillApplicationService(
        episodic=repos.episodic,
        knowledge=knowledge or repos.knowledge,
        outbox=repos.outbox,
        llm=PayloadLLM(payload),
        clock=FixedClock(),
        uow=uow,
    )
    return service, repos, uow


def test_distill_rolls_back_partial_l2_and_leaves_source_pending(test_app, monkeypatch):
    _, engine, _, _ = test_app
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    payload = {
        "profile_updates": [
            {"category": "like", "key": "language", "value": "Python"},
            {"category": "like", "key": "editor", "value": "Vim"},
        ]
    }

    class FailingKnowledgeRepository(SqlKnowledgeRepository):
        def __init__(self, session: Session) -> None:
            super().__init__(session)
            self.profile_writes = 0

        def save_profile(self, profile):
            saved = super().save_profile(profile)
            self.profile_writes += 1
            if self.profile_writes == 2:
                raise RuntimeError("injected L2 failure")
            return saved

    with Session(engine) as session:
        memory = _memory("agent_partial")
        repos = build_repositories(session)
        memory_id = repos.episodic.add(memory)
        session.commit()
        service, _, uow = _service(session, payload, FailingKnowledgeRepository(session))
        result = asyncio.run(service.distill("agent_partial", force=True))
        assert result.distilled_count == 0
        assert result.l2_created == 0
        uow.commit()

    with Session(engine) as session:
        assert session.exec(select(orm.MemsL2ProfileItem)).all() == []
        source = session.get(orm.MemsL1Episodic, memory_id)
        assert source is not None
        assert source.is_distilled is False
        assert source.version == 1


def test_finalize_failure_rolls_back_l2_and_source_update(
    test_app,
):
    _, engine, _, _ = test_app
    payload = {
        "profile_updates": [{"category": "like", "key": "language", "value": "Python"}],
        "long_term_summary": "summary finalized with the source",
    }

    with Session(engine) as session:
        memory = _memory("agent_finalize")
        repos = build_repositories(session)
        memory_id = repos.episodic.add(memory)
        session.commit()
        service, repos, uow = _service(session, payload)

        def finalize() -> None:
            memory.mark_distilled()
            repos.episodic.save(memory)
            raise RuntimeError("injected finalize failure")

        with pytest.raises(RuntimeError, match="finalize failure"):
            asyncio.run(service.process_source(_source(memory), finalize=finalize))
        uow.commit()

    with Session(engine) as session:
        assert session.exec(select(orm.MemsL2ProfileItem)).all() == []
        assert session.exec(select(orm.MemsL2Summary)).all() == []
        assert session.exec(select(orm.MemsOutbox)).all() == []
        source = session.get(orm.MemsL1Episodic, memory_id)
        assert source is not None
        assert source.is_distilled is False
        assert source.version == 1


def test_pending_distill_count_excludes_archived_rows(test_app):
    _, engine, _, _ = test_app
    memory_id = _add_memory(engine, "agent_archived_count", "Archived pending memory")
    with Session(engine) as session:
        memory = session.get(orm.MemsL1Episodic, memory_id)
        assert memory is not None
        memory.is_archived = True
        session.add(memory)
        session.commit()
        repo = build_repositories(session).episodic
        assert repo.count_pending_distill() == 0
        assert "agent_archived_count" not in repo.list_agents_with_pending_distill()


def test_find_for_distill_skips_locked_rows_and_archived_rows(test_app):
    _, engine, _, _ = test_app
    agent_id = "agent_locks"
    archived_id = _add_memory(engine, agent_id, "archived")
    first_id = _add_memory(engine, agent_id, "first")
    second_id = _add_memory(engine, agent_id, "second")
    with Session(engine) as session:
        archived = session.get(orm.MemsL1Episodic, archived_id)
        assert archived is not None
        archived.is_archived = True
        session.add(archived)
        session.commit()

    identity = _identity(agent_id)
    with Session(engine) as first, Session(engine) as second:
        first_repo = build_repositories(first).episodic
        second_repo = build_repositories(second).episodic
        first_batch = first_repo.find_for_distill(
            agent_id,
            1,
            False,
            0.0,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            namespace=identity.namespace_key,
        )
        second_batch = second_repo.find_for_distill(
            agent_id,
            1,
            False,
            0.0,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            namespace=identity.namespace_key,
        )
        assert len(first_batch) == len(second_batch) == 1
        assert first_batch[0].id != second_batch[0].id
        assert {first_batch[0].id, second_batch[0].id} == {first_id, second_id}
        first.rollback()

    with Session(engine) as session:
        repo = build_repositories(session).episodic
        forced = repo.find_for_distill(
            agent_id,
            10,
            True,
            0.0,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            namespace=identity.namespace_key,
        )
        assert {item.id for item in forced} == {first_id, second_id}


def test_episodic_save_rejects_stale_version(test_app):
    _, engine, _, _ = test_app
    memory_id = _add_memory(engine, "agent_stale", "original")

    with Session(engine) as first, Session(engine) as stale:
        first_row = first.get(orm.MemsL1Episodic, memory_id)
        stale_row = stale.get(orm.MemsL1Episodic, memory_id)
        assert first_row is not None and stale_row is not None
        first_memory = episodic_from_orm(first_row)
        stale_memory = episodic_from_orm(stale_row)

        first_memory.mark_distilled()
        build_repositories(first).episodic.save(first_memory)
        first.commit()

        stale_memory.content = "stale overwrite"
        with pytest.raises(StaleDataError):
            build_repositories(stale).episodic.save(stale_memory)
        stale.rollback()

    with Session(engine) as session:
        row = session.get(orm.MemsL1Episodic, memory_id)
        assert row is not None
        assert row.version == 2
        assert row.content == "original"
        assert row.is_distilled is True


def test_event_replay_merges_sources_into_one_canonical_row(test_app):
    _, engine, _, _ = test_app
    identity = _identity("agent_events")

    with Session(engine) as session:
        reconciler = ReconciliationService(build_repositories(session).knowledge, FixedClock())
        reconciler.commit_event(
            identity,
            DistillEventItem(
                subject=" user ",
                action=" started ",
                object=" Mems ",
                time_hint=" recent ",
            ),
            10,
        )
        SqlUnitOfWork(session).commit()

    with Session(engine) as session:
        reconciler = ReconciliationService(build_repositories(session).knowledge, FixedClock())
        reconciler.commit_event(
            identity,
            DistillEventItem(
                subject="user",
                action="started",
                object="Mems",
                time_hint="recent",
            ),
            20,
        )
        reconciler.commit_event(
            identity,
            DistillEventItem(
                subject="user",
                action="started",
                object="Mems",
                time_hint="recent",
            ),
            10,
        )
        SqlUnitOfWork(session).commit()

    with Session(engine) as session:
        rows = session.exec(select(orm.MemsL2Event)).all()
        assert len(rows) == 1
        assert rows[0].source_l1_ids == [10, 20]
        assert rows[0].semantic_hash is not None

        other_identity = MemoryIdentity(
            tenant_id="other",
            user_id="user",
            agent_id="agent_events",
            session_id="session",
        )
        other = ReconciliationService(
            build_repositories(session).knowledge, FixedClock()
        ).commit_event(
            other_identity,
            DistillEventItem(subject="user", action="started", object="Mems", time_hint="recent"),
            30,
        )
        assert other == 1
        SqlUnitOfWork(session).commit()
        assert len(session.exec(select(orm.MemsL2Event)).all()) == 2


def test_summary_reuse_targets_latest_created_at_then_id(test_app):
    _, engine, _, _ = test_app
    identity = _identity("agent_summary")
    payload = {"long_term_summary": "latest"}

    with Session(engine) as session:
        repos = build_repositories(session)
        for content in ("older", "latest"):
            repos.knowledge.save_summary(
                Summary(
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    agent_id=identity.agent_id,
                    namespace=identity.namespace_key,
                    content=content,
                    created_at=NOW,
                    last_verified_at=NOW,
                    source_l1_ids=[1],
                )
            )
        session.commit()
        service, _, uow = _service(session, payload)
        memory = _memory(identity.agent_id)
        memory.id = 2
        created, updated = asyncio.run(service.process_source(_source(memory)))
        assert (created, updated) == (0, 0)
        uow.commit()

    with Session(engine) as session:
        rows = session.exec(select(orm.MemsL2Summary).order_by(orm.MemsL2Summary.id)).all()
        assert len(rows) == 2
        assert rows[0].source_l1_ids == [1]
        assert rows[1].source_l1_ids == [1, 2]


def test_concurrent_profile_reconciliation_retries_unique_conflict(test_app):
    _, engine, _, _ = test_app
    identity = _identity("agent_profile_race")
    barrier = Barrier(2)

    def reconcile(value: str, source_id: int):
        payload = {
            "profile_updates": [
                {
                    "category": "like",
                    "key": "technology",
                    "value": value,
                    "confidence": 0.9,
                }
            ]
        }
        with Session(engine) as session:
            service, _, _ = _service(session, payload)
            original = service._knowledge.get_active_profile
            first_read = True

            def synchronized_get(*args, **kwargs):
                nonlocal first_read
                result = original(*args, **kwargs)
                if first_read:
                    first_read = False
                    barrier.wait(timeout=10)
                return result

            service._knowledge.get_active_profile = synchronized_get
            source = DistillSource(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                session_id=identity.session_id,
                namespace=identity.namespace_key,
                content="The user strongly prefers a backend language.",
                source_id=source_id,
                created_at=NOW,
            )
            asyncio.run(service.process_source(source))
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reconcile, "Python", 101), pool.submit(reconcile, "Rust", 102)]
        for future in futures:
            future.result(timeout=20)

    with Session(engine) as session:
        rows = session.exec(
            select(orm.MemsL2ProfileItem).where(
                orm.MemsL2ProfileItem.agent_id == identity.agent_id
            )
        ).all()
        active = [row for row in rows if row.status == "active"]
        assert len(active) == 1
        assert sorted(row.version for row in rows) == [1, 2]
        assert {source for row in rows for source in row.source_l1_ids} == {101, 102}


def test_concurrent_single_value_facts_are_serialized(test_app):
    _, engine, _, _ = test_app
    identity = _identity("agent_fact_race")
    barrier = Barrier(2)

    def reconcile(value: str):
        payload = {
            "facts": [
                {
                    "subject": "user",
                    "relation": "主要使用",
                    "object": value,
                    "confidence": 0.9,
                }
            ]
        }
        with Session(engine) as session:
            service, _, _ = _service(session, payload)
            source = DistillSource(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                session_id=identity.session_id,
                namespace=identity.namespace_key,
                content="The user mainly uses one backend language.",
                source_id=201 if value == "Python" else 202,
                created_at=NOW,
            )
            barrier.wait(timeout=10)
            asyncio.run(service.process_source(source))
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reconcile, "Python"), pool.submit(reconcile, "Rust")]
        for future in futures:
            future.result(timeout=20)

    with Session(engine) as session:
        rows = session.exec(
            select(orm.MemsL2Fact).where(orm.MemsL2Fact.agent_id == identity.agent_id)
        ).all()
        assert len([row for row in rows if row.status == "active"]) == 1
        assert len([row for row in rows if row.status == "superseded"]) == 1


def test_reconcile_fact_supersedes_all_conflicts_and_creates_one_fact(test_app):
    _, engine, _, _ = test_app
    identity = _identity("agent_facts")

    with Session(engine) as session:
        repos = build_repositories(session)
        for value, version in (("A", 2), ("B", 5), ("C", 3)):
            fact = Fact(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                namespace=identity.namespace_key,
                subject="user",
                predicate="主要使用",
                object=value,
                version=version,
                first_seen_at=NOW,
                last_verified_at=NOW,
            )
            repos.knowledge.save_fact(fact)
        session.commit()

        result = ReconciliationService(repos.knowledge, FixedClock()).reconcile_fact(
            identity,
            DistillFactItem(subject="user", relation="主要使用", object="D", confidence=0.9),
            99,
        )
        assert result == (1, 3)
        SqlUnitOfWork(session).commit()

    with Session(engine) as session:
        facts = session.exec(select(orm.MemsL2Fact)).all()
        active = [fact for fact in facts if fact.status == "active"]
        assert len(active) == 1
        assert active[0].object == "D"
        assert active[0].version == 6
        assert all(fact.status == "superseded" for fact in facts if fact != active[0])
        conflicts = session.exec(select(orm.MemsL2ConflictLog)).all()
        assert len(conflicts) == 3


@pytest.mark.parametrize("response", ["", "   ", "{}", '{"unexpected": []}', "[]"])
def test_empty_or_unrecognized_llm_response_is_retried_not_finalized(test_app, response):
    _, engine, _, _ = test_app
    source_id = _add_memory(engine, "empty_response", "I use Python for work")

    class EmptyLLM:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return response

    with Session(engine) as session:
        service, _, _ = _service(session, {})
        llm = EmptyLLM()
        service._llm = llm
        result = asyncio.run(service.distill(
            "empty_response", tenant_id="tenant", user_id="user",
            namespace=_identity("empty_response").namespace_key,
        ))
        assert not result.success
        assert result.distilled_count == 0
        assert llm.calls == 2
        assert not session.get(orm.MemsL1Episodic, source_id).is_distilled
        assert not session.exec(select(orm.MemsL2ProfileItem)).all()


def test_explicit_empty_extraction_can_finalize_source(test_app):
    _, engine, _, _ = test_app
    source_id = _add_memory(engine, "discarded", "A long enough memory to review")
    with Session(engine) as session:
        service, _, _ = _service(session, {"facts": [], "discarded": []})
        result = asyncio.run(service.distill(
            "discarded", tenant_id="tenant", user_id="user",
            namespace=_identity("discarded").namespace_key,
        ))
        assert result.success
        assert session.get(orm.MemsL1Episodic, source_id).is_distilled


def test_reconciliation_uses_source_time_not_importance_or_processing_time(test_app):
    from datetime import timedelta

    from mems.domain.knowledge import DistillProfileUpdate

    _, engine, _, _ = test_app
    identity = _identity("source_order")
    old = NOW - timedelta(days=10)
    with Session(engine) as session:
        repos = build_repositories(session)
        sources = []
        for value, timestamp in [("Python", old), ("Rust", NOW)]:
            memory = _memory(identity.agent_id, f"I use {value}")
            memory.created_at = timestamp
            repos.episodic.add(memory)
            sources.append(memory)
        session.commit()
        reconcile = ReconciliationService(repos.knowledge, FixedClock())
        for memory, value in [(sources[1], "Rust"), (sources[0], "Python")]:
            reconcile.reconcile_profile(identity, DistillProfileUpdate(
                category="tech", key="primary", value=value,
            ), memory.id, memory.created_at)
            reconcile.reconcile_fact(identity, DistillFactItem(
                subject="user", relation="主要使用", object=value,
            ), memory.id, memory.created_at)
        session.commit()
        assert [item.value for item in repos.knowledge.list_active_profiles(identity)] == ["Rust"]
        assert [item.object for item in repos.knowledge.list_active_facts(identity)] == ["Rust"]
        # Archive-only evidence uses persisted source dates and preserves provenance.
        newer = NOW + timedelta(days=1)
        reconcile.reconcile_profile(identity, DistillProfileUpdate(
            category="tech", key="primary", value="Go",
        ), 99999, newer)
        reconcile.reconcile_profile(identity, DistillProfileUpdate(
            category="tech", key="primary", value="Go",
        ), 99998, old)
        reconcile.reconcile_profile(identity, DistillProfileUpdate(
            category="tech", key="primary", value="Python",
        ), 99997, NOW)
        session.commit()
        profile = repos.knowledge.list_active_profiles(identity)[0]
        assert profile.value == "Go"
        assert profile.last_verified_at == newer
        assert set(profile.source_l1_ids) == {99998, 99999}


def test_distill_batch_keeps_newer_values_when_importance_reorders_sources(test_app):
    from datetime import timedelta

    _, engine, _, _ = test_app
    identity = _identity("importance_order")
    with Session(engine) as session:
        repos = build_repositories(session)
        for value, days_old, importance in [("Python", 10, 0.5), ("Rust", 0, 0.9)]:
            memory = _memory(identity.agent_id, f"I use {value}")
            memory.created_at = NOW - timedelta(days=days_old)
            memory.importance_score = importance
            repos.episodic.add(memory)
        session.commit()
        service, _, _ = _service(session, {})

        class SourceLLM:
            async def chat(self, messages, **kwargs):
                value = "Rust" if "Rust" in messages[-1]["content"] else "Python"
                return json.dumps({
                    "profile_updates": [{"category": "tech", "key": "primary", "value": value}],
                    "facts": [{"subject": "user", "relation": "主要使用", "object": value}],
                })

        service._llm = SourceLLM()
        response = asyncio.run(service.distill(
            identity.agent_id, batch_size=50, tenant_id=identity.tenant_id,
            user_id=identity.user_id, namespace=identity.namespace_key,
        ))
        assert response.success
        assert response.distilled_count == 2
        assert [item.value for item in repos.knowledge.list_active_profiles(identity)] == ["Rust"]
        assert [item.object for item in repos.knowledge.list_active_facts(identity)] == ["Rust"]
