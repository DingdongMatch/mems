import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mems.config import settings
from mems.database import get_session
from mems.models import MemsL1Episodic
from mems.schemas import IngestRequest, IngestResponse
from mems.services.embedding import get_embedding_service
from mems.services.jsonl_utils import JsonlWriter
from mems.services.vector_service import get_vector_service


router = APIRouter(prefix="/ingest", tags=["Ingestion"])


@router.post("", response_model=IngestResponse)
async def ingest_memory(
    request: IngestRequest,
    session: Session = Depends(get_session),
):
    """记忆摄取接口 - 写入 L1 情景记忆"""
    try:
        vector_id = str(uuid.uuid4())

        vector_service = await get_vector_service()
        embedding_service = await get_embedding_service()

        embeddings = await embedding_service.embed([request.content])
        vector = embeddings[0]

        await vector_service.upsert(
            collection_name=f"agent_{request.agent_id}",
            points=[
                {
                    "id": vector_id,
                    "vector": vector,
                    "payload": {
                        "agent_id": request.agent_id,
                        "session_id": request.session_id,
                        "content": request.content,
                    },
                }
            ],
        )

        l1_record = MemsL1Episodic(
            agent_id=request.agent_id,
            session_id=request.session_id,
            content=request.content,
            vector_id=vector_id,
            importance_score=request.importance_score,
            is_distilled=False,
            metadata_json=request.metadata,
        )
        session.add(l1_record)
        session.commit()
        session.refresh(l1_record)

        l1_writer = JsonlWriter(settings.storage_l1_path, "l1")
        l1_writer.write(
            request.agent_id,
            {
                "id": l1_record.id,
                "agent_id": request.agent_id,
                "session_id": request.session_id,
                "content": request.content,
                "vector_id": vector_id,
                "importance_score": request.importance_score,
                "metadata": request.metadata,
                "created_at": l1_record.created_at.isoformat(),
            },
        )

        return IngestResponse(
            success=True,
            l1_id=l1_record.id,
            vector_id=vector_id,
            message="Memory ingested successfully",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))