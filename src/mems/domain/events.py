"""Domain events for Mems.

当前仅保留实际被使用的 L0 管道集成事件；蒸馏、归档等内部信号统一由
Outbox（见 ``mems.domain.outbox``）承载，不再维护未消费的领域事件类。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MemoryPipelineEvent(BaseModel):
    """Flat integration event published to the L0 Redis stream.

    发布到 L0 输出流的扁平集成事件。
    """

    event_id: str
    event_type: str = Field(
        ...,
        pattern="^(write|append)$",
    )
    timestamp: datetime
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    scope: Optional[str] = None
    content: Optional[str] = None
    messages: str = Field(default="[]")
    expires_at: Optional[datetime] = None
