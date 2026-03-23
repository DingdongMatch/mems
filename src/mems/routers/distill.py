from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mems.config import settings
from mems.database import get_session
from mems.models import MemsL1Episodic, MemsL2Semantic
from mems.schemas import DistillRequest, DistillResponse
from mems.services.distill import DistillService
from mems.services.embedding import get_embedding_service


router = APIRouter(prefix="/distill", tags=["Distillation"])


@router.post("", response_model=DistillResponse)
async def distill_l1_to_l2(
    request: DistillRequest,
    session: Session = Depends(get_session),
):
    """记忆蒸馏接口 - L1 → L2"""
    try:
        distill_service = DistillService(session)
        embedding_service = await get_embedding_service()

        result = await distill_service.distill(
            agent_id=request.agent_id,
            batch_size=request.batch_size,
            force=request.force,
            embedding_service=embedding_service,
        )
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))