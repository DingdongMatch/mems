"""Memory identity value objects.

记忆身份与时间范围等值对象。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class MemoryIdentity(BaseModel):
    """Immutable memory identity used for isolation and key generation.

    不可变的记忆身份值对象，用于隔离过滤与存储键构造。
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    scope: Optional[str] = None

    def redis_key(self) -> str:
        """Build the stable L0 Redis key from identity fields.

        根据身份字段构造稳定的 L0 Redis 键。
        """
        tenant_part = self.tenant_id or "_"
        user_part = self.user_id or "_"
        scope_part = self.scope or "_"
        session_part = self.session_id or "_"
        return (
            f"mems:l0:{tenant_part}:{user_part}:{scope_part}:"
            f"{self.agent_id}:{session_part}"
        )

    def filter_fields(self) -> dict[str, Any]:
        """Return non-None tenant/user/scope fields for SQL/Qdrant filtering.

        返回用于 SQL/Qdrant 过滤的非空身份字段。
        """
        fields: dict[str, Any] = {}
        if self.tenant_id is not None:
            fields["tenant_id"] = self.tenant_id
        if self.user_id is not None:
            fields["user_id"] = self.user_id
        if self.scope is not None:
            fields["scope"] = self.scope
        return fields

    def as_dict(self) -> dict[str, Optional[str]]:
        """Return all identity fields as a plain dictionary."""
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "scope": self.scope,
        }


class TimeRange(BaseModel):
    """An optional half-open time range used by re-distillation filters.

    再蒸馏过滤使用的可选时间范围值对象。
    """

    model_config = ConfigDict(frozen=True)

    start: Optional[datetime] = None
    end: Optional[datetime] = None

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def contains(self, value: datetime) -> bool:
        """Return True when ``value`` falls inside the configured range."""
        value = self._as_utc(value)
        if self.start is not None and value < self._as_utc(self.start):
            return False
        if self.end is not None and value > self._as_utc(self.end):
            return False
        return True


class Importance(BaseModel):
    """Bounded importance value used by distillation policy decisions.

    用于蒸馏策略判断的有界重要度值对象。
    """

    model_config = ConfigDict(frozen=True)

    value: float = Field(ge=0.0, le=1.0)

    def is_high_value(self, threshold: float) -> bool:
        return self.value >= threshold

    def as_float(self) -> float:
        return self.value
