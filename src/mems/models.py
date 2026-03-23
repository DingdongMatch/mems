from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class MemsL1Episodic(SQLModel, table=True):
    """L1: 情景记录表"""

    __tablename__ = "mems_l1_episodic"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    session_id: str = Field(index=True, default="")
    content: str
    vector_id: str = Field(unique=True, index=True)
    importance_score: float = 0.0
    is_distilled: bool = Field(default=False, index=True)
    is_archived: bool = Field(default=False, index=True)
    metadata_json: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL2Semantic(SQLModel, table=True):
    """L2: 语义知识表"""

    __tablename__ = "mems_l2_semantic"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    subject: str = Field(index=True)
    predicate: str
    object: str
    confidence: float = 1.0
    source_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    version: int = 1
    is_active: bool = Field(default=True, index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL2ProfileItem(SQLModel, table=True):
    """L2: 用户画像与偏好"""

    __tablename__ = "mems_l2_profile_item"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    category: str = Field(index=True)
    key: str = Field(index=True)
    value: str
    confidence: float = 1.0
    status: str = Field(default="active", index=True)
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    supersedes_id: Optional[int] = Field(default=None, index=True)
    version: int = 1
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemsL2Fact(SQLModel, table=True):
    """L2: 稳定事实与实体关系"""

    __tablename__ = "mems_l2_fact"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    subject: str = Field(index=True)
    predicate: str = Field(index=True)
    object: str
    fact_type: str = Field(default="general", index=True)
    confidence: float = 1.0
    status: str = Field(default="active", index=True)
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    supersedes_id: Optional[int] = Field(default=None, index=True)
    version: int = 1
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemsL2Event(SQLModel, table=True):
    """L2: 事件层知识"""

    __tablename__ = "mems_l2_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    subject: str = Field(index=True)
    action: str = Field(index=True)
    object: str
    time_hint: Optional[str] = None
    importance_score: int = 5
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL2Summary(SQLModel, table=True):
    """L2: 滚动摘要"""

    __tablename__ = "mems_l2_summary"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    summary_type: str = Field(default="long_term", index=True)
    content: str
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL2ConflictLog(SQLModel, table=True):
    """L2: 冲突记录"""

    __tablename__ = "mems_l2_conflict_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    memory_type: str = Field(index=True)
    old_value: str
    new_value: str
    resolution: str
    reason: str = ""
    source_l1_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemsL3Archive(SQLModel, table=True):
    """L3: 长期归档索引"""

    __tablename__ = "mems_l3_archive"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    time_period: str
    summary_text: str
    file_path: str
    record_count: int = 0
    archived_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
