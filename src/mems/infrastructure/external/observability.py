"""Structured logging and correlation context for API and worker operations."""

from __future__ import annotations

import json
import logging
import sys
import traceback
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Iterator

from starlette.types import ASGIApp, Message, Receive, Scope, Send


_log_context: ContextVar[dict[str, Any]] = ContextVar("mems_log_context", default={})
_SAFE_FIELDS = {
    "event",
    "request_id",
    "outbox_event_id",
    "outbox_type",
    "source_l1_id",
    "agent_id",
    "operation",
    "duration_ms",
    "status_code",
    "attempts",
    "result",
    "model",
    "batch_size",
    "error_type",
    "count",
    "l2_created",
    "l2_updated",
    "skipped",
}


class JsonLogFormatter(logging.Formatter):
    """Emit one safe, machine-readable JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        context = _log_context.get()
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        data.update(
            {key: value for key, value in context.items() if key in _SAFE_FIELDS}
        )
        for key in _SAFE_FIELDS:
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            exc_type, _exc_value, exc_tb = record.exc_info
            data["exception"] = {
                "type": exc_type.__name__ if exc_type else "Exception",
                # Include call sites but omit exception text, which can contain
                # request payloads returned by third-party clients.
                "traceback": "".join(traceback.extract_tb(exc_tb).format()),
            }
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure application and framework loggers to use JSON on stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        framework_logger = logging.getLogger(logger_name)
        framework_logger.handlers.clear()
        framework_logger.propagate = True


@contextmanager
def bind_log_context(**fields: Any) -> Iterator[None]:
    """Temporarily add safe correlation fields to log records in this context."""
    merged = {**_log_context.get(), **fields}
    token = _log_context.set(merged)
    try:
        yield
    finally:
        _log_context.reset(token)


class RequestContextMiddleware:
    """Attach a generated request ID to logs and the response headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        status_code = 500
        started = perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with bind_log_context(request_id=request_id, operation="http_request"):
            logging.getLogger("mems.http").info(
                "HTTP request started",
                extra={
                    "event": "http.request.started",
                    "operation": scope.get("path", ""),
                },
            )
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception as exc:
                logging.getLogger("mems.http").error(
                    "HTTP request failed",
                    exc_info=True,
                    extra={
                        "event": "http.request.failed",
                        "error_type": type(exc).__name__,
                    },
                )
                raise
            finally:
                logging.getLogger("mems.http").info(
                    "HTTP request completed",
                    extra={
                        "event": "http.request.completed",
                        "status_code": status_code,
                        "duration_ms": round((perf_counter() - started) * 1000, 2),
                    },
                )
