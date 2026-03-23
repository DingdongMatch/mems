import uuid
import logging
from typing import List, Dict, Any, Optional
from sqlmodel import Session, select

from mems.config import settings
from mems.models import MemsL1Episodic
from mems.schemas import MemsL0Working
from mems.services.embedding import get_embedding_service
from mems.services.vector_service import get_vector_service
from mems.services.jsonl_utils import JsonlWriter


logger = logging.getLogger(__name__)


async def sync_l0_to_l1(
    l0_data: MemsL0Working,
    session: Session,
    importance_score: float = 0.5,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    将 L0 工作记忆同步到 L1
    
    Args:
        l0_data: L0 工作记忆数据
        session: 数据库会话
        importance_score: 重要性评分
        metadata: 额外元数据
    
    Returns:
        L1 记录 ID，失败返回 None
    """
    try:
        # 将 L0 的消息合并为一个内容
        if l0_data.short_term_buffer:
            content_parts = []
            for msg in l0_data.short_term_buffer:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                content_parts.append(f"{role}: {content}")
            content = "\n".join(content_parts)
        else:
            content = f"Active plan: {l0_data.active_plan}" if l0_data.active_plan else ""
        
        if not content:
            logger.warning("L0 data is empty, skipping sync to L1")
            return None

        vector_id = str(uuid.uuid4())

        # 获取向量服务
        vector_service = await get_vector_service()
        embedding_service = await get_embedding_service()

        # 生成向量
        embeddings = await embedding_service.embed([content])
        vector = embeddings[0]

        # 写入 Qdrant
        await vector_service.upsert(
            collection_name=f"agent_{l0_data.agent_id}",
            points=[
                {
                    "id": vector_id,
                    "vector": vector,
                    "payload": {
                        "agent_id": l0_data.agent_id,
                        "session_id": l0_data.session_id,
                        "content": content,
                    },
                }
            ],
        )

        # 合并元数据
        full_metadata = metadata or {}
        full_metadata["active_plan"] = l0_data.active_plan
        full_metadata["temp_variables"] = l0_data.temp_variables
        full_metadata["source"] = "l0_sync"

        # 写入 L1
        l1_record = MemsL1Episodic(
            agent_id=l0_data.agent_id,
            session_id=l0_data.session_id,
            content=content,
            vector_id=vector_id,
            importance_score=importance_score,
            is_distilled=False,
            metadata_json=full_metadata,
        )
        session.add(l1_record)
        session.commit()
        session.refresh(l1_record)

        # 同步写 JSONL
        l1_writer = JsonlWriter(settings.storage_l1_path, "l1")
        l1_writer.write(
            l0_data.agent_id,
            {
                "id": l1_record.id,
                "agent_id": l0_data.agent_id,
                "session_id": l0_data.session_id,
                "content": content,
                "vector_id": vector_id,
                "importance_score": importance_score,
                "metadata": full_metadata,
                "created_at": l1_record.created_at.isoformat(),
                "sync_from": "l0",
            },
        )

        logger.info(f"L0 synced to L1: agent={l0_data.agent_id}, session={l0_data.session_id}, l1_id={l1_record.id}")
        return l1_record.id

    except Exception as e:
        logger.error(f"Failed to sync L0 to L1: {e}")
        session.rollback()
        return None


async def batch_sync_l0_to_l1(
    l0_records: List[MemsL0Working],
    session: Session,
    importance_score: float = 0.5,
) -> int:
    """批量同步 L0 到 L1"""
    synced_count = 0
    for l0_data in l0_records:
        result = await sync_l0_to_l1(l0_data, session, importance_score)
        if result:
            synced_count += 1
    return synced_count