from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ===================
# Request Schemas
# ===================

class IngestRequest(BaseModel):
    agent_id: str = Field(..., description="Agent 唯一标识")
    session_id: str = Field(default="", description="会话 ID")
    content: str = Field(..., description="原始对话内容")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")
    importance_score: float = Field(default=0.0, ge=0.0, le=1.0)


class L0WriteRequest(BaseModel):
    agent_id: str
    session_id: str
    messages: List[Dict[str, str]] = Field(default_factory=list)
    active_plan: Optional[str] = None
    temp_variables: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=1800, description="过期秒数，默认 30 分钟")


class SearchRequest(BaseModel):
    agent_id: str
    query: str
    top_k: int = Field(default=5, ge=1, le=100)
    include_l2: bool = Field(default=False, description="是否包含 L2 语义知识")


class DistillRequest(BaseModel):
    agent_id: str = Field(..., description="指定 Agent")
    batch_size: int = Field(default=10, ge=1, le=100)
    force: bool = Field(default=False, description="是否强制重新蒸馏")


class ArchiveRequest(BaseModel):
    agent_id: str = Field(..., description="指定 Agent")
    days: int = Field(default=30, ge=1, description="超过多少天的数据归档")


# ===================
# Response Schemas
# ===================

class IngestResponse(BaseModel):
    success: bool
    l1_id: int
    vector_id: str
    message: str


class L0ReadResponse(BaseModel):
    agent_id: str
    session_id: str
    short_term_buffer: List[Dict[str, str]]
    active_plan: Optional[str]
    temp_variables: Dict[str, Any]


class SearchResultItem(BaseModel):
    source: str = Field(..., description="来源: l1_episodic 或 l2_semantic")
    content: str
    score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem]
    total: int


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