"""Public HTTP request schemas.

公开 API 请求模型；响应模型位于 application.commands，字段保持兼容。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mems.config import settings
from mems.domain.identity import MemoryNamespace


_IDENTITY_MAX_LENGTH = 128
_NAMESPACE_SEGMENT_MAX_LENGTH = 128


def _validate_identity(value: str) -> str:
    if not value.strip():
        raise ValueError("identity fields must not be blank")
    return value


def _validate_optional_identity(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _validate_identity(value)


def _validate_namespace(value: List[str]) -> List[str]:
    if any(
        not segment.strip() or len(segment) > _NAMESPACE_SEGMENT_MAX_LENGTH for segment in value
    ):
        raise ValueError(
            "namespace segments must be non-empty and at most "
            f"{_NAMESPACE_SEGMENT_MAX_LENGTH} chars"
        )
    return value


class _PartitionRequest(BaseModel):
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    namespace: List[str] = Field(
        default_factory=list,
        max_length=16,
        description="Canonical identity + namespace must fit within 512 UTF-8 bytes",
    )

    @model_validator(mode="after")
    def validate_partition_size(self):
        namespace = MemoryNamespace.build(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            path=tuple(self.namespace),
        )
        if len(namespace.key.encode("utf-8")) > 512:
            raise ValueError("canonical identity + namespace exceeds 512 UTF-8 bytes")
        return self


class MemsMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(..., min_length=1, max_length=32)
    content: str = Field(
        ..., min_length=1, max_length=settings.MEMORY_MESSAGE_CONTENT_MAX_CHARS
    )


class MemsQueryRequest(_PartitionRequest):
    model_config = ConfigDict(extra="forbid")

    tenant_id: Optional[str] = Field(default=None, min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    user_id: Optional[str] = Field(default=None, min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    agent_id: str = Field(..., min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    query: str = Field(min_length=1, max_length=settings.QUERY_MAX_CHARS)
    top_k: int = Field(default=5, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    @field_validator("tenant_id", "user_id")
    @classmethod
    def validate_optional_identity(cls, value: Optional[str]) -> Optional[str]:
        return _validate_optional_identity(value)

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        return _validate_identity(value)

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: List[str]) -> List[str]:
        return _validate_namespace(value)


class MemsWriteRequest(_PartitionRequest):
    model_config = ConfigDict(extra="forbid")

    tenant_id: Optional[str] = Field(default=None, min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    user_id: Optional[str] = Field(default=None, min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    agent_id: str = Field(..., min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    content: Optional[str] = Field(
        default=None, min_length=1, max_length=settings.MEMORY_CONTENT_MAX_CHARS
    )
    messages: List[MemsMessage] = Field(
        default_factory=list, max_length=settings.MEMORY_MESSAGE_MAX_COUNT
    )
    ttl_seconds: int = Field(default=1800, ge=1, le=604800)
    importance_score: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def validate_memory_payload(self) -> "MemsWriteRequest":
        if not self.content and not self.messages:
            raise ValueError("Either content or messages must be provided")
        return self

    @field_validator("tenant_id", "user_id", "session_id")
    @classmethod
    def validate_optional_identity(cls, value: Optional[str]) -> Optional[str]:
        return _validate_optional_identity(value)

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        return _validate_identity(value)

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: List[str]) -> List[str]:
        return _validate_namespace(value)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped


class RedistillRequest(_PartitionRequest):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(..., min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    user_id: str = Field(..., min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    agent_id: str = Field(..., min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    start_date: Optional[datetime] = Field(default=None)
    end_date: Optional[datetime] = Field(default=None)
    min_importance: float = Field(default=0.0, ge=0, le=1)
    batch_size: Optional[int] = Field(
        default=None,
        ge=1,
        le=1000,
        description="Maximum eligible sources attempted, including failures; capped by REDISTILL_BATCH_SIZE",
    )

    @field_validator("tenant_id", "user_id", "agent_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _validate_identity(value)

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: List[str]) -> List[str]:
        return _validate_namespace(value)
