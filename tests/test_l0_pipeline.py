from __future__ import annotations

import asyncio
import fnmatch
import json
import signal
import threading
import time
from datetime import datetime, timezone

import httpx
import pytest
from conftest import FakeWorkingMemoryStore
from fastapi.testclient import TestClient
from pydantic import SecretStr

import mems.main as main_module
import mems.worker as worker_module
from mems.config import settings
from mems.domain.identity import MemoryNamespace
from mems.infrastructure.external import embedding as embedding_module
from mems.infrastructure.external import llm_client as llm_module
from mems.infrastructure.external import vector_service as vector_module
from mems.infrastructure.external.embedding import OpenAIEmbeddingProvider
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
        self._indexes: dict[str, dict[str, float]] = {}

    async def eval(self, script, numkeys, *args):  # noqa: ANN001
        assert numkeys == 2
        key, index_key, raw, _ttl, score = args
        self.set_calls += 1
        self._store[key] = raw
        self._indexes.setdefault(index_key, {})[key] = float(score)
        return 1

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


class _AtomicRedisClient:
    """In-memory stand-in executing the append script's merge atomically."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}
        self.eval_calls = 0
        self.get_calls = 0
        self.pipeline_calls = 0
        self.pipeline_get_calls = 0
        self.scan_counts: list[int] = []
        self.zrange_calls = 0
        self.close_calls = 0
        self._indexes: dict[str, dict[str, float]] = {}

    def _live(self, key: str) -> tuple[str, float] | None:
        entry = self._store.get(key)
        if entry is not None and entry[1] <= time.monotonic():
            self._store.pop(key, None)
            return None
        return entry

    async def set(self, key, value, ex=None):  # noqa: ANN001
        expires = time.monotonic() + ex if ex is not None else float("inf")
        self._store[key] = (value, expires)
        return True

    async def get(self, key):  # noqa: ANN001
        self.get_calls += 1
        return self._value(key)

    def _value(self, key):  # noqa: ANN001
        entry = self._live(key)
        return entry[0] if entry else None

    async def eval(self, script, numkeys, *args):  # noqa: ANN001
        assert numkeys == 2
        self.eval_calls += 1
        key, index_key, *argv = args
        index = self._indexes.setdefault(index_key, {})
        if "local current" not in script:
            if "redis.call('DEL'" in script:
                removed = int(self._live(key) is not None)
                self._store.pop(key, None)
                index.pop(key, None)
                return removed
            ttl = int(argv[1])
            self._store[key] = (argv[0], time.monotonic() + ttl / 1000)
            index[key] = float(argv[2])
            return 1

        entry = self._live(key)
        if entry is None:
            ttl = int(argv[6])
            self._store[key] = (argv[7], time.monotonic() + ttl / 1000)
            index[key] = float(argv[8])
            return [argv[7], str(ttl), "0"]

        ttl = max(1, int((entry[1] - time.monotonic()) * 1000))
        snapshot = json.loads(entry[0])
        merged = snapshot.get("short_term_buffer", []) + json.loads(argv[0])
        max_buffer = int(argv[1])
        if max_buffer > 0:
            merged = merged[-max_buffer:]
        snapshot["short_term_buffer"] = merged
        if str(argv[2]) == "1":
            snapshot["content"] = json.loads(argv[3])
        if str(argv[4]) == "1":
            snapshot["namespace"] = json.loads(argv[5])
        encoded = json.dumps(snapshot, ensure_ascii=False)
        self._store[key] = (encoded, time.monotonic() + ttl / 1000)
        index[key] = time.time() + ttl / 1000
        return [encoded, str(ttl), "1"]

    async def zremrangebyscore(self, name, min_score, max_score):  # noqa: ANN001
        index = self._indexes.setdefault(name, {})
        for key, score in list(index.items()):
            if float(min_score) <= score <= float(max_score):
                index.pop(key, None)
        return 1

    async def zrevrangebyscore(
        self, name, max_score, min_score, start=None, num=None
    ):  # noqa: ANN001
        self.zrange_calls += 1
        index = self._indexes.setdefault(name, {})
        keys = [
            key
            for key, score in sorted(index.items(), key=lambda item: item[1], reverse=True)
            if float(min_score) <= score <= float(max_score)
        ]
        offset = start or 0
        return keys[offset : offset + num] if num is not None else keys[offset:]

    async def zrem(self, name, key):  # noqa: ANN001
        index = self._indexes.setdefault(name, {})
        return int(index.pop(key, None) is not None)

    async def scan_iter(self, match=None, count=100):  # noqa: ANN001
        self.scan_counts.append(count)
        for key in list(self._store):
            if self._live(key) and fnmatch.fnmatchcase(key, match):
                yield key

    def pipeline(self, transaction=False):  # noqa: ANN001
        self.pipeline_calls += 1
        return _AtomicPipeline(self)

    async def close(self) -> None:
        self.close_calls += 1

    async def aclose(self) -> None:
        await self.close()


class _AtomicPipeline:
    def __init__(self, client: _AtomicRedisClient) -> None:
        self.client = client
        self.keys: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False

    def get(self, key):  # noqa: ANN001
        self.keys.append(key)

    async def execute(self):  # noqa: ANN001
        self.client.pipeline_get_calls += len(self.keys)
        return [self.client._value(key) for key in self.keys]


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


def test_redis_append_is_atomic_and_preserves_ttl():
    client = _AtomicRedisClient()
    service = RedisService()
    service._client = client

    async def run():
        first = await service.write(
            tenant_id=None,
            user_id=None,
            agent_id="agent_atomic",
            session_id="session_atomic",
            messages=[{"role": "user", "content": "initial"}],
            content="initial",
            ttl_seconds=100,
        )
        await asyncio.gather(
            *(
                service.append_messages(
                    tenant_id=None,
                    user_id=None,
                    agent_id="agent_atomic",
                    session_id="session_atomic",
                    messages=[{"role": "user", "content": str(index)}],
                    content=str(index),
                    ttl_seconds=999,
                    max_buffer_size=10,
                )
                for index in range(30)
            )
        )
        merged = await service.read("agent_atomic", "session_atomic")
        assert merged is not None
        assert merged.expires_at == first.expires_at
        assert merged.content == "29"
        assert [item["content"] for item in merged.short_term_buffer] == [
            str(index) for index in range(20, 30)
        ]
        assert client.eval_calls == 31
        assert client.get_calls == 1  # the explicit read above
        active = await service.list_active("agent_atomic")
        assert len(active) == 1
        assert client.get_calls == 1
        assert client.pipeline_calls == 1
        assert client.pipeline_get_calls == 1
        assert client.scan_counts == []
        assert client.zrange_calls == 1
        await service.close()
        await service.close()
        await service.aclose()
        assert client.close_calls == 1

    asyncio.run(run())


def test_list_active_uses_bounded_sorted_index_reads_and_delete_cleans_index():
    client = _AtomicRedisClient()
    service = RedisService()
    service._client = client

    async def run():
        for index in range(20):
            await service.write(
                None,
                None,
                "agent_indexed",
                f"session_{index}",
                [],
                content=f"memory {index}",
                ttl_seconds=1000 + index,
            )
        active = await service.list_active("agent_indexed", limit=3)
        assert len(active) == 3
        assert active[0].session_id == "session_19"
        assert client.pipeline_get_calls == 3
        assert client.scan_counts == []
        assert await service.delete("agent_indexed", "session_19") is True
        remaining = await service.list_active("agent_indexed", limit=3)
        assert "session_19" not in {item.session_id for item in remaining}

    asyncio.run(run())


def test_fake_store_expires_and_preserves_append_ttl():
    store = FakeWorkingMemoryStore()

    async def run():
        default = await store.write(
            None,
            None,
            "agent_fake",
            "session_fake",
            [{"role": "user", "content": "default"}],
            ttl_seconds=100,
            namespace=None,
        )
        namespaced = await store.write(
            None,
            None,
            "agent_fake",
            "session_fake",
            [{"role": "user", "content": "namespaced"}],
            ttl_seconds=100,
            namespace="namespace-fake",
        )
        merged = await store.append_messages(
            None,
            None,
            "agent_fake",
            "session_fake",
            [{"role": "user", "content": "next"}],
            ttl_seconds=999,
            namespace=None,
        )
        assert merged.expires_at == default.expires_at
        assert await store.read("agent_fake", "session_fake", namespace=None) is merged
        assert (
            await store.read("agent_fake", "session_fake", namespace="namespace-fake")
            is namespaced
        )
        default_only = await store.list_active("agent_fake", namespace=None)
        assert [item.namespace for item in default_only] == [None]

        await store.write(
            None,
            None,
            "agent_fake",
            "expired",
            [],
            ttl_seconds=-1,
            namespace=None,
        )
        assert await store.read("agent_fake", "expired", namespace=None) is None
        assert all(
            item.session_id != "expired"
            for item in await store.list_active("agent_fake", namespace=None)
        )

    asyncio.run(run())


@pytest.mark.asyncio
async def test_lifespan_cleans_up_after_startup_failure(monkeypatch, tmp_path):
    cleanup: list[str] = []

    async def record(name):
        cleanup.append(name)

    monkeypatch.setattr(settings, "OUTBOX_WORKER_ENABLED", False)
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    monkeypatch.setattr(settings, "ARCHIVE_STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    monkeypatch.setattr(main_module.scheduler_service, "add_archive_job", lambda *a, **k: None)
    monkeypatch.setattr(main_module.scheduler_service, "shutdown", lambda: record("scheduler"))

    def fail_start():
        raise RuntimeError("scheduler start failed")

    monkeypatch.setattr(main_module.scheduler_service, "start", fail_start)
    for name in ("redis", "vector", "embedding", "llm"):
        monkeypatch.setattr(
            main_module,
            f"close_{name}_service" if name != "llm" else "close_llm_client",
            lambda name=name: record(name),
        )
    monkeypatch.setattr(main_module.engine, "dispose", lambda: record("engine"))

    with pytest.raises(RuntimeError, match="scheduler start failed"):
        async with main_module.lifespan(main_module.app):
            pass
    assert cleanup == ["scheduler", "redis", "vector", "embedding", "llm", "engine"]


@pytest.mark.asyncio
async def test_worker_sigterm_stops_and_closes_resources(monkeypatch):
    loop = asyncio.get_running_loop()
    callbacks: dict[signal.Signals, object] = {}
    removed: list[signal.Signals] = []
    cleanup: list[str] = []
    started = asyncio.Event()
    stopped = asyncio.Event()

    def add_signal_handler(signum, callback):
        callbacks[signum] = callback

    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    monkeypatch.setattr(
        loop, "remove_signal_handler", lambda signum: removed.append(signum)
    )
    monkeypatch.setattr(worker_module, "init_db", lambda: None)

    class FakeWorker:
        async def run_forever(self, poll_interval):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    monkeypatch.setattr(worker_module, "build_outbox_worker", lambda: FakeWorker())
    for name in ("redis", "vector", "embedding", "llm"):
        monkeypatch.setattr(
            worker_module,
            f"close_{name}_service" if name != "llm" else "close_llm_client",
            lambda name=name: record(name),
        )
    monkeypatch.setattr(worker_module.engine, "dispose", lambda: record("engine"))

    def record(name):
        cleanup.append(name)

    task = asyncio.create_task(worker_module._run())
    await started.wait()
    callbacks[signal.SIGTERM]()
    await asyncio.wait_for(task, timeout=1)
    assert stopped.is_set()
    assert removed == [signal.SIGTERM, signal.SIGINT]
    assert cleanup == ["redis", "vector", "embedding", "llm", "engine"]


@pytest.mark.asyncio
async def test_openai_embedding_reuses_client_and_retries_only_transient_status():
    class StubClient:
        def __init__(self):
            self.calls = 0
            self.close_calls = 0

        async def post(self, url, json):  # noqa: ANN001
            self.calls += 1
            request = httpx.Request("POST", url)
            if self.calls == 1:
                return httpx.Response(429, request=request)
            return httpx.Response(
                200,
                request=request,
                json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
            )

        async def aclose(self):
            self.close_calls += 1

    stub = StubClient()
    provider = OpenAIEmbeddingProvider("secret", base_url="https://embedding.example/v1")
    provider._client = stub
    assert await provider.embed(["hello"]) == [[0.1, 0.2]]
    assert stub.calls == 2
    await provider.close()
    await provider.aclose()
    assert stub.close_calls == 1


@pytest.mark.asyncio
async def test_sentence_transformers_load_is_locked_and_encoding_is_offloaded(monkeypatch):
    main_thread = threading.get_ident()
    encoding_threads: list[int] = []
    loads = 0

    class Vector:
        def tolist(self):
            return [1.0, 2.0]

    class Model:
        def encode(self, texts, convert_to_numpy):  # noqa: ANN001
            encoding_threads.append(threading.get_ident())
            assert convert_to_numpy is True
            return [Vector() for _ in texts]

    provider = embedding_module.SentenceTransformersProvider("local-model")

    def load():
        nonlocal loads
        loads += 1
        provider._model = Model()

    monkeypatch.setattr(provider, "_load_model_sync", load)
    await asyncio.gather(provider._load_model(), provider._load_model())
    assert loads == 1
    assert await provider.embed(["hello"]) == [[1.0, 2.0]]
    assert encoding_threads and encoding_threads[0] != main_thread


@pytest.mark.asyncio
async def test_vector_and_llm_clients_close_idempotently(monkeypatch):
    class StubClient:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    vector_stub = StubClient()
    vector = vector_module.VectorService()
    vector._client = vector_stub
    await vector.close()
    await vector.aclose()
    assert vector_stub.close_calls == 1

    llm_stub = StubClient()
    monkeypatch.setattr(llm_module, "_client", llm_stub)
    await llm_module.close_llm_client()
    await llm_module.close_llm_client()
    assert llm_stub.close_calls == 1


def test_openai_embedding_uses_common_credentials_and_base_url(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(settings, "OPENAI_EMBEDDING_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("shared-key"))
    monkeypatch.setattr(settings, "OPENAI_EMBEDDING_BASE_URL", "")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://common.example/v1")
    provider = embedding_module.get_embedding_provider()
    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.api_key == "shared-key"
    assert provider.base_url == "https://common.example/v1"


@pytest.mark.parametrize("operation", ["write", "append_messages"])
async def test_redis_snapshot_ttl_uses_seconds(operation):
    client = _AtomicRedisClient()
    service = RedisService()
    service._client = client
    snapshot = await getattr(service, operation)(
        tenant_id=None, user_id=None, agent_id="ttl", session_id="session",
        messages=[], content="persistent hot memory", ttl_seconds=1800,
    )
    key = next(iter(client._store))
    remaining = client._store[key][1] - time.monotonic()
    assert 1799 < remaining <= 1800
    assert abs((snapshot.expires_at - datetime.now(timezone.utc)).total_seconds() - remaining) < 1
    await service.append_messages(
        tenant_id=None, user_id=None, agent_id="ttl", session_id="session",
        messages=[], content="updated", ttl_seconds=9999,
    )
    assert 1799 < client._store[key][1] - time.monotonic() <= remaining
