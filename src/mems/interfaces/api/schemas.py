"""Public HTTP request schemas.

公开 API 请求模型；响应模型位于 application.commands，字段保持兼容。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemsMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(..., min_length=1, max_length=32)
    content: str = Field(..., min_length=1)


class MemsQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: Optional[str] = Field(default=None)
    user_id: Optional[str] = Field(default=None)
    agent_id: str = Field(...)
    namespace: List[str] = Field(default_factory=list, max_length=16)
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: List[str]) -> List[str]:
        if any(not segment.strip() or len(segment) > 128 for segment in value):
            raise ValueError("namespace segments must be non-empty and at most 128 chars")
        return value


class MemsWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: Optional[str] = Field(default=None)
    user_id: Optional[str] = Field(default=None)
    agent_id: str = Field(...)
    session_id: Optional[str] = Field(default=None)
    namespace: List[str] = Field(default_factory=list, max_length=16)
    content: Optional[str] = Field(default=None, min_length=1)
    messages: List[MemsMessage] = Field(default_factory=list)
    ttl_seconds: int = Field(default=1800, ge=1, le=604800)
    importance_score: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def validate_memory_payload(self) -> "MemsWriteRequest":
        if not self.content and not self.messages:
            raise ValueError("Either content or messages must be provided")
        return self

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: List[str]) -> List[str]:
        if any(not segment.strip() or len(segment) > 128 for segment in value):
            raise ValueError("namespace segments must be non-empty and at most 128 chars")
        return value


class RedistillRequest(BaseModel):
    agent_id: Optional[str] = Field(default=None)
    start_date: Optional[datetime] = Field(default=None)
    end_date: Optional[datetime] = Field(default=None)
    min_importance: float = Field(default=0.0, ge=0, le=1)
    batch_size: Optional[int] = Field(default=None, ge=1, le=1000)
