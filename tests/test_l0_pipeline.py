from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from mems.config import settings
from mems.domain.identity import MemoryNamespace
from mems.infrastructure.external.redis_service import RedisService


def test_pipeline_disabled_by_default(test_app):
    """The L0 output pipeline publishes nothing when disabled.

    L0 输出管道默认关闭时不应发布任何事件。
    """
    app, _, redis_service, _ = test_app

    with TestClient(app) as client:
        response = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "agent_no_pipe",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert response.status_code == 200

    assert redis_service.pipeline_events == []


def test_pipeline_publishes_write_and_append_events(test_app, monkeypatch):
    """Write/append publish typed events with identity and snapshot data.

    写入/追加会发布带类型、身份与快照数据的事件。
    """
    monkeypatch.setattr(settings, "L0_PIPELINE_ENABLED", True)
    app, _, redis_service, _ = test_app

    with TestClient(app) as client:
        first = client.post(
            "/v1/mems/write",
            json={
                "tenant_id": "tenant_demo",
                "user_id": "user_alice",
                "agent_id": "agent_pipe",
                "session_id": "session_pipe",
                "namespace": ["projects", "prj_pipe"],
                "content": "User prefers Rust.",
                "messages": [{"role": "user", "content": "I prefer Rust."}],
            },
        )
        second = client.post(
            "/v1/mems/write",
            json={
                "tenant_id": "tenant_demo",
                "user_id": "user_alice",
                "agent_id": "agent_pipe",
                "session_id": "session_pipe",
                "namespace": ["projects", "prj_pipe"],
                "messages": [{"role": "user", "content": "Also like Chinese."}],
            },
        )
        assert first.status_code == 200
        assert second.status_code == 200

        status = client.get("/v1/mems/status").json()
        assert status["pipeline"]["l0_pipeline_enabled"] is True

    assert len(redis_service.pipeline_events) == 2
    write_event, append_event = redis_service.pipeline_events

    assert write_event["event_type"] == "write"
    assert write_event["event_id"]
    assert write_event["tenant_id"] == "tenant_demo"
    assert write_event["user_id"] == "user_alice"
    assert write_event["agent_id"] == "agent_pipe"
    assert write_event["session_id"] == "session_pipe"
    assert write_event["namespace"] == MemoryNamespace.build(
        tenant_id="tenant_demo",
        user_id="user_alice",
        agent_id="agent_pipe",
        path=("projects", "prj_pipe"),
    ).key
    assert write_event["content"] == "User prefers Rust."
    assert json.loads(write_event["messages"]) == [
        {"role": "user", "content": "I prefer Rust."}
    ]
    assert write_event["expires_at"]

    assert append_event["event_type"] == "append"
    # The L0 snapshot content reflects the latest write; the buffer appends.
    assert append_event["content"] == "user: Also like Chinese."
    assert json.loads(append_event["messages"]) == [
        {"role": "user", "content": "I prefer Rust."},
        {"role": "user", "content": "Also like Chinese."},
    ]


class _RecordingClient:
    """Stub Redis client that records stream publications.

    用于记录流发布的 Redis 客户端替身。
    """

    def __init__(self, fail_on_xadd: bool = False) -> None:
        self.xadd_calls: list[tuple] = []
        self.set_calls = 0
        self.fail_on_xadd = fail_on_xadd
        self._store: dict = {}

    async def set(self, key, value, ex=None):  # noqa: ANN001
        self.set_calls += 1
        self._store[key] = value
        return True

    async def get(self, key):  # noqa: ANN001
        return self._store.get(key)

    async def ttl(self, key):  # noqa: ANN001
        return 100

    async def xadd(self, name, fields, **kwargs):  # noqa: ANN001
        self.xadd_calls.append((name, fields, kwargs))
        if self.fail_on_xadd:
            raise RuntimeError("stream unavailable")
        return "1-0"


def test_real_service_publishes_only_when_enabled(monkeypatch):
    """The real RedisService writes to the configured stream when enabled.

    真实 RedisService 在启用时写入配置的流，关闭时不做任何发布。
    """
    client = _RecordingClient()
    service = RedisService()

    async def fake_get_client():
        return client

    monkeypatch.setattr(service, "get_client", fake_get_client)

    async def run(enabled: bool):
        monkeypatch.setattr(settings, "L0_PIPELINE_ENABLED", enabled)
        return await service.write(
            tenant_id=None,
            user_id=None,
            agent_id="agent_unit",
            session_id=None,
            messages=[{"role": "user", "content": "Unit test memory"}],
            content="Unit test memory",
            namespace=None,
        )

    asyncio.run(run(enabled=False))
    assert client.xadd_calls == []

    asyncio.run(run(enabled=True))
    assert len(client.xadd_calls) == 1
    stream, fields, kwargs = client.xadd_calls[0]
    assert stream == "mems:l0:events"
    assert kwargs["maxlen"] == settings.L0_PIPELINE_MAXLEN
    assert kwargs["approximate"] is True
    assert fields["event_type"] == "write"
    assert fields["agent_id"] == "agent_unit"
    assert fields["content"] == "Unit test memory"
    assert json.loads(fields["messages"]) == [
        {"role": "user", "content": "Unit test memory"}
    ]


def test_publish_failure_does_not_break_write(monkeypatch):
    """A broken output stream must not fail the memory write itself.

    输出流不可用时，记忆写入本身不应失败。
    """
    client = _RecordingClient(fail_on_xadd=True)
    service = RedisService()

    async def fake_get_client():
        return client

    monkeypatch.setattr(service, "get_client", fake_get_client)
    monkeypatch.setattr(settings, "L0_PIPELINE_ENABLED", True)

    l0 = asyncio.run(
        service.write(
            tenant_id=None,
            user_id=None,
            agent_id="agent_unit",
            session_id=None,
            messages=[{"role": "user", "content": "Still stored"}],
            content="Still stored",
            namespace=None,
        )
    )
    assert l0.agent_id == "agent_unit"
    assert l0.short_term_buffer == [{"role": "user", "content": "Still stored"}]
    assert client.set_calls == 1


def test_empty_stream_name_disables_publish(monkeypatch):
    """An empty stream key must disable publishing even when enabled.

    即使管道开关打开，空的流名也应禁用发布。
    """
    client = _RecordingClient()
    service = RedisService()

    async def fake_get_client():
        return client

    monkeypatch.setattr(service, "get_client", fake_get_client)
    monkeypatch.setattr(settings, "L0_PIPELINE_ENABLED", True)
    monkeypatch.setattr(settings, "L0_PIPELINE_STREAM", "")

    import asyncio

    l0 = asyncio.run(
        service.write(
            tenant_id=None,
            user_id=None,
            agent_id="agent_unit",
            session_id=None,
            messages=[{"role": "user", "content": "Guard test"}],
            content="Guard test",
            namespace=None,
        )
    )
    assert l0.agent_id == "agent_unit"
    assert client.xadd_calls == []


def test_status_reports_pipeline_enabled(test_app, monkeypatch):
    """The status endpoint reflects the pipeline config flag.

    状态端点反映输出管道的配置开关。
    """
    app, _, _, _ = test_app

    with TestClient(app) as client:
        assert client.get("/v1/mems/status").json()["pipeline"][
            "l0_pipeline_enabled"
        ] is False

    monkeypatch.setattr(settings, "L0_PIPELINE_ENABLED", True)

    with TestClient(app) as client:
        assert client.get("/v1/mems/status").json()["pipeline"][
            "l0_pipeline_enabled"
        ] is True
