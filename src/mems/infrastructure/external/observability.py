"""Structured logging and correlation context for API and worker operations."""

from __future__ import annotations

import json
import logging
import sys
import traceback
import unicodedata
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode

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
    "archive_id",
    "l3_path",
    "l3_line",
}
# Record-only fields. Bodies must never be bound as log context, otherwise every
# log record produced while handling the request would repeat them.
_HTTP_FIELDS = {
    "http_method",
    "http_query",
    "request_body",
    "request_body_truncated",
    "response_body",
    "response_body_truncated",
    "request_bytes",
    "response_bytes",
}
_RECORD_FIELDS = _SAFE_FIELDS | _HTTP_FIELDS

_REDACTED = "***"
_SENSITIVE_KEYWORDS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "cookie",
    "private_key",
    "client_secret",
)
_MAX_REDACT_DEPTH = 8
_MAX_CAPTURE_BYTES = 64 * 1024


class JsonLogFormatter(logging.Formatter):
    """Emit one safe, machine-readable JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        context = _log_context.get()
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        data.update({key: value for key, value in context.items() if key in _SAFE_FIELDS})
        for key in _RECORD_FIELDS:
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


class _BodyCapture:
    """Buffer a bounded prefix of a request or response body for logging."""

    def __init__(self, limit: int = _MAX_CAPTURE_BYTES) -> None:
        self._limit = limit
        self._chunks: list[bytes] = []
        self._size = 0
        self.total_bytes = 0
        self.truncated = False

    def add(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        if self._size >= self._limit:
            self.truncated = True
            return
        remaining = self._limit - self._size
        if len(chunk) > remaining:
            self._chunks.append(chunk[:remaining])
            self._size = self._limit
            self.truncated = True
            return
        self._chunks.append(chunk)
        self._size += len(chunk)

    @property
    def data(self) -> bytes:
        return b"".join(self._chunks)


def _header_value(headers: list[tuple[bytes, bytes]], name: bytes) -> str:
    for header_name, header_value in headers:
        if header_name.lower() == name:
            return header_value.decode("latin-1").strip()
    return ""


def _is_json_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _is_encoded(encoding: str) -> bool:
    return bool(encoding) and encoding.lower() != "identity"


def _is_sensitive_key(key: str) -> bool:
    normalized = unicodedata.normalize("NFKC", key).casefold()
    return any(keyword in normalized for keyword in _SENSITIVE_KEYWORDS)


def _redact_query(query: str) -> str:
    """Redact sensitive query values while preserving non-sensitive parameters."""
    try:
        pairs = parse_qsl(query, keep_blank_values=True)
    except (UnicodeError, ValueError):
        return "[omitted]"
    return urlencode(
        [(key, _REDACTED if _is_sensitive_key(key) else value) for key, value in pairs]
    )


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_REDACT_DEPTH:
        return "..."
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _is_sensitive_key(str(key)) else _redact(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value]
    return value


def _render_body(
    capture: _BodyCapture,
    content_type: str,
    encoding: str,
    max_chars: int,
) -> tuple[str | None, bool]:
    if max_chars <= 0:
        return None, capture.truncated
    if _is_encoded(encoding) or not capture.total_bytes:
        return None, False
    if not _is_json_content_type(content_type):
        return None, False
    if capture.truncated:
        return "[omitted]", True

    text = capture.data.decode("utf-8", errors="replace")
    try:
        text = json.dumps(
            _redact(json.loads(text)),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return "[omitted]", False

    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return text, truncated


def _body_log_fields(
    capture: _BodyCapture | None,
    content_type: str,
    encoding: str,
    max_chars: int,
    prefix: str,
) -> dict[str, Any]:
    if capture is None:
        return {}
    fields: dict[str, Any] = {f"{prefix}_bytes": capture.total_bytes}
    body, truncated = _render_body(capture, content_type, encoding, max_chars)
    if body is not None:
        fields[f"{prefix}_body"] = body
        fields[f"{prefix}_body_truncated"] = truncated
    elif truncated:
        fields[f"{prefix}_body_truncated"] = True
    return fields


def _route_operation(scope: Scope) -> str:
    route_path = getattr(scope.get("route"), "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return "unmatched"


class RequestContextMiddleware:
    """Attach a generated request ID to logs and the response headers."""

    def __init__(
        self,
        app: ASGIApp,
        log_bodies: bool = False,
        max_body_chars: int = 2000,
    ) -> None:
        self.app = app
        self.log_bodies = log_bodies
        self.max_body_chars = max_body_chars

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        status_code = 500
        started = perf_counter()
        method = scope.get("method", "")
        query = _redact_query(scope.get("query_string", b"").decode("latin-1"))
        request_type = _header_value(scope.get("headers", []), b"content-type")
        request_encoding = _header_value(scope.get("headers", []), b"content-encoding")

        request_capture = _BodyCapture() if self.log_bodies else None
        response_capture = _BodyCapture() if self.log_bodies else None
        response_type = ""
        response_encoding = ""

        if request_capture is not None:
            captured_request = request_capture
            upstream_receive = receive

            async def capture_receive() -> Message:
                message = await upstream_receive()
                if message["type"] == "http.request":
                    captured_request.add(message.get("body", b""))
                return message

            receive = capture_receive

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_type, response_encoding
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                response_type = _header_value(headers, b"content-type")
                response_encoding = _header_value(headers, b"content-encoding")
                message = {**message, "headers": headers}
            elif message["type"] == "http.response.body":
                if response_capture is not None:
                    response_capture.add(message.get("body", b""))
            await send(message)

        with bind_log_context(request_id=request_id, operation="http_request"):
            logging.getLogger("mems.http").info(
                "HTTP request started",
                extra={
                    "event": "http.request.started",
                    "http_method": method,
                    "http_query": query,
                },
            )
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception as exc:
                failed_extra: dict[str, Any] = {
                    "event": "http.request.failed",
                    "operation": _route_operation(scope),
                    "http_method": method,
                    "http_query": query,
                    "error_type": type(exc).__name__,
                }
                failed_extra.update(
                    _body_log_fields(
                        request_capture,
                        request_type,
                        request_encoding,
                        self.max_body_chars,
                        "request",
                    )
                )
                logging.getLogger("mems.http").error(
                    "HTTP request failed",
                    exc_info=True,
                    extra=failed_extra,
                )
                raise
            finally:
                completed_extra: dict[str, Any] = {
                    "event": "http.request.completed",
                    "operation": _route_operation(scope),
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                    "http_method": method,
                    "http_query": query,
                }
                completed_extra.update(
                    _body_log_fields(
                        request_capture,
                        request_type,
                        request_encoding,
                        self.max_body_chars,
                        "request",
                    )
                )
                completed_extra.update(
                    _body_log_fields(
                        response_capture,
                        response_type,
                        response_encoding,
                        self.max_body_chars,
                        "response",
                    )
                )
                logging.getLogger("mems.http").info(
                    "HTTP request completed",
                    extra=completed_extra,
                )
