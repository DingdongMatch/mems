from __future__ import annotations

from datetime import datetime, timezone

from mems.domain.identity import MemoryIdentity
from mems.domain.knowledge import (
    DistillFactItem,
    DistillProfileUpdate,
    Fact,
    Profile,
)
from mems.domain.memory import EpisodicMemory, WorkingMemory
from mems.domain.services.distillation import (
    eligible_for_distill,
    filter_record,
    is_high_value,
)
from mems.domain.services.reconciliation import ReconciliationService


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now


class FakeKnowledgeRepository:
    def __init__(self) -> None:
        self.profiles: list[Profile] = []
        self.facts: list[Fact] = []
        self.conflicts = []
        self.events = []
        self.summaries = []
        self._next_id = 1

    def get_active_profile(self, identity, category, key):
        for profile in self.profiles:
            if (
                profile.status == "active"
                and profile.category == category
                and profile.key == key
            ):
                return profile
        return None

    def get_exact_fact(self, identity, subject, predicate, object):
        for fact in self.facts:
            if (
                fact.status == "active"
                and fact.subject == subject
                and fact.predicate == predicate
                and fact.object == object
            ):
                return fact
        return None

    def get_conflicting_facts(self, identity, subject, predicate):
        return [
            fact
            for fact in self.facts
            if fact.status == "active"
            and fact.subject == subject
            and fact.predicate == predicate
        ]

    def save_profile(self, profile):
        if profile.id is None:
            profile.id = self._next_id
            self._next_id += 1
            self.profiles.append(profile)
        return profile

    def save_fact(self, fact):
        if fact.id is None:
            fact.id = self._next_id
            self._next_id += 1
            self.facts.append(fact)
        return fact

    def add_event(self, event):
        event.id = self._next_id
        self._next_id += 1
        self.events.append(event)
        return event

    def add_conflict(self, conflict):
        conflict.id = self._next_id
        self._next_id += 1
        self.conflicts.append(conflict)
        return conflict

    def save_summary(self, summary):
        if summary.id is None:
            summary.id = self._next_id
            self._next_id += 1
            self.summaries.append(summary)
        return summary


def test_memory_identity_key_and_filters():
    identity = MemoryIdentity(
        tenant_id="t",
        user_id="u",
        agent_id="a",
        session_id="s",
        scope="p",
    )
    assert identity.redis_key() == "mems:l0:t:u:p:a:s"
    assert identity.filter_fields() == {
        "tenant_id": "t",
        "user_id": "u",
        "scope": "p",
    }
    assert identity == MemoryIdentity(
        tenant_id="t", user_id="u", agent_id="a", session_id="s", scope="p"
    )


def test_working_memory_merge_trims_buffer():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = WorkingMemory(
        tenant_id=None,
        user_id=None,
        agent_id="a",
        session_id="s",
        scope=None,
        content="old",
        short_term_buffer=[{"role": "user", "content": "one"}],
        expires_at=now,
    )
    merged = existing.merge_append(
        [{"role": "user", "content": "two"}, {"role": "user", "content": "three"}],
        content=None,
        scope=None,
        max_buffer_size=2,
    )
    assert merged.short_term_buffer == [
        {"role": "user", "content": "two"},
        {"role": "user", "content": "three"},
    ]
    assert merged.content == "old"


def test_episodic_memory_state_transitions():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    memory = EpisodicMemory(
        agent_id="a",
        content="content",
        vector_id="v",
        created_at=now,
    )
    memory.mark_distilled()
    assert memory.is_distilled is True
    memory.mark_vector_failed("vector: boom", now)
    assert memory.vector_status == "failed"
    assert memory.last_sync_error == "vector: boom"
    memory.mark_archived(now)
    assert memory.is_archived is True
    assert memory.archive_status == "ready"
    assert memory.last_sync_error is None


def test_distillation_policy():
    assert is_high_value(0.9, 0.8)
    assert not is_high_value(0.7, 0.8)
    assert filter_record("") == "empty"
    assert filter_record("hi") == "low_signal"
    assert filter_record("user: hi\nassistant: ok") == "low_signal"

    memory = EpisodicMemory(agent_id="a", content="x", vector_id="v")
    assert eligible_for_distill(memory, force=False, min_importance=0.5)
    memory.is_distilled = True
    assert not eligible_for_distill(memory, force=False, min_importance=0.5)
    assert eligible_for_distill(memory, force=True, min_importance=0.5)


def test_reconciliation_profile_creates_and_supersedes():
    repo = FakeKnowledgeRepository()
    clock = FakeClock()
    service = ReconciliationService(repo, clock)
    identity = MemoryIdentity(agent_id="a")

    created, updated = service.reconcile_profile(
        identity,
        DistillProfileUpdate(category="like", key="tech", value="Python"),
        source_id=1,
    )
    assert (created, updated) == (1, 0)
    assert repo.profiles[0].value == "Python"

    created, updated = service.reconcile_profile(
        identity,
        DistillProfileUpdate(category="like", key="tech", value="Rust"),
        source_id=2,
    )
    assert (created, updated) == (1, 1)
    old = repo.profiles[0]
    new = repo.profiles[-1]
    assert old.status == "superseded"
    assert new.status == "active"
    assert new.version == old.version + 1
    assert new.supersedes_id == old.id
    assert any(conflict.memory_type == "profile" for conflict in repo.conflicts)


def test_reconciliation_fact_multi_value_coexists():
    repo = FakeKnowledgeRepository()
    service = ReconciliationService(repo, FakeClock())
    identity = MemoryIdentity(agent_id="a")

    service.reconcile_fact(
        identity,
        DistillFactItem(subject="用户", relation="喜欢", object="Python"),
        source_id=1,
    )
    service.reconcile_fact(
        identity,
        DistillFactItem(subject="用户", relation="喜欢", object="Rust"),
        source_id=2,
    )
    assert [fact.object for fact in repo.facts] == ["Python", "Rust"]
    assert all(fact.status == "active" for fact in repo.facts)
    assert not repo.conflicts
