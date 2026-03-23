from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ===================
# Public API Schemas
# ===================


class MemoryWriteRequest(BaseModel):
    agent_id: str
    session_id: str
    messages: List[Dict[str, str]] = Field(default_factory=list)
    active_plan: Optional[str] = None
    temp_variables: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=1800, description="过期秒数，默认 30 分钟")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")


class MemorySearchRequest(BaseModel):
    agent_id: str
    query: str
    top_k: int = Field(default=5, ge=1, le=100)


# ===================
# Response Schemas
# ===================


class MemoryWriteResponse(BaseModel):
    success: bool
    agent_id: str
    session_id: str
    short_term_buffer: List[Dict[str, str]]
    active_plan: Optional[str]
    temp_variables: Dict[str, Any]
    persisted_to_l1: bool
    l1_id: Optional[int] = None
    message: str


class SearchResultItem(BaseModel):
    source: str = Field(..., description="来源: l1_episodic 或 l2_semantic")
    content: str
    score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class MemorySearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem]
    total: int


class HealthCheckItem(BaseModel):
    status: str
    detail: Optional[str] = None


class MonitorPipelineStatus(BaseModel):
    pending_distill: int
    pending_archive: int
    recent_failures: int = 0
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


class DistillDiscardedItem(BaseModel):
    text: str
    reason: str


class DistillProfileUpdate(BaseModel):
    category: str
    key: str
    value: str
    confidence: float = 0.8
    evidence: str = ""


class DistillFactItem(BaseModel):
    subject: str
    relation: str
    object: str
    fact_type: str = "general"
    confidence: float = 0.8
    evidence: str = ""


class DistillEventItem(BaseModel):
    subject: str
    action: str
    object: str
    time_hint: str = "recent"
    importance: int = 5
    evidence: str = ""


class DistillConflictCandidate(BaseModel):
    memory_type: str
    old: str
    new: str
    reason: str = ""


class DistillExtractionResult(BaseModel):
    discarded: List[DistillDiscardedItem] = Field(default_factory=list)
    profile_updates: List[DistillProfileUpdate] = Field(default_factory=list)
    facts: List[DistillFactItem] = Field(default_factory=list)
    events: List[DistillEventItem] = Field(default_factory=list)
    conflict_candidates: List[DistillConflictCandidate] = Field(default_factory=list)
    long_term_summary: str = ""


class ArchiveResponse(BaseModel):
    success: bool
    archived_count: int
    file_path: str
    message: str


# ===================
# L0 Working Memory
# ===================


class MemsL0Working(BaseModel):
    agent_id: str
    session_id: str
    short_term_buffer: List[Dict[str, str]] = Field(default_factory=list)
    active_plan: Optional[str] = None
    temp_variables: Dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime
