from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import SecretStr, ValidationError

from mems.config import Settings
from mems.domain.identity import MemoryIdentity
from mems.domain.identity import MemoryNamespace
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

    def source_created_at(self, identity, source_ids):
        return None

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

    def lock_fact_scope(self, identity, subject, predicate):
        return None

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
        namespace_segments=("p",),
    )
    expected_namespace = MemoryNamespace.build(
        tenant_id="t", user_id="u", agent_id="a", path=("p",)
    ).key
    assert identity.redis_key().startswith("mems:l0:")
    assert identity.filter_fields() == {
        "tenant_id": "t",
        "user_id": "u",
        "namespace": expected_namespace,
    }
    assert identity.namespace_key == expected_namespace
    assert identity == MemoryIdentity(
        tenant_id="t",
        user_id="u",
        agent_id="a",
        session_id="s",
        namespace_segments=("p",),
    )


def test_working_memory_merge_trims_buffer():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = WorkingMemory(
        tenant_id=None,
        user_id=None,
        agent_id="a",
        session_id="s",
        namespace=None,
        content="old",
        short_term_buffer=[{"role": "user", "content": "one"}],
        expires_at=now,
    )
    merged = existing.merge_append(
        [{"role": "user", "content": "two"}, {"role": "user", "content": "three"}],
        content=None,
        namespace=None,
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
    assert filter_record("booked a room for tonight") is None
    assert filter_record("The token budget is sufficient") is None
    assert filter_record("ok thanks") == "greeting"
    assert filter_record("谢谢你的帮助") == "greeting"
    assert filter_record("我喜欢Rust") is None

    memory = EpisodicMemory(agent_id="a", content="x", vector_id="v")
    assert eligible_for_distill(memory, force=False, min_importance=0.5)
    memory.is_distilled = True
    assert not eligible_for_distill(memory, force=False, min_importance=0.5)
    assert eligible_for_distill(memory, force=True, min_importance=0.5)


def test_config_defaults_and_secret_values():
    config = Settings(
        _env_file=None,
        OPENAI_API_KEY="llm-secret",
        REDIS_PASSWORD="redis-secret",
        QDRANT_API_KEY="qdrant-secret",
        OPENAI_EMBEDDING_API_KEY="embedding-secret",
    )
    assert config.APP_ENV == "development"
    assert config.OUTBOX_LEASE_SECONDS == 300
    assert config.OUTBOX_MAX_ATTEMPTS == 5
    assert isinstance(config.OPENAI_API_KEY, SecretStr)
    assert config.OPENAI_API_KEY.get_secret_value() == "llm-secret"
    assert config.REDIS_PASSWORD.get_secret_value() == "redis-secret"
    assert config.QDRANT_API_KEY.get_secret_value() == "qdrant-secret"
    assert isinstance(config.OPENAI_EMBEDDING_API_KEY, SecretStr)
    assert config.OPENAI_EMBEDDING_API_KEY.get_secret_value() == "embedding-secret"
    assert "llm-secret" not in repr(config.OPENAI_API_KEY)


def test_local_embedding_does_not_require_openai_credentials():
    config = Settings(_env_file=None, EMBEDDING_PROVIDER="sentence-transformers")
    assert config.EMBEDDING_PROVIDER == "sentence-transformers"
    assert config.OPENAI_API_KEY.get_secret_value() == ""


def test_openai_providers_validate_credentials():
    config = Settings(
        _env_file=None,
        EMBEDDING_PROVIDER="openai",
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="shared-secret",
    )
    assert config.OPENAI_API_KEY.get_secret_value() == "shared-secret"
    with pytest.raises(ValidationError, match="OPENAI_EMBEDDING_API_KEY"):
        Settings(_env_file=None, EMBEDDING_PROVIDER="openai")
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(_env_file=None, LLM_PROVIDER="openai")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("OUTBOX_LEASE_SECONDS", 59),
        ("OUTBOX_MAX_ATTEMPTS", 0),
        ("LOG_HTTP_BODY_MAX_CHARS", -1),
    ],
)
def test_config_rejects_out_of_range_values(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


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
