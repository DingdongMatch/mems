from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ===================
# Public API Schemas
# ===================
class MemsQueryRequest(BaseModel):
    """Request body for hybrid memory search.

    用于混合记忆检索的请求体。
    """

    # Hard identity boundaries for retrieval isolation.
    # 检索隔离使用的硬身份边界。
    tenant_id: Optional[str] = Field(
        default=None, description="Optional tenant boundary. / 可选租户边界。"
    )
    user_id: Optional[str] = Field(
        default=None, description="Optional user boundary. / 可选用户边界。"
    )
    agent_id: str = Field(
        description="Agent boundary used for search isolation. / 用于检索隔离的 Agent 边界。"
    )
    # Soft visibility tag defined by the upstream business.
    # 上层业务定义的软可见性标签。
    scope: Optional[str] = Field(
        default=None,
        description="Soft visibility tag for filtering. / 用于过滤的软可见性标签。",
    )
    query: str = Field(
        description="User search query. / 用户检索查询内容。",
        examples=["What does the user like?"],
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=100,
        description="返回结果数。默认搜索仅覆盖活跃 L1 与 L2，不包含已归档 L1。",
    )


class MemsContextRequest(BaseModel):
    """Request model for fetching live context or history pages.

    用于获取实时上下文或历史分页的请求模型。
    """

    # Hard identity boundaries for context isolation.
    # 上下文隔离使用的硬身份边界。
    tenant_id: Optional[str] = Field(
        default=None, description="Optional tenant boundary. / 可选租户边界。"
    )
    user_id: Optional[str] = Field(
        default=None, description="Optional user boundary. / 可选用户边界。"
    )
    agent_id: str = Field(
        description="Agent boundary used for context isolation. / 用于上下文隔离的 Agent 边界。"
    )
    session_id: str = Field(
        description="Session whose context should be loaded. / 需要加载上下文的 session。"
    )
    # Soft visibility tag defined by the upstream business.
    # 上层业务定义的软可见性标签。
    scope: Optional[str] = Field(
        default=None,
        description="Soft visibility tag for filtering. / 用于过滤的软可见性标签。",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of L1 records per page. / 每页返回的 L1 记录数。",
    )
    before_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="History pagination cursor; loads older L1 records with smaller ids. / 历史分页游标；传入后返回 id 更小的更早 L1 记录。",
    )


class MemsMessage(BaseModel):
    """One chat turn with role and content.

    表示一条包含角色与内容的对话消息。
    """

    role: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Message role such as user or assistant. / 消息角色，例如 user 或 assistant。",
    )
    content: str = Field(
        ..., min_length=1, description="Message text content. / 消息文本内容。"
    )


class MemsWriteRequest(BaseModel):
    """Request body for appending conversation turns into memory.

    用于向记忆系统追加会话轮次的请求体。
    """

    # Hard identity boundaries for multi-tenant and multi-user isolation.
    # 多租户、多用户隔离使用的硬身份边界。
    tenant_id: Optional[str] = Field(
        default=None, description="Optional tenant boundary. / 可选租户边界。"
    )
    user_id: Optional[str] = Field(
        default=None, description="Optional user boundary. / 可选用户边界。"
    )
    agent_id: str = Field(
        description="Agent boundary used for write isolation. / 用于写入隔离的 Agent 边界。"
    )
    session_id: str = Field(
        description="Live session receiving the appended turns. / 接收追加消息的实时 session。"
    )
    # Soft visibility tag defined by the upstream business.
    # 上层业务定义的软可见性标签。
    scope: Optional[str] = Field(
        default=None,
        description="Soft visibility tag for the turns. / 本次 turn 的软可见性标签。",
    )
    messages: List[MemsMessage] = Field(
        default_factory=list,
        min_length=1,
        description="Turns to append, usually one user turn plus one assistant turn. / 要追加的消息，通常为一条 user 和一条 assistant。",
    )
    # Current task/goal of the agent in this session.
    # 当前会话中 agent 正在推进的任务或目标。
    active_plan: Optional[str] = Field(
        default=None,
        description="Current task or goal of the session. / 当前会话正在推进的任务目标。",
    )
    # Ephemeral runtime state, not durable long-term facts.
    # 临时运行态变量，不代表长期事实。
    temp_variables: Dict[str, Any] = Field(
        default_factory=dict,
        description="Ephemeral runtime variables for the live session. / 当前实时会话的临时运行态变量。",
    )
    ttl_seconds: int = Field(
        default=1800, ge=1, le=604800, description="L0 TTL，单位秒"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extra metadata written with this turn batch. / 随本次 turn 一起写入的附加元数据。",
    )


# ===================
# Response Schemas
# ===================


class QueryResultItem(BaseModel):
    """One normalized memory search result across L1 and L2.

    表示跨 L1 与 L2 统一格式的一条检索结果。
    """

    source: str = Field(
        ...,
        description="结果来源，例如 `l1_episodic`、`l2_profile`、`l2_fact`、`l2_event`、`l2_summary`",
    )
    content: str
    score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    # Identity context attached to the recalled memory item.
    # 命中记忆项所附带的身份上下文。
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    scope: Optional[str] = None


class MemsQueryResponse(BaseModel):
    """Response payload for memory search results.

    记忆检索结果的响应载荷。
    """

    query: str = Field(description="Original search query. / 原始检索查询。")
    results: List[QueryResultItem]
    total: int = Field(
        description="Number of returned results; archived L1 records are excluded by default. / 返回结果数量；默认不包含已归档 L1。"
    )


class MemsContextResponse(BaseModel):
    """Response payload for live context or historical pages.

    实时上下文或历史分页的响应载荷。
    """

    # Hard identity boundaries echoed back for the returned page.
    # 当前返回页对应的硬身份边界。
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    # Soft visibility tag echoed back for the returned page.
    # 当前返回页对应的软可见性标签。
    scope: Optional[str] = None
    source: str = Field(
        description="Context source: `l0`, `l1`, or `mixed`. / 上下文来源：`l0`、`l1` 或 `mixed`。"
    )
    page_type: str = Field(
        description="`live` for the first page, `history` for older L1 pages. / `live` 表示首页，`history` 表示更早的 L1 历史页。"
    )
    messages: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Flattened message list returned for this page. / 当前页返回的扁平化消息列表。",
    )
    total: int = Field(
        description="Number of messages returned on this page. / 当前页返回的消息数量。"
    )
    has_more: bool = Field(
        default=False,
        description="Whether older history pages are still available. / 是否还有更早的历史页。",
    )
    next_before_id: Optional[int] = Field(
        default=None,
        description="Cursor for loading the next older history page. / 用于继续加载更早历史页的游标。",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="Expiration time of the live L0 snapshot, if present. / 若存在 L0 快照，则为其过期时间。",
    )


class MemsWriteResponse(BaseModel):
    """Response payload after appending conversation turns.

    追加会话轮次后的响应载荷。
    """

    success: bool
    # Hard identity boundaries echoed back to the caller.
    # 回传给调用方的硬身份边界。
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    # Soft visibility tag echoed back to the caller.
    # 回传给调用方的软可见性标签。
    scope: Optional[str] = None
    short_term_buffer: List[Dict[str, str]]
    appended_count: int = Field(
        description="Number of turns appended in this request. / 本次请求追加的消息条数。"
    )
    persisted_to_l1: bool = Field(
        description="是否已成功写入 L1 主记录（副本同步可能稍后修复）"
    )
    l1_id: Optional[int] = Field(
        default=None,
        description="Primary L1 record id if persistence succeeded. / 若持久化成功，对应的主 L1 记录 id。",
    )
    message: str


class HealthCheckItem(BaseModel):
    """One component health-check result.

    单个组件的健康检查结果。
    """

    status: str
    detail: Optional[str] = None


class MonitorPipelineStatus(BaseModel):
    """Pipeline metrics exposed by the monitor endpoint.

    监控接口暴露的流水线统计指标。
    """

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
    """Full monitor endpoint response including checks and metrics.

    包含健康检查与指标信息的完整监控响应。
    """

    status: str
    version: str
    timestamp: datetime
    checks: Dict[str, HealthCheckItem]
    pipeline: MonitorPipelineStatus


class DistillResponse(BaseModel):
    """Result summary returned after an L1-to-L2 distillation run.

    一次 L1 到 L2 蒸馏执行后的结果摘要。
    """

    success: bool
    distilled_count: int
    l2_created: int
    l2_updated: int
    message: str


class DistillDiscardedItem(BaseModel):
    """One discarded fragment and the reason it was ignored.

    一条被丢弃片段及其被忽略原因。
    """

    text: str
    reason: str


class DistillProfileUpdate(BaseModel):
    """One extracted profile update candidate from distillation.

    蒸馏过程中提取出的一条画像更新候选项。
    """

    category: str
    key: str
    value: str
    confidence: float = 0.8
    evidence: str = ""


class DistillFactItem(BaseModel):
    """One extracted fact candidate from distillation.

    蒸馏过程中提取出的一条事实候选项。
    """

    subject: str
    relation: str
    object: str
    fact_type: str = "general"
    confidence: float = 0.8
    evidence: str = ""


class DistillEventItem(BaseModel):
    """One extracted event candidate from distillation.

    蒸馏过程中提取出的一条事件候选项。
    """

    subject: str
    action: str
    object: str
    time_hint: str = "recent"
    importance: int = 5
    evidence: str = ""


class DistillConflictCandidate(BaseModel):
    """One potential memory conflict surfaced during distillation.

    蒸馏过程中识别出的一条潜在记忆冲突。
    """

    memory_type: str
    old: str
    new: str
    reason: str = ""


class DistillExtractionResult(BaseModel):
    """Structured extraction payload produced by the LLM.

    由 LLM 产出的结构化提取结果载荷。
    """

    discarded: List[DistillDiscardedItem] = Field(default_factory=list)
    profile_updates: List[DistillProfileUpdate] = Field(default_factory=list)
    facts: List[DistillFactItem] = Field(default_factory=list)
    events: List[DistillEventItem] = Field(default_factory=list)
    conflict_candidates: List[DistillConflictCandidate] = Field(default_factory=list)
    long_term_summary: str = ""


class ArchiveResponse(BaseModel):
    """Result summary returned after an archive operation.

    归档操作完成后返回的结果摘要。
    """

    success: bool
    archived_count: int
    file_path: str
    message: str


# ===================
# L0 Working Memory
# ===================


class MemsL0Working(BaseModel):
    """In-memory L0 working-memory snapshot stored in Redis.

    存储在 Redis 中的 L0 工作记忆快照。
    """

    # Hard identity boundaries for the live working-memory slot.
    # 实时工作记忆槽位使用的硬身份边界。
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: str
    # Soft visibility tag defined by the upstream business.
    # 上层业务定义的软可见性标签。
    scope: Optional[str] = None
    short_term_buffer: List[Dict[str, str]] = Field(default_factory=list)
    # Current task/goal being actively pursued in this session.
    # 当前会话里正在推进的任务或目标。
    active_plan: Optional[str] = None
    # Ephemeral runtime state that should travel with the live session.
    # 需要随实时会话一起保存的临时运行态变量。
    temp_variables: Dict[str, Any] = Field(default_factory=dict)
    # TTL-based expiration time for the live Redis snapshot.
    # Redis 实时快照按 TTL 计算出的过期时间。
    expires_at: datetime
