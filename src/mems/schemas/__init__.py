from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ===================
# Public API Schemas
# ===================


class MemoryWriteRequest(BaseModel):
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    scope: Optional[str] = None
    messages: List[Dict[str, str]] = Field(default_factory=list)
    active_plan: Optional[str] = None
    temp_variables: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=1800, description="L0 过期秒数，默认 30 分钟")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="附加元数据，会一并持久化到 L1"
    )


class MemorySearchRequest(BaseModel):
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    scope: Optional[str] = None
    query: str
    top_k: int = Field(
        default=5,
        ge=1,
        le=100,
        description="返回结果数。默认搜索仅覆盖活跃 L1 与 L2，不包含已归档 L1。",
    )


class MemoryContextRequest(BaseModel):
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    scope: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)


class MemoryTurn(BaseModel):
    role: str = Field(..., min_length=1, max_length=32)
    content: str = Field(..., min_length=1)


class MemoryTurnWriteRequest(BaseModel):
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    scope: Optional[str] = None
    messages: List[MemoryTurn] = Field(default_factory=list, min_length=1)
    active_plan: Optional[str] = None
    temp_variables: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(
        default=1800, ge=1, le=604800, description="L0 TTL，单位秒"
    )
    persist_to_l1: bool = Field(
        default=True,
        description="是否把本次 turn 持久化到 L1。持久化时先提交 SQL，再同步派生副本。",
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class SimulatorChatRequest(BaseModel):
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str = Field(default="__reference_agent__")
    session_id: str
    scope: Optional[str] = None
    message: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


# ===================
# Response Schemas
# ===================


class MemoryWriteResponse(BaseModel):
    success: bool
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    scope: Optional[str] = None
    short_term_buffer: List[Dict[str, str]]
    active_plan: Optional[str]
    temp_variables: Dict[str, Any]
    persisted_to_l1: bool = Field(
        description="是否已成功写入 L1 主记录（不代表向量/JSONL 副本一定已同步完成）"
    )
    l1_id: Optional[int] = None
    message: str


class SearchResultItem(BaseModel):
    source: str = Field(
        ...,
        description="结果来源，例如 `l1_episodic`、`l2_profile`、`l2_fact`、`l2_event`、`l2_summary`",
    )
    content: str
    score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    scope: Optional[str] = None


class MemorySearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem]
    total: int = Field(description="返回结果数量。默认不包含已归档 L1。")


class MemoryContextResponse(BaseModel):
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    scope: Optional[str] = None
    source: str = Field(description="上下文来源: l0 或 l1_fallback")
    messages: List[Dict[str, str]] = Field(default_factory=list)
    total: int
    expires_at: Optional[datetime] = None


class MemoryTurnWriteResponse(BaseModel):
    success: bool
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    scope: Optional[str] = None
    short_term_buffer: List[Dict[str, str]]
    appended_count: int
    persisted_to_l1: bool = Field(
        description="是否已成功写入 L1 主记录（副本同步可能稍后修复）"
    )
    l1_id: Optional[int] = None
    message: str


class SimulatorChatDebug(BaseModel):
    mode: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    scope: Optional[str] = None
    context_source: str
    context_messages_count: int
    search_hits: int
    used_sources: List[str] = Field(default_factory=list)
    memory_write_success: bool
    fallback_used: bool
    context_messages: List[Dict[str, str]] = Field(default_factory=list)
    prompt_messages: List[Dict[str, str]] = Field(default_factory=list)
    search_query: str = ""
    write_l1_id: Optional[int] = None
    persisted_to_l1: bool = False


class SimulatorChatResponse(BaseModel):
    answer: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    scope: Optional[str] = None
    retrieved_memories: List[SearchResultItem] = Field(default_factory=list)
    debug: SimulatorChatDebug


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
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    scope: Optional[str] = None
    short_term_buffer: List[Dict[str, str]] = Field(default_factory=list)
    active_plan: Optional[str] = None
    temp_variables: Dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime
