from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from mems.database import get_session
from mems.schemas import ArchiveRequest, ArchiveResponse
from mems.services.archive import ArchiveService


router = APIRouter(prefix="/archive", tags=["Archive"])


@router.post("", response_model=ArchiveResponse)
async def archive_old_memories(
    request: ArchiveRequest,
    session: Session = Depends(get_session),
):
    """归档接口 - 超过指定天数的 L1 数据归档到 L3"""
    try:
        archive_service = ArchiveService(session)
        result = await archive_service.archive(
            agent_id=request.agent_id,
            days=request.days,
        )
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))