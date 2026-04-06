import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from sqlmodel import Session

from mems.models import MemsL1Episodic
from mems.schemas import MemsL0Working
from mems.services.embedding import get_embedding_service
from mems.services.vector_service import get_vector_service

logger = logging.getLogger(__name__)


def _mark_l1_replica_status(
    session: Session,
    record: MemsL1Episodic,
    *,
    vector_status: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Update L1 replica sync status fields after a downstream write.

    在下游副本写入后更新 L1 记录的同步状态字段。
    """
    if vector_status is not None:
        record.vector_status = vector_status
    if error is not None:
        record.last_sync_error = error
    record.last_sync_at = datetime.now(timezone.utc)
    session.add(record)
    session.commit()
    session.refresh(record)


async def sync_l0_to_l1(
    l0_data: MemsL0Working,
    session: Session,
    importance_score: float = 0.5,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Persist one L0 snapshot into L1 and its replicas.

    将单个 L0 快照落入 L1，并同步向量副本。
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
            content = (
                f"Active plan: {l0_data.active_plan}" if l0_data.active_plan else ""
            )

        if not content:
            logger.warning("L0 data is empty, skipping sync to L1")
            return None

        vector_id = str(uuid.uuid4())

        # 合并元数据
        full_metadata = dict(metadata or {})
        full_metadata["tenant_id"] = l0_data.tenant_id
        full_metadata["user_id"] = l0_data.user_id
        full_metadata["scope"] = l0_data.scope
        full_metadata["active_plan"] = l0_data.active_plan
        full_metadata["temp_variables"] = l0_data.temp_variables
        full_metadata.setdefault("source", "l0_sync")
        full_metadata["sync_source"] = "l0_sync"

        # 写入 L1
        l1_record = MemsL1Episodic(
            tenant_id=l0_data.tenant_id,
            user_id=l0_data.user_id,
            agent_id=l0_data.agent_id,
            session_id=l0_data.session_id,
            scope=l0_data.scope,
            content=content,
            vector_id=vector_id,
            importance_score=importance_score,
            is_distilled=False,
            vector_status="pending",
            archive_status="pending",
            metadata_json=full_metadata,
        )
        session.add(l1_record)
        session.commit()
        session.refresh(l1_record)

        try:
            vector_service = await get_vector_service()
            embedding_service = await get_embedding_service()
            embeddings = await embedding_service.embed([content])
            vector = embeddings[0]
            await vector_service.upsert(
                collection_name=f"agent_{l0_data.agent_id}",
                points=[
                    {
                        "id": vector_id,
                        "vector": vector,
                        "payload": {
                            "l1_id": l1_record.id,
                            "vector_id": vector_id,
                            "tenant_id": l0_data.tenant_id,
                            "user_id": l0_data.user_id,
                            "agent_id": l0_data.agent_id,
                            "session_id": l0_data.session_id,
                            "scope": l0_data.scope,
                            "content": content,
                        },
                    }
                ],
            )
            _mark_l1_replica_status(session, l1_record, vector_status="ready")
        except Exception as exc:
            logger.error("Failed to sync L1 vector replica: %s", exc)
            _mark_l1_replica_status(
                session, l1_record, vector_status="failed", error=f"vector: {exc}"
            )

        logger.info(
            f"L0 synced to L1: agent={l0_data.agent_id}, session={l0_data.session_id}, l1_id={l1_record.id}"
        )
        return l1_record.id

    except Exception as e:
        logger.error(f"Failed to sync L0 to L1: {e}")
        session.rollback()
        return None
