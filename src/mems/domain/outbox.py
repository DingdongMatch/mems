"""Outbox event value object and event types.

事务性发件箱（Outbox）事件类型与值对象。所有需要在主事务提交后异步执行的
副作用（向量副本同步、归档、蒸馏触发）都先写成 Outbox 事件，再由 worker
按状态机 claim / done / failed 处理，保证跨存储最终一致与分布式互斥。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import json
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class OutboxEventType(StrEnum):
    """Known outbox event kinds."""

    VECTOR_SYNC = "vector_sync"
    VECTOR_DELETE = "vector_delete"
    ARCHIVE_AGENT = "archive_agent"
    DISTILL_AGENT = "distill_agent"
    DISTILL_DUE = "distill_due"


def outbox_aggregate_key(
    event_type: str | OutboxEventType,
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    namespace: Optional[str] = None,
    resource: Optional[str] = None,
) -> str:
    """Build an unambiguous, stable key from the complete memory identity."""
    return json.dumps(
        {
            "event_type": str(event_type),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "namespace": namespace,
            "resource": resource,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class OutboxEvent(BaseModel):
    """A durable, idempotent outbox event."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    event_type: OutboxEventType
    aggregate_id: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    namespace: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    attempts: int = 0
    owner: Optional[str] = None
    last_error: Optional[str] = None
    dead: bool = False
    dead_at: Optional[datetime] = None
    available_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
