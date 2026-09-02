"""Outbox event value object and event types.

事务性发件箱（Outbox）事件类型与值对象。所有需要在主事务提交后异步执行的
副作用（向量副本同步、归档、蒸馏触发）都先写成 Outbox 事件，再由 worker
按状态机 claim / done / failed 处理，保证跨存储最终一致与分布式互斥。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class OutboxEventType(StrEnum):
    """Known outbox event kinds."""

    VECTOR_SYNC = "vector_sync"
    ARCHIVE_AGENT = "archive_agent"
    DISTILL_AGENT = "distill_agent"
    DISTILL_DUE = "distill_due"


class OutboxEvent(BaseModel):
    """A durable, idempotent outbox event."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    event_type: OutboxEventType
    aggregate_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    attempts: int = 0
    owner: Optional[str] = None
    last_error: Optional[str] = None
    available_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
