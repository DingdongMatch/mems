import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from mems.config import settings
from mems.database import get_session
from mems.schemas import L0WriteRequest, L0ReadResponse
from mems.services.redis_service import get_redis_service, RedisService
from mems.services.l0_sync import sync_l0_to_l1


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/l0", tags=["L0 Working Memory"])


@router.post("/write", response_model=L0ReadResponse)
async def write_l0(
    request: L0WriteRequest,
    redis: RedisService = Depends(get_redis_service),
    session: Session = Depends(get_session),
):
    """写入 L0 工作记忆，并自动同步到 L1"""
    try:
        # 1. 写入 L0 (Redis)
        ttl_seconds = request.ttl_seconds or settings.L0_DEFAULT_TTL_SECONDS
        l0 = await redis.write(
            agent_id=request.agent_id,
            session_id=request.session_id,
            messages=request.messages,
            active_plan=request.active_plan,
            temp_variables=request.temp_variables,
            ttl_seconds=ttl_seconds,
        )

        # 2. 自动同步到 L1 (如果启用)
        if settings.L0_AUTO_SYNC_L1:
            await sync_l0_to_l1(
                l0_data=l0,
                session=session,
                importance_score=0.5,
                metadata={"ttl_seconds": ttl_seconds},
            )

        return L0ReadResponse(
            agent_id=l0.agent_id,
            session_id=l0.session_id,
            short_term_buffer=l0.short_term_buffer,
            active_plan=l0.active_plan,
            temp_variables=l0.temp_variables,
        )
    except Exception as e:
        logger.error(f"Failed to write L0: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/read/{agent_id}/{session_id}", response_model=L0ReadResponse)
async def read_l0(
    agent_id: str,
    session_id: str,
    redis: RedisService = Depends(get_redis_service),
):
    """读取 L0 工作记忆"""
    l0 = await redis.read(agent_id, session_id)
    if l0 is None:
        raise HTTPException(status_code=404, detail="L0 memory not found")
    return L0ReadResponse(
        agent_id=l0.agent_id,
        session_id=l0.session_id,
        short_term_buffer=l0.short_term_buffer,
        active_plan=l0.active_plan,
        temp_variables=l0.temp_variables,
    )


@router.delete("/{agent_id}/{session_id}")
async def delete_l0(
    agent_id: str,
    session_id: str,
    redis: RedisService = Depends(get_redis_service),
):
    """删除 L0 工作记忆"""
    result = await redis.delete(agent_id, session_id)
    if not result:
        raise HTTPException(status_code=404, detail="L0 memory not found")
    return {"success": True, "message": "L0 memory deleted"}


@router.post("/commit/{agent_id}/{session_id}")
async def commit_l0_to_l1(
    agent_id: str,
    session_id: str,
    redis: RedisService = Depends(get_redis_service),
    session: Session = Depends(get_session),
):
    """手动提交 L0 到 L1（用于会话结束时的主动提交）"""
    l0 = await redis.read(agent_id, session_id)
    if l0 is None:
        raise HTTPException(status_code=404, detail="L0 memory not found")

    l1_id = await sync_l0_to_l1(l0_data=l0, session=session)
    
    if l1_id:
        # 删除 L0
        await redis.delete(agent_id, session_id)
        return {"success": True, "l1_id": l1_id, "message": "L0 committed to L1"}
    else:
        return {"success": False, "message": "Failed to sync to L1"}