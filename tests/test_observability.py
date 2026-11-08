from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from mems.config import settings
from mems.infrastructure.external import llm_client
from mems.infrastructure.external.observability import (
    JsonLogFormatter,
    RequestContextMiddleware,
    bind_log_context,
)


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
    assert (
        "test_json_formatter_omits_exception_message"
        in parsed["exception"]["traceback"]
    )


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

    monkeypatch.setattr(llm_client, "_client", None)
    monkeypatch.setattr(llm_client, "AsyncOpenAI", fake_async_openai)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://llm.example/v1")
    monkeypatch.setattr(settings, "OPENAI_TIMEOUT", 42)
    llm_client.get_llm_client()

    assert calls["api_key"] == "test-openai-key"
    assert calls["base_url"] == "https://llm.example/v1"
    assert calls["timeout"] == 42.0
