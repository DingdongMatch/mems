from __future__ import annotations

from datetime import datetime, timezone

from mems.domain.knowledge import Fact, Profile
from mems.domain.memory import EpisodicMemory
from mems.infrastructure.persistence import models as orm
from mems.infrastructure.persistence.mappers import (
    episodic_from_orm,
    episodic_to_orm,
    fact_from_orm,
    fact_to_orm,
    profile_from_orm,
    profile_to_orm,
)


def test_episodic_mapper_round_trip():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    memory = EpisodicMemory(
        tenant_id="t",
        user_id="u",
        agent_id="a",
        session_id="s",
        scope="p",
        content="content",
        messages=[{"role": "user", "content": "hello"}],
        vector_id="v",
        importance_score=0.8,
        created_at=now,
    )
    row = orm.MemsL1Episodic()
    episodic_to_orm(memory, row)
    assert row.messages_json == memory.messages
    restored = episodic_from_orm(row)
    assert restored.messages == memory.messages
    assert restored.vector_id == "v"
    assert restored.created_at == now


def test_profile_and_fact_mappers_round_trip():
    profile = Profile(
        tenant_id="t",
        user_id="u",
        agent_id="a",
        scope="p",
        category="like",
        key="tech",
        value="Python",
        source_l1_ids=[1, 2],
    )
    profile_row = orm.MemsL2ProfileItem()
    profile_to_orm(profile, profile_row)
    restored_profile = profile_from_orm(profile_row)
    assert restored_profile.source_l1_ids == [1, 2]
    assert restored_profile.value == "Python"

    fact = Fact(
        tenant_id="t",
        user_id="u",
        agent_id="a",
        scope="p",
        subject="用户",
        predicate="喜欢",
        object="Rust",
        source_l1_ids=[3],
    )
    fact_row = orm.MemsL2Fact()
    fact_to_orm(fact, fact_row)
    restored_fact = fact_from_orm(fact_row)
    assert restored_fact.source_l1_ids == [3]
    assert restored_fact.object == "Rust"
