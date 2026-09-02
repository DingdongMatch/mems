"""Application commands and read/result models.

应用命令与结果模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from mems.domain.services.ranking import ScoredMemory


class WriteMemoryCommand(BaseModel):
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    scope: Optional[str] = None
    content: Optional[str] = None
    messages: List[Dict[str, str]] = Field(default_factory=list)
    ttl_seconds: int = 1800
    importance_score: float = 0.5


class WriteMemoryResult(BaseModel):
    success: bool
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    scope: Optional[str] = None
    importance_score: float = 0.5
    content: str
    messages: List[Dict[str, str]] = Field(default_factory=list)
    appended_count: int = 0
    persisted_to_l1: bool = False
    l1_id: Optional[int] = None
    message: str


class QueryMemoryCommand(BaseModel):
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    scope: Optional[str] = None
    query: str
    top_k: int = 5


class RecallResult(BaseModel):
    query: str
    items: List[ScoredMemory] = Field(default_factory=list)
    total: int = 0


class RedistillCommand(BaseModel):
    agent_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    min_importance: float = 0.0
    batch_size: Optional[int] = None


class HealthCheckItem(BaseModel):
    status: str
    detail: Optional[str] = None


class MonitorPipelineStatus(BaseModel):
    pending_distill: int
    pending_archive: int
    recent_failures: int = 0
    l0_pipeline_enabled: bool = False
    profile_items: int = 0
    fact_items: int = 0
    summary_items: int = 0
    conflict_count: int = 0
    stale_profile_items: int = 0
    stale_fact_items: int = 0
    stale_summary_items: int = 0


class MonitorStatusResponse(BaseModel):
    status: str
    version: str
    timestamp: datetime
    checks: Dict[str, HealthCheckItem]
    pipeline: MonitorPipelineStatus


class DistillResponse(BaseModel):
    success: bool
    distilled_count: int
    l2_created: int
    l2_updated: int
    message: str


class ArchiveResponse(BaseModel):
    success: bool
    archived_count: int
    file_path: str
    message: str


class RedistillResponse(BaseModel):
    success: bool
    processed: int
    created: int
    updated: int
    skipped: int
    message: str


class QueryResultItem(BaseModel):
    source: str
    content: str
    score: float
    created_at: Optional[datetime] = None
    source_l1_ids: List[int] = Field(default_factory=list)
    session_id: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    scope: Optional[str] = None


class MemsQueryResponse(BaseModel):
    query: str
    results: List[QueryResultItem]
    total: int


class MemsWriteResponse(BaseModel):
    success: bool
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    scope: Optional[str] = None
    importance_score: float = 0.5
    content: str
    messages: List[Dict[str, str]] = Field(default_factory=list)
    appended_count: int = 0
    persisted_to_l1: bool = False
    l1_id: Optional[int] = None
    message: str
