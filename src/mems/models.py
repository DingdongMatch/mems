from datetime import datetime
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
    metadata_json: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


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
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MemsL3Archive(SQLModel, table=True):
    """L3: 长期归档索引"""
    __tablename__ = "mems_l3_archive"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    time_period: str
    summary_text: str
    file_path: str
    record_count: int = 0
    archived_at: datetime = Field(default_factory=datetime.utcnow)