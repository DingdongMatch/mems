"""L3 archive aggregate.

L3 归档聚合。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ArchiveBatch(BaseModel):
    """Metadata aggregate for one L3 archive batch."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    scope: Optional[str] = None
    time_period: str
    summary_text: str
    file_path: str
    record_count: int = 0
    archived_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
