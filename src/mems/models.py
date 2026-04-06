from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class MemsL1Episodic(SQLModel, table=True):
    """Store raw episodic memory records persisted from live sessions.

    存储从实时会话落盘的原始 L1 情景记忆记录。
    """

    __tablename__ = "mems_l1_episodic"

    # Hard identity boundaries for multi-tenant and multi-user isolation.
    # 多租户、多用户隔离使用的硬身份边界。
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    agent_id: str = Field(index=True)
    session_id: str = Field(index=True, default="")
    # Soft visibility tag defined by the upstream business.
    # 上层业务定义的软可见性标签。
    scope: Optional[str] = Field(default=None, index=True)
    # Flattened L1 content, usually derived from live turns or plan snapshots.
    # L1 展平后的内容，通常来自实时对话或计划快照。
    content: str
    # Stable vector replica id shared with the vector store.
    # 与向量库共享的稳定向量副本 id。
    vector_id: str = Field(unique=True, index=True)
    importance_score: float = 0.0
    is_distilled: bool = Field(default=False, index=True)
    is_archived: bool = Field(default=False, index=True)
    # Replica sync states for vector and archive pipelines.
    # 向量与归档流水线的副本同步状态。
    vector_status: str = Field(default="pending", index=True)
    archive_status: str = Field(default="pending", index=True)
    last_sync_error: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    # Extra payload such as messages, active_plan, and runtime metadata.
    # 额外载荷，例如 messages、active_plan 与运行态元数据。
    metadata_json: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL2Semantic(SQLModel, table=True):
    """Store generic semantic triples distilled into L2 knowledge.

    存储蒸馏进入 L2 的通用语义三元组知识。
    """

    __tablename__ = "mems_l2_semantic"

    # Hard identity boundaries carried over from source memories.
    # 从源记忆继承而来的硬身份边界。
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    agent_id: str = Field(index=True)
    scope: Optional[str] = Field(default=None, index=True)
    subject: str = Field(index=True)
    predicate: str
    object: str
    confidence: float = 1.0
    # Source L1 records used to produce this semantic item.
    # 生成该语义项所使用的来源 L1 记录。
    source_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    version: int = 1
    is_active: bool = Field(default=True, index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL2ProfileItem(SQLModel, table=True):
    """Store stable user profile items such as preferences and habits.

    存储稳定的用户画像信息，例如偏好、习惯等。
    """

    __tablename__ = "mems_l2_profile_item"

    # Hard identity boundaries carried over from source memories.
    # 从源记忆继承而来的硬身份边界。
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    agent_id: str = Field(index=True)
    scope: Optional[str] = Field(default=None, index=True)
    # Category + key + value together represent one profile assertion.
    # category + key + value 共同表示一条画像断言。
    category: str = Field(index=True)
    key: str = Field(index=True)
    value: str
    confidence: float = 1.0
    # Active/superseded lifecycle used during reconciliation.
    # 对账过程中使用的 active/superseded 生命周期状态。
    status: str = Field(default="active", index=True)
    # Source lineage and version chain for profile evolution.
    # 画像演化过程中的来源链路与版本链。
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    supersedes_id: Optional[int] = Field(default=None, index=True)
    version: int = 1
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemsL2Fact(SQLModel, table=True):
    """Store durable facts and entity relations in the L2 layer.

    存储 L2 层中的稳定事实与实体关系。
    """

    __tablename__ = "mems_l2_fact"

    # Hard identity boundaries carried over from source memories.
    # 从源记忆继承而来的硬身份边界。
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    agent_id: str = Field(index=True)
    scope: Optional[str] = Field(default=None, index=True)
    # subject + predicate + object represent one durable fact triple.
    # subject + predicate + object 表示一条稳定事实三元组。
    subject: str = Field(index=True)
    predicate: str = Field(index=True)
    object: str
    fact_type: str = Field(default="general", index=True)
    confidence: float = 1.0
    # Active/superseded lifecycle used during reconciliation.
    # 对账过程中使用的 active/superseded 生命周期状态。
    status: str = Field(default="active", index=True)
    # Source lineage and version chain for fact evolution.
    # 事实演化过程中的来源链路与版本链。
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    supersedes_id: Optional[int] = Field(default=None, index=True)
    version: int = 1
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemsL2Event(SQLModel, table=True):
    """Store important extracted events with time hints and sources.

    存储带时间线索与来源信息的重要提取事件。
    """

    __tablename__ = "mems_l2_event"

    # Hard identity boundaries carried over from source memories.
    # 从源记忆继承而来的硬身份边界。
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    agent_id: str = Field(index=True)
    scope: Optional[str] = Field(default=None, index=True)
    subject: str = Field(index=True)
    action: str = Field(index=True)
    object: str
    time_hint: Optional[str] = None
    # Source L1 records and event importance used for later recall.
    # 用于后续召回的来源 L1 记录与事件重要度。
    importance_score: int = 5
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL2Summary(SQLModel, table=True):
    """Store rolling long-term summaries distilled from L1 activity.

    存储从 L1 活动中蒸馏出的滚动长期摘要。
    """

    __tablename__ = "mems_l2_summary"

    # Hard identity boundaries carried over from source memories.
    # 从源记忆继承而来的硬身份边界。
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    agent_id: str = Field(index=True)
    scope: Optional[str] = Field(default=None, index=True)
    # Vector replica and sync status for semantic summary recall.
    # 语义摘要召回使用的向量副本及其同步状态。
    summary_type: str = Field(default="long_term", index=True)
    content: str
    vector_id: Optional[str] = Field(default=None, unique=True, index=True)
    vector_status: str = Field(default="pending", index=True)
    last_sync_error: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    # Source lineage of the summary content.
    # 摘要内容对应的来源链路。
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemsL2ConflictLog(SQLModel, table=True):
    """Store audit logs for profile and fact reconciliation conflicts.

    存储画像与事实对账过程中产生的冲突审计日志。
    """

    __tablename__ = "mems_l2_conflict_log"

    # Hard identity boundaries carried over from source memories.
    # 从源记忆继承而来的硬身份边界。
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    agent_id: str = Field(index=True)
    scope: Optional[str] = Field(default=None, index=True)
    # Audit fields that explain how conflicting memory values were resolved.
    # 描述冲突值如何被处理的审计字段。
    memory_type: str = Field(index=True)
    old_value: str
    new_value: str
    resolution: str
    reason: str = ""
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL3Archive(SQLModel, table=True):
    """Store archive batch metadata for long-term L3 retention.

    存储长期 L3 归档批次的元数据信息。
    """

    __tablename__ = "mems_l3_archive"

    # Hard identity boundaries for the archived batch index.
    # 归档批次索引使用的硬身份边界。
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    agent_id: str = Field(index=True)
    scope: Optional[str] = Field(default=None, index=True)
    # Archive batch descriptor and file pointer for L3 storage.
    # L3 存储中归档批次的描述信息与文件指针。
    time_period: str
    summary_text: str
    file_path: str
    record_count: int = 0
    archived_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
