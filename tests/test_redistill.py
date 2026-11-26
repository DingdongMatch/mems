from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mems.application.commands import RedistillResponse
from mems.config import settings
from mems.domain.identity import MemoryIdentity
from mems.infrastructure.external.jsonl import JsonlReader
from mems.infrastructure.persistence.models import (
    MemsL1Episodic,
    MemsL2ConflictLog,
    MemsL2Fact,
    MemsL2ProfileItem,
    MemsL2Summary,
    MemsL3Archive,
    MemsRedistillProgress,
)
from mems.infrastructure.persistence.repositories import build_repositories
from mems.interfaces import bootstrap
from mems.interfaces.api import dependencies


TENANT_ID = "tenant_redistill"
USER_ID = "user_redistill"


@pytest.fixture(autouse=True)
def use_fake_redistill_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        dependencies,
        "get_llm_port",
        lambda: bootstrap.get_llm_port(),
    )
    monkeypatch.setattr(settings, "ARCHIVE_STORAGE_PATH", str(tmp_path / "l3"))


def _identity(
    agent_id: str,
    *,
    tenant_id: str = TENANT_ID,
    user_id: str = USER_ID,
    namespace: tuple[str, ...] = (),
) -> MemoryIdentity:
    return MemoryIdentity(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        namespace_segments=namespace,
    )


def _redistill_payload(identity: MemoryIdentity, **overrides) -> dict:
    payload = {
        "tenant_id": identity.tenant_id,
        "user_id": identity.user_id,
        "agent_id": identity.agent_id,
        "namespace": list(identity.namespace_segments),
    }
    payload.update(overrides)
    return payload


def _redistill(client: TestClient, identity: MemoryIdentity, **overrides):
    return client.post(
        "/v1/mems/redistill",
        json=_redistill_payload(identity, **overrides),
    )


def _write_and_archive(
    client: TestClient,
    agent_id: str,
    engine,
    content: str = "I like Python and long term memory systems.",
    importance_score: float = 0.5,
    days_old: int = 40,
    namespace: tuple[str, ...] = (),
) -> int:
    """Write one memory, backdate it, and archive its exact identity into L3."""
    identity = _identity(agent_id, namespace=namespace)
    response = client.post(
        "/v1/mems/write",
        json={
            "tenant_id": identity.tenant_id,
            "user_id": identity.user_id,
            "agent_id": identity.agent_id,
            "namespace": list(identity.namespace_segments),
            "importance_score": importance_score,
            "messages": [{"role": "user", "content": content}],
        },
    )
    assert response.status_code == 200

    with Session(engine) as session:
        record = session.exec(
            select(MemsL1Episodic)
            .where(
                MemsL1Episodic.tenant_id == identity.tenant_id,
                MemsL1Episodic.user_id == identity.user_id,
                MemsL1Episodic.agent_id == identity.agent_id,
                MemsL1Episodic.namespace == identity.namespace_key,
            )
            .order_by(MemsL1Episodic.id.desc())
        ).first()
        assert record is not None
        l1_id = record.id
        record.created_at = datetime.now(timezone.utc) - timedelta(days=days_old)
        session.add(record)
        session.commit()

    with Session(engine) as session:
        result = asyncio.run(
            bootstrap.make_archive_service(session).archive(
                agent_id=identity.agent_id,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                namespace=identity.namespace_key,
                days=30,
            )
        )
        assert result.archived_count == 1
    return l1_id


def _archive_record(engine) -> tuple[MemsL3Archive, dict]:
    with Session(engine) as session:
        archive = session.exec(select(MemsL3Archive)).one()
    path = Path(archive.file_path)
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    return archive, record


def test_redistill_closed_loop(test_app, monkeypatch):
    """write -> archive -> redistill creates L2 and records progress."""
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app
    identity = _identity("agent_loop")

    with TestClient(app) as client:
        _write_and_archive(client, identity.agent_id, engine)
        response = _redistill(client, identity)
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["processed"] == 1
        assert payload["created"] >= 1

    with Session(engine) as session:
        profiles = session.exec(
            select(MemsL2ProfileItem).where(MemsL2ProfileItem.agent_id == identity.agent_id)
        ).all()
        facts = session.exec(
            select(MemsL2Fact).where(MemsL2Fact.agent_id == identity.agent_id)
        ).all()
        assert len(profiles) >= 1
        assert len(facts) >= 1

        progress = session.exec(select(MemsRedistillProgress)).all()
        assert len(progress) == 1
        assert progress[0].l3_path
        assert progress[0].l3_line == 1


def test_redistill_dedup_and_model_upgrade(test_app, monkeypatch):
    """Same content+model is skipped; a new model re-processes it."""
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app
    identity = _identity("agent_dedup")

    with TestClient(app) as client:
        _write_and_archive(client, identity.agent_id, engine)
        original_model = settings.OPENAI_MODEL

        first = _redistill(client, identity)
        assert first.status_code == 200
        assert first.json()["processed"] == 1

        second = _redistill(client, identity)
        assert second.json()["processed"] == 0
        assert second.json()["skipped"] >= 1

        with Session(engine) as session:
            for row in session.exec(select(MemsL1Episodic)).all():
                session.delete(row)
            session.commit()

        monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-5-redistill")
        third = _redistill(client, identity)
        assert third.json()["processed"] == 1

    with Session(engine) as session:
        progress = session.exec(select(MemsRedistillProgress)).all()
        assert {item.model for item in progress} == {
            original_model,
            "gpt-5-redistill",
        }
        assert all(item.l3_path for item in progress)


def test_redistill_supersedes_old_values(test_app, monkeypatch):
    """A newer model producing a different value supersedes the old L2 item."""
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app
    identity = _identity("agent_version")

    with TestClient(app) as client:
        _write_and_archive(client, identity.agent_id, engine)
        first = _redistill(client, identity)
        assert first.json()["processed"] == 1

        class UpgradedLLM:
            async def chat(self, messages, model=None, temperature=0.7, json_mode=False):
                _ = (messages, model, temperature, json_mode)
                return json.dumps(
                    {
                        "discarded": [],
                        "profile_updates": [
                            {
                                "category": "like",
                                "key": "technology",
                                "value": "Rust",
                                "confidence": 0.95,
                                "evidence": "upgraded model",
                            }
                        ],
                        "facts": [],
                        "events": [],
                        "conflict_candidates": [],
                        "long_term_summary": "Upgraded summary.",
                    },
                    ensure_ascii=False,
                )

        monkeypatch.setattr(
            "mems.interfaces.bootstrap.get_llm_port",
            lambda: UpgradedLLM(),
        )
        monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-5-version")
        second = _redistill(client, identity)
        assert second.json()["processed"] == 1

    with Session(engine) as session:
        old = session.exec(
            select(MemsL2ProfileItem).where(
                MemsL2ProfileItem.agent_id == identity.agent_id,
                MemsL2ProfileItem.value == "Python",
            )
        ).first()
        new = session.exec(
            select(MemsL2ProfileItem).where(
                MemsL2ProfileItem.agent_id == identity.agent_id,
                MemsL2ProfileItem.value == "Rust",
                MemsL2ProfileItem.status == "active",
            )
        ).first()
        assert old is not None and old.status == "superseded"
        assert new is not None and new.version == old.version + 1
        conflicts = session.exec(
            select(MemsL2ConflictLog).where(MemsL2ConflictLog.agent_id == identity.agent_id)
        ).all()
        assert len(conflicts) >= 1


def test_redistill_filters(test_app, monkeypatch):
    """Agent and min-importance filters restrict what gets re-distilled."""
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app
    identity_a = _identity("agent_filter_a")
    identity_b = _identity("agent_filter_b")

    with TestClient(app) as client:
        _write_and_archive(client, identity_a.agent_id, engine, importance_score=0.9)
        _write_and_archive(client, identity_b.agent_id, engine, importance_score=0.3)

        only_a = _redistill(client, identity_a)
        assert only_a.json()["processed"] == 1

        important = _redistill(client, identity_b, min_importance=0.8)
        assert important.json()["processed"] == 0

        all_remaining = _redistill(client, identity_b)
        assert all_remaining.json()["processed"] == 1

    with Session(engine) as session:
        progress = session.exec(select(MemsRedistillProgress)).all()
        assert len(progress) == 2


def test_redistill_llm_failure_does_not_mark_progress(test_app, monkeypatch):
    """A failing LLM run leaves no progress rows behind."""
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app
    identity = _identity("agent_fail")

    with TestClient(app) as client:
        _write_and_archive(client, identity.agent_id, engine)

        class FailingLLM:
            async def chat(self, messages, model=None, temperature=0.7, json_mode=False):
                _ = (messages, model, temperature, json_mode)
                raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(
            "mems.interfaces.bootstrap.get_llm_port",
            lambda: FailingLLM(),
        )
        response = _redistill(client, identity)
        assert response.status_code == 200
        assert response.json()["success"] is False
        assert response.json()["processed"] == 0
        assert response.json()["failed"] == 1
        assert response.json()["attempted"] == 1
        assert "LLM unavailable" not in response.text

    with Session(engine) as session:
        assert len(session.exec(select(MemsRedistillProgress)).all()) == 0
        assert len(session.exec(select(MemsL2Summary)).all()) == 0


def test_redistill_empty_archive_is_noop(test_app):
    """No archive batches means a no-op run."""
    app, _, _, _ = test_app
    with TestClient(app) as client:
        response = _redistill(client, _identity("agent_empty"))
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["processed"] == 0
    assert response.json()["message"] == "No archive batches found"


def test_redistill_requires_complete_nonblank_identity(test_app):
    app, _, _, _ = test_app
    valid = _redistill_payload(_identity("agent_validation"))
    invalid_payloads = [
        {},
        {**valid, "tenant_id": "   "},
        {**valid, "user_id": ""},
        {**valid, "agent_id": "\t"},
        {**valid, "agent_id": "a" * 129},
        {**valid, "namespace": ["   "]},
        {**valid, "unexpected": True},
    ]

    with TestClient(app) as client:
        for payload in invalid_payloads:
            response = client.post("/v1/mems/redistill", json=payload)
            assert response.status_code == 422, payload


def test_write_and_query_validate_identity_and_blank_content(test_app):
    app, engine, _, _ = test_app
    with TestClient(app) as client:
        for field in ("tenant_id", "user_id", "agent_id", "session_id"):
            response = client.post(
                "/v1/mems/write",
                json={"agent_id": "agent", field: "   ", "content": "hello"},
            )
            assert response.status_code == 422, field

        response = client.post(
            "/v1/mems/write",
            json={"agent_id": "agent", "content": "   "},
        )
        assert response.status_code == 422

        response = client.post(
            "/v1/mems/query",
            json={"agent_id": "a" * 129, "query": "hello"},
        )
        assert response.status_code == 422

        response = client.post(
            "/v1/mems/write",
            json={"agent_id": "agent_strip", "content": "  hello  "},
        )
        assert response.status_code == 200
        assert response.json()["content"] == "hello"

    with Session(engine) as session:
        record = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == "agent_strip")
        ).one()
        assert record.content == "hello"


def test_redistill_dependency_override_receives_exact_identity(test_app):
    app, _, _, _ = test_app
    captured: dict = {}

    class FakeRedistillService:
        async def run(self, identity, **kwargs):
            captured["identity"] = identity
            captured["kwargs"] = kwargs
            return RedistillResponse(
                success=True,
                processed=0,
                created=0,
                updated=0,
                skipped=0,
                message="ok",
            )

    app.dependency_overrides[dependencies.get_redistill_service] = lambda: FakeRedistillService()
    identity = _identity("agent_override", namespace=("projects", "prj_1"))

    with TestClient(app) as client:
        response = _redistill(client, identity, batch_size=7)

    assert response.status_code == 200
    assert captured["identity"].namespace_key == identity.namespace_key
    assert captured["kwargs"]["batch_size"] == 7


def test_redistill_request_cannot_exceed_configured_batch_limit(test_app, monkeypatch):
    app, engine, _, _ = test_app
    monkeypatch.setattr(settings, "REDISTILL_BATCH_SIZE", 1)
    identity = _identity("agent_batch_cap")
    with TestClient(app) as client:
        _write_and_archive(client, identity.agent_id, engine, content="The user likes Python.")
        _write_and_archive(client, identity.agent_id, engine, content="The user likes Rust.")
        response = _redistill(client, identity, batch_size=100)
    assert response.status_code == 200
    assert response.json()["processed"] == 1


def test_redistill_does_not_cross_tenant_user_or_namespace(test_app, monkeypatch):
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app
    original = _identity("agent_boundary", namespace=("projects", "prj_1"))
    other_identities = [
        _identity(
            original.agent_id,
            tenant_id="tenant_other",
            namespace=original.namespace_segments,
        ),
        _identity(
            original.agent_id,
            user_id="user_other",
            namespace=original.namespace_segments,
        ),
        _identity(original.agent_id, namespace=("projects", "prj_2")),
        _identity(original.agent_id),
        _identity("agent_other", namespace=original.namespace_segments),
    ]

    with TestClient(app) as client:
        _write_and_archive(
            client,
            original.agent_id,
            engine,
            namespace=original.namespace_segments,
        )
        for identity in other_identities:
            response = _redistill(client, identity)
            assert response.status_code == 200
            assert response.json()["processed"] == 0

    with Session(engine) as session:
        assert len(session.exec(select(MemsRedistillProgress)).all()) == 0
        assert len(session.exec(select(MemsL2ProfileItem)).all()) == 0


def test_redistill_checks_jsonl_identity_again(test_app, monkeypatch):
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app
    identity = _identity("agent_line_boundary")

    with TestClient(app) as client:
        _write_and_archive(client, identity.agent_id, engine)
        archive, record = _archive_record(engine)
        record["tenant_id"] = "tenant_other"
        Path(archive.file_path).write_text(
            json.dumps(record, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        response = _redistill(client, identity)
        assert response.json()["processed"] == 0

    with Session(engine) as session:
        assert len(session.exec(select(MemsRedistillProgress)).all()) == 0


def test_jsonl_reader_streams_physical_lines_and_redacts_warnings(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "archive.jsonl"
    secret = "private malformed payload"
    path.write_text(
        json.dumps({"id": 1})
        + "\n\n"
        + "{bad json "
        + secret
        + "\n"
        + json.dumps([secret])
        + "\n"
        + json.dumps({"id": 5})
        + "\n",
        encoding="utf-8",
    )
    reader = JsonlReader(tmp_path)

    with caplog.at_level(logging.WARNING, logger="mems.infrastructure.external.jsonl"):
        records = list(reader.iter_records(path))

    assert records == [(1, {"id": 1}), (5, {"id": 5})]
    assert len(caplog.records) == 2
    assert secret not in caplog.text
    caplog.clear()
    assert reader.read(path) == [{"id": 1}, {"id": 5}]


def test_redistill_skips_bad_fields_and_preserves_evidence_line(
    test_app, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app
    identity = _identity("agent_bad_fields")

    with TestClient(app) as client:
        _write_and_archive(client, identity.agent_id, engine)
        archive, valid = _archive_record(engine)
        bad_time = {**valid, "content": "bad time", "created_at": "not-a-date"}
        missing_agent = {**valid, "content": "missing agent"}
        missing_agent.pop("agent_id")
        bad_importance = {
            **valid,
            "content": "bad importance",
            "importance_score": "not-a-number",
        }
        out_of_range = {
            **valid,
            "content": "out of range",
            "importance_score": 2,
        }
        lines = [
            "{malformed secret",
            "",
            json.dumps(valid, ensure_ascii=False),
            json.dumps(bad_time, ensure_ascii=False),
            json.dumps(missing_agent, ensure_ascii=False),
            json.dumps(bad_importance, ensure_ascii=False),
            json.dumps(out_of_range, ensure_ascii=False),
        ]
        Path(archive.file_path).write_text("\n".join(lines) + "\n", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="mems.infrastructure.external.jsonl"):
            response = _redistill(client, identity)

        assert response.json()["processed"] == 1
        assert "malformed secret" not in caplog.text

    with Session(engine) as session:
        progress = session.exec(select(MemsRedistillProgress)).one()
        assert progress.l3_line == 3


def test_redistill_progress_concurrent_insert_is_idempotent(test_app):
    _, engine, _, _ = test_app
    barrier = Barrier(2)

    def insert_progress():
        with Session(engine) as session:
            repo = build_repositories(session).redistill
            barrier.wait(timeout=10)
            inserted = repo.add("same-hash", "same-model", "/tmp/archive.jsonl", 1)
            session.commit()
            return inserted

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(insert_progress) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]

    assert sorted(results) == [False, True]
    with Session(engine) as session:
        assert len(session.exec(select(MemsRedistillProgress)).all()) == 1


def test_redistill_single_source_failure_leaves_no_partial_l2(test_app, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DISTILL_HIGH_IMPORTANCE_THRESHOLD", 1.0)
    app, engine, _, _ = test_app
    identity = _identity("agent_partial")
    fake_llm = bootstrap.get_llm_port()

    class SelectiveFailingLLM:
        async def chat(self, messages, model=None, temperature=0.7, json_mode=False):
            if "Rust" in messages[-1]["content"]:
                raise RuntimeError("Rust source failed")
            return await fake_llm.chat(
                messages,
                model=model,
                temperature=temperature,
                json_mode=json_mode,
            )

    monkeypatch.setattr(
        "mems.interfaces.bootstrap.get_llm_port",
        lambda: SelectiveFailingLLM(),
    )

    with TestClient(app) as client:
        _write_and_archive(client, identity.agent_id, engine, content="I like Python")
        _write_and_archive(client, identity.agent_id, engine, content="I prefer Rust")
        response = _redistill(client, identity)
        assert response.json()["processed"] == 1

    with Session(engine) as session:
        profiles = session.exec(select(MemsL2ProfileItem)).all()
        assert {item.value for item in profiles} == {"Python"}
        assert len(session.exec(select(MemsRedistillProgress)).all()) == 1
        assert len(session.exec(select(MemsL2Summary)).all()) == 1


def test_write_query_redistill_500s_are_generic_and_correlated(test_app):
    app, _, _, _ = test_app
    secret = "do-not-leak-internal-error"

    class FailingWriteService:
        async def remember(self, command):
            raise RuntimeError(secret)

    class FailingQueryService:
        async def recall(self, command):
            raise RuntimeError(secret)

    class FailingRedistillService:
        async def run(self, identity, **kwargs):
            raise RuntimeError(secret)

    app.dependency_overrides[dependencies.get_write_service] = lambda: FailingWriteService()
    app.dependency_overrides[dependencies.get_query_service] = lambda: FailingQueryService()
    app.dependency_overrides[dependencies.get_redistill_service] = lambda: FailingRedistillService()

    with TestClient(app) as client:
        responses = [
            client.post(
                "/v1/mems/write",
                json={"agent_id": "agent", "content": "hello"},
            ),
            client.post(
                "/v1/mems/query",
                json={"agent_id": "agent", "query": "hello"},
            ),
            _redistill(client, _identity("agent")),
        ]

    for response in responses:
        assert response.status_code == 500
        payload = response.json()
        assert payload["detail"] == "Internal server error"
        assert payload["request_id"] == response.headers["x-request-id"]
        assert secret not in response.text


def test_redistill_failures_consume_batch_budget(test_app, monkeypatch):
    app, engine, _, _ = test_app
    monkeypatch.setattr(settings, "REDISTILL_BATCH_SIZE", 1)
    identity = _identity("bounded_failures")

    class FailingLLM:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise RuntimeError("unavailable")

    llm = FailingLLM()
    with TestClient(app) as client:
        for index in range(3):
            _write_and_archive(client, identity.agent_id, engine, content=f"Python memory {index}")
        monkeypatch.setattr(dependencies, "get_llm_port", lambda: llm)
        for _ in range(2):
            response = _redistill(client, identity, batch_size=100)
            assert response.status_code == 200
            body = response.json()
            assert body["success"] is False
            assert body["attempted"] == body["failed"] == 1
            assert body["processed"] == 0
        # Two requests, one source each, JSON mode + fallback per source.
        assert llm.calls == 4
    with Session(engine) as session:
        assert session.exec(select(MemsRedistillProgress)).all() == []


def test_disabled_provider_skips_manual_redistill_even_with_api_key(test_app, monkeypatch):
    app, engine, _, _ = test_app
    identity = _identity("disabled_redistill")
    with TestClient(app) as client:
        _write_and_archive(client, identity.agent_id, engine)
        monkeypatch.setattr(settings, "LLM_PROVIDER", None)
        response = _redistill(client, identity)
        assert response.status_code == 200
        assert response.json()["success"] is False
        assert response.json()["attempted"] == 0
    with Session(engine) as session:
        assert session.exec(select(MemsRedistillProgress)).all() == []


@pytest.mark.parametrize("endpoint", ["write", "query", "redistill"])
@pytest.mark.parametrize("oversize", ["ascii_path", "unicode_identity", "escaped_path"])
def test_oversized_partition_is_rejected_before_sql(test_app, endpoint, oversize):
    app, engine, _, _ = test_app
    payload = {"tenant_id": "tenant", "user_id": "user", "agent_id": "agent"}
    if oversize == "ascii_path":
        payload["namespace"] = [f"{index:02}" + "x" * 126 for index in range(16)]
    elif oversize == "unicode_identity":
        payload.update(tenant_id="租" * 128, user_id="用" * 128, agent_id="助" * 128)
    else:
        payload["namespace"] = ['"' * 128] * 2
    if endpoint == "write":
        payload["content"] = "Memory"
    elif endpoint == "query":
        payload["query"] = "Memory"
    with TestClient(app) as client:
        response = client.post(f"/v1/mems/{endpoint}", json=payload)
    assert response.status_code == 422
    assert "512 UTF-8 bytes" in response.text
    with Session(engine) as session:
        assert session.exec(select(MemsL1Episodic)).all() == []


def test_largest_accepted_partition_fits_postgres_indexes(test_app):
    import random
    import string

    app, _, _, _ = test_app
    rng = random.Random(42)
    identity = {"tenant_id": "tenant", "user_id": "user", "agent_id": "agent"}
    segments = ["".join(rng.choices(string.ascii_letters + string.digits, k=128)) for _ in range(3)]
    overhead = len(MemoryIdentity(**identity, namespace_segments=tuple([*segments, ""])).namespace_key.encode())
    segments.append("".join(rng.choices(string.ascii_letters, k=512 - overhead)))
    assert len(MemoryIdentity(**identity, namespace_segments=tuple(segments)).namespace_key.encode()) == 512
    with TestClient(app) as client:
        response = client.post("/v1/mems/write", json={
            **identity, "namespace": segments, "content": "Python memory",
        })
        assert response.status_code == 200
        response = client.post("/v1/mems/query", json={
            **identity, "namespace": segments, "query": "Python",
        })
        assert response.status_code == 200


def test_redistill_run_error_preserves_committed_counts(test_app, monkeypatch):
    app, engine, _, _ = test_app
    identity = _identity("committed_before_failure")
    original = JsonlReader.iter_records

    def fail_after_record(self, path):
        yield from original(self, path)
        raise RuntimeError("sensitive reader diagnostic")

    with TestClient(app) as client:
        _write_and_archive(client, identity.agent_id, engine)
        monkeypatch.setattr(JsonlReader, "iter_records", fail_after_record)
        response = _redistill(client, identity)
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["processed"] == body["attempted"] == 1
        assert body["created"] > 0
        assert "sensitive reader diagnostic" not in response.text
    with Session(engine) as session:
        assert len(session.exec(select(MemsRedistillProgress)).all()) == 1
