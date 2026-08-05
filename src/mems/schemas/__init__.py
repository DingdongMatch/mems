from datetime import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, Field, model_validator


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
    """Request body for remembering memory content.

    用于向记忆系统写入记忆内容的请求体。
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
    session_id: Optional[str] = Field(
        default=None,
        description="Optional source session id. / 可选的来源会话 id。",
    )
    # Soft visibility tag defined by the upstream business.
    # 上层业务定义的软可见性标签。
    scope: Optional[str] = Field(
        default=None,
        description="Soft visibility tag for the turns. / 本次 turn 的软可见性标签。",
    )
    content: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Memory text to store directly. / 直接写入的记忆文本。",
    )
    messages: List[MemsMessage] = Field(
        default_factory=list,
        description="Optional source messages to remember. / 可选的来源消息。",
    )
    ttl_seconds: int = Field(
        default=1800, ge=1, le=604800, description="L0 TTL，单位秒"
    )
    importance_score: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description=(
            "Memory importance used for distillation priority. "
            "Records at or above DISTILL_HIGH_IMPORTANCE_THRESHOLD are distilled "
            "immediately after write. / 记忆重要度，用于蒸馏优先级；达到"
            "DISTILL_HIGH_IMPORTANCE_THRESHOLD 的记录写入后立即蒸馏。"
        ),
    )

    @model_validator(mode="after")
    def validate_memory_payload(self) -> "MemsWriteRequest":
        """Require either direct content or source messages.

        要求至少提供直接 content 或来源 messages。
        """
        if not self.content and not self.messages:
            raise ValueError("Either content or messages must be provided")
        return self


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
    created_at: Optional[datetime] = None
    source_l1_ids: List[int] = Field(default_factory=list)
    session_id: Optional[str] = None
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


class MemsWriteResponse(BaseModel):
    """Response payload after remembering content.

    写入记忆后的响应载荷。
    """

    success: bool
    # Hard identity boundaries echoed back to the caller.
    # 回传给调用方的硬身份边界。
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    # Soft visibility tag echoed back to the caller.
    # 回传给调用方的软可见性标签。
    scope: Optional[str] = None
    importance_score: float = Field(
        default=0.5,
        description="Memory importance echoed back to the caller. / 回传的记忆重要度。",
    )
    content: str
    messages: List[Dict[str, str]] = Field(default_factory=list)
    appended_count: int = Field(
        description="Number of source messages remembered in this request. / 本次请求写入的来源消息条数。"
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
    # Whether the L0 memory output pipeline (Redis Stream) is enabled.
    # L0 记忆输出管道（Redis Stream）是否启用。
    l0_pipeline_enabled: bool = False
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
    """In-memory L0 hot-memory snapshot stored in Redis.

    存储在 Redis 中的 L0 热记忆快照。
    """

    # Hard identity boundaries for the live working-memory slot.
    # 实时工作记忆槽位使用的硬身份边界。
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    # Soft visibility tag defined by the upstream business.
    # 上层业务定义的软可见性标签。
    scope: Optional[str] = None
    content: Optional[str] = None
    short_term_buffer: List[Dict[str, str]] = Field(default_factory=list)
    # TTL-based expiration time for the live Redis snapshot.
    # Redis 实时快照按 TTL 计算出的过期时间。
    expires_at: datetime


class MemoryPipelineEvent(BaseModel):
    """One memory event published to the L0 output stream.

    发布到 L0 输出流的一条记忆事件。

    The event is a flat snapshot of the L0 working memory after a
    write/append. Redis Streams store flat field-value pairs, so nested
    structures are JSON-encoded strings (e.g. ``messages``).
    """

    # Stable event id generated by Mems (consumers can use it for idempotency).
    # Mems 生成的稳定事件 id（消费方可用来做幂等）。
    event_id: str
    # "write" for a fresh L0 snapshot, "append" for a buffer continuation.
    # "write" 表示新建 L0 快照，"append" 表示在现有缓冲区上追加。
    event_type: str = Field(
        ...,
        pattern="^(write|append)$",
        description="write 或 append。",
    )
    timestamp: datetime = Field(
        description="Event publication time in UTC. / 事件发布时间（UTC）。"
    )
    # Identity context of the memory snapshot.
    # 该记忆快照的身份上下文；未设置的字段在流中为空字符串。
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    scope: Optional[str] = None
    # Memory text snapshot after this event.
    # 本次事件之后的记忆文本快照。
    content: Optional[str] = None
    # JSON-encoded list of short-term buffer messages.
    # 短期缓冲区消息的 JSON 编码。
    messages: str = Field(
        default="[]",
        description="JSON-encoded short-term buffer. / JSON 编码的短期缓冲区。",
    )
    # When the L0 snapshot expires (TTL end).
    # L0 快照的过期时间（TTL 结束时刻）。
    expires_at: Optional[datetime] = None
