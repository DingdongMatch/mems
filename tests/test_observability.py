from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from urllib.parse import parse_qsl

import pytest

from mems.config import settings
from mems.infrastructure.external import llm_client
from mems.infrastructure.external.observability import (
    JsonLogFormatter,
    RequestContextMiddleware,
    _BodyCapture,
    _render_body,
    bind_log_context,
)


@pytest.fixture
def http_logs() -> list[dict]:
    observed: list[dict] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            observed.append(json.loads(JsonLogFormatter().format(record)))

    logger = logging.getLogger("mems.http")
    handler = CaptureHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield observed
    finally:
        logger.removeHandler(handler)


def _scope(
    *,
    method: str = "POST",
    path: str = "/v1/mems/write",
    query: bytes = b"",
    request_headers: list[tuple[bytes, bytes]] | None = None,
) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query,
        "headers": request_headers or [],
    }


def _receive(*chunks: bytes):
    pending = list(chunks)

    async def receive() -> dict:
        body = pending.pop(0) if pending else b""
        return {
            "type": "http.request",
            "body": body,
            "more_body": bool(pending),
        }

    return receive


def test_json_formatter_includes_correlation_and_safe_fields() -> None:
    record = logging.LogRecord(
        name="mems.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="operation completed",
        args=(),
        exc_info=None,
    )
    record.event = "test.completed"
    record.duration_ms = 12.5
    record.memory_content = "must not be serialized"

    with bind_log_context(request_id="request-123", outbox_event_id=42):
        result = json.loads(JsonLogFormatter().format(record))

    assert result["request_id"] == "request-123"
    assert result["outbox_event_id"] == 42
    assert result["event"] == "test.completed"
    assert result["duration_ms"] == 12.5
    assert "memory_content" not in result


def test_json_formatter_omits_exception_message_but_keeps_stack() -> None:
    secret = "private memory text"
    try:
        raise ValueError(secret)
    except ValueError as exc:
        record = logging.LogRecord(
            name="mems.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=20,
            msg="operation failed",
            args=(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    result = JsonLogFormatter().format(record)
    assert "private memory text" not in result
    parsed = json.loads(result)
    assert parsed["exception"]["type"] == "ValueError"
    assert "test_json_formatter_omits_exception_message" in parsed["exception"]["traceback"]


@pytest.mark.asyncio
async def test_request_middleware_adds_and_logs_request_id() -> None:
    observed: list[dict] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            observed.append(json.loads(JsonLogFormatter().format(record)))

    logger = logging.getLogger("mems.http")
    handler = CaptureHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    response_messages: list[dict] = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        response_messages.append(message)

    try:
        await RequestContextMiddleware(app)(
            {"type": "http", "method": "GET", "path": "/v1/mems/status", "headers": []},
            receive,
            send,
        )
    finally:
        logger.removeHandler(handler)

    request_id = dict(response_messages[0]["headers"])[b"x-request-id"].decode()
    assert request_id
    assert observed[0]["request_id"] == request_id
    assert observed[-1]["request_id"] == request_id
    assert observed[-1]["status_code"] == 204
    assert observed[-1]["event"] == "http.request.completed"


@pytest.mark.asyncio
async def test_openai_adapter_preserves_model_json_mode_and_request_options(
    monkeypatch,
) -> None:
    calls: dict = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.update(kwargs)
            message = SimpleNamespace(content='{"ok": true}')
            choice = SimpleNamespace(message=message)
            return SimpleNamespace(choices=[choice])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    monkeypatch.setattr(llm_client, "get_llm_client", lambda: fake_client)
    messages = [{"role": "user", "content": "memory input"}]
    response = await llm_client.chat(
        messages,
        model="custom-model",
        temperature=0.1,
        json_mode=True,
    )

    assert response == '{"ok": true}'
    assert calls == {
        "model": "custom-model",
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }


def test_openai_client_uses_configured_credentials_and_endpoint(monkeypatch) -> None:
    calls: dict = {}

    def fake_async_openai(**kwargs):
        calls.update(kwargs)
        return object()

    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm_client, "_client", None)
    monkeypatch.setattr(llm_client, "AsyncOpenAI", fake_async_openai)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://llm.example/v1")
    monkeypatch.setattr(settings, "OPENAI_TIMEOUT", 42)
    llm_client.get_llm_client()

    assert calls["api_key"] == "test-openai-key"
    assert calls["base_url"] == "https://llm.example/v1"
    assert calls["timeout"] == 42.0


@pytest.mark.asyncio
async def test_request_middleware_logs_bodies_with_redaction(http_logs) -> None:
    request_payload = json.dumps(
        {
            "agent_id": "agent-1",
            "content": "hello",
            "password": "hunter2",
            "ＰＡＳＳＷＯＲＤ": "fullwidth-password",
            "nested": {
                "api_key": "key-1",
                "ａｐｉ＿ｋｅｙ": "fullwidth-key",
                "items": [{"token": "tok"}],
            },
        }
    ).encode()
    response_payload = json.dumps({"ok": True, "authorization": "Bearer x"}).encode()
    received: list[bytes] = []

    async def app(scope, receive, send):
        chunks: list[bytes] = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        received.append(b"".join(chunks))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json; charset=utf-8")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": response_payload[:6],
                "more_body": True,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": response_payload[6:],
                "more_body": False,
            }
        )

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    middleware = RequestContextMiddleware(app, log_bodies=True)
    await middleware(
        _scope(
            query=b"debug=1",
            request_headers=[(b"content-type", b"application/json")],
        ),
        _receive(request_payload[:7], request_payload[7:]),
        send,
    )

    completed = http_logs[-1]
    assert completed["event"] == "http.request.completed"
    assert completed["http_method"] == "POST"
    assert completed["http_query"] == "debug=1"
    assert completed["request_bytes"] == len(request_payload)
    assert received == [request_payload]
    assert json.loads(completed["request_body"]) == {
        "agent_id": "agent-1",
        "content": "hello",
        "password": "***",
        "ＰＡＳＳＷＯＲＤ": "***",
        "nested": {
            "api_key": "***",
            "ａｐｉ＿ｋｅｙ": "***",
            "items": [{"token": "***"}],
        },
    }
    assert completed["request_body_truncated"] is False
    assert completed["response_bytes"] == len(response_payload)
    assert json.loads(completed["response_body"]) == {
        "ok": True,
        "authorization": "***",
    }
    assert completed["response_body_truncated"] is False


@pytest.mark.asyncio
async def test_request_middleware_omits_bodies_by_default(http_logs) -> None:
    request_payload = b'{"content":"hello"}'

    async def app(scope, receive, send):
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    async def send(message):
        pass

    await RequestContextMiddleware(app)(
        _scope(request_headers=[(b"content-type", b"application/json")]),
        _receive(request_payload),
        send,
    )

    completed = http_logs[-1]
    assert "request_body" not in completed
    assert "request_bytes" not in completed
    assert "response_body" not in completed
    assert "response_bytes" not in completed


@pytest.mark.asyncio
async def test_request_middleware_truncates_bodies(http_logs) -> None:
    request_payload = b'{"content":"hello world"}'

    async def app(scope, receive, send):
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": request_payload})

    async def send(message):
        pass

    middleware = RequestContextMiddleware(app, log_bodies=True, max_body_chars=10)
    await middleware(
        _scope(request_headers=[(b"content-type", b"application/json")]),
        _receive(request_payload),
        send,
    )

    completed = http_logs[-1]
    assert completed["request_body"] == '{"content"'
    assert completed["request_body_truncated"] is True
    assert completed["response_body"] == '{"content"'
    assert completed["response_body_truncated"] is True


@pytest.mark.asyncio
async def test_request_middleware_skips_non_json_and_encoded_bodies(http_logs) -> None:
    async def app(scope, receive, send):
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-encoding", b"gzip"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"\x1f\x8b"})

    async def send(message):
        pass

    middleware = RequestContextMiddleware(app, log_bodies=True)
    await middleware(
        _scope(request_headers=[(b"content-type", b"text/plain")]),
        _receive(b"hello"),
        send,
    )

    completed = http_logs[-1]
    assert completed["request_bytes"] == 5
    assert completed["response_bytes"] == 2
    assert "request_body" not in completed
    assert "response_body" not in completed


@pytest.mark.asyncio
async def test_request_middleware_omits_invalid_json_body(http_logs) -> None:
    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        pass

    middleware = RequestContextMiddleware(app, log_bodies=True)
    await middleware(
        _scope(request_headers=[(b"content-type", b"application/json")]),
        _receive(b"not json"),
        send,
    )

    completed = http_logs[-1]
    assert completed["request_body"] == "[omitted]"
    assert completed["request_body_truncated"] is False
    assert "not json" not in json.dumps(completed)


def test_render_body_never_exposes_truncated_json() -> None:
    capture = _BodyCapture(limit=8)
    capture.add(b'{"content":"private memory"}')

    body, truncated = _render_body(capture, "application/json", "", 2000)

    assert body == "[omitted]"
    assert truncated is True
    assert "private memory" not in body


@pytest.mark.asyncio
async def test_request_middleware_redacts_query_and_logs_route_template(
    http_logs,
) -> None:
    request_payload = b'{"content":"hello"}'
    raw_path = "/v1/mems/write/private-path"
    query = (
        "access_token=query-secret&q=hello+world&"
        "%EF%BC%B0%EF%BC%A1%EF%BC%B3%EF%BC%B3%EF%BC%B7%EF%BC%AF%EF%BC%B2%EF%BC%A4=unicode-secret"
    )

    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        pass

    scope = _scope(path=raw_path, query=query.encode("ascii"))
    scope["route"] = SimpleNamespace(path="/v1/mems/write")
    await RequestContextMiddleware(app, log_bodies=True)(
        scope,
        _receive(request_payload),
        send,
    )

    started = http_logs[0]
    completed = http_logs[-1]
    assert started["operation"] == "http_request"
    assert completed["operation"] == "/v1/mems/write"
    assert raw_path not in json.dumps(http_logs)
    assert "query-secret" not in json.dumps(completed)
    assert "unicode-secret" not in json.dumps(completed)
    assert dict(parse_qsl(completed["http_query"])) == {
        "access_token": "***",
        "q": "hello world",
        "ＰＡＳＳＷＯＲＤ": "***",
    }


@pytest.mark.asyncio
async def test_request_middleware_marks_unmatched_route_without_raw_path(
    http_logs,
) -> None:
    raw_path = "/private/raw-path"

    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        pass

    await RequestContextMiddleware(app)(
        _scope(path=raw_path),
        _receive(b""),
        send,
    )

    completed = http_logs[-1]
    assert completed["operation"] == "unmatched"
    assert raw_path not in json.dumps(http_logs)


@pytest.mark.asyncio
async def test_request_middleware_nonpositive_body_limit_omits_body(
    http_logs,
) -> None:
    async def app(scope, receive, send):
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    async def send(message):
        pass

    await RequestContextMiddleware(app, log_bodies=True, max_body_chars=0)(
        _scope(request_headers=[(b"content-type", b"application/json")]),
        _receive(b'{"content":"hello"}'),
        send,
    )

    completed = http_logs[-1]
    assert completed["request_bytes"] > 0
    assert completed["response_bytes"] > 0
    assert "request_body" not in completed
    assert "response_body" not in completed


@pytest.mark.asyncio
async def test_request_middleware_passes_disconnect_through(http_logs) -> None:
    events = ["request", "disconnect"]
    seen: list[str] = []

    async def app(scope, receive, send):
        seen.append((await receive())["type"])
        seen.append((await receive())["type"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        if events.pop(0) == "request":
            return {"type": "http.request", "body": b"{}", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    middleware = RequestContextMiddleware(app, log_bodies=True)
    await middleware(_scope(), receive, send)

    assert seen == ["http.request", "http.disconnect"]
    assert http_logs[-1]["status_code"] == 204


def test_json_formatter_reads_body_fields_from_record_only() -> None:
    record = logging.LogRecord(
        name="mems.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=30,
        msg="operation completed",
        args=(),
        exc_info=None,
    )

    with bind_log_context(request_body="from-context"):
        result = json.loads(JsonLogFormatter().format(record))
    assert "request_body" not in result

    record.request_body = "from-record"
    with bind_log_context(request_body="from-context"):
        result = json.loads(JsonLogFormatter().format(record))
    assert result["request_body"] == "from-record"


def test_llm_provider_gate_applies_to_cached_client(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", None)
    monkeypatch.setattr(llm_client, "_client", object())
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "embedding-only-key")
    with pytest.raises(ValueError, match="LLM_PROVIDER is disabled"):
        llm_client.get_llm_client()
