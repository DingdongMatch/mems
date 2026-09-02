"""Thin HTTP router for the Mems public API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from mems.application.commands import (
    MemsQueryResponse,
    MemsWriteResponse,
    QueryResultItem,
    RedistillResponse,
    MonitorStatusResponse,
)
from mems.application.query_service import QueryMemoryService
from mems.application.status_service import StatusApplicationService
from mems.application.write_service import WriteMemoryService
from mems.interfaces.api.dependencies import (
    get_query_service,
    get_status_service,
    get_write_service,
)
from mems.interfaces.bootstrap import trigger_redistill
from mems.interfaces.api.schemas import (
    MemsQueryRequest,
    MemsWriteRequest,
    RedistillRequest,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/mems", tags=["Mems"])


WRITE_MEMORY_EXAMPLE = {
    "summary": "Remember memory content / 写入记忆内容",
    "value": {
        "tenant_id": "tenant_demo",
        "user_id": "user_alice",
        "agent_id": "support_agent",
        "session_id": "session_20260401_001",
        "scope": "private",
        "content": "The user likes Rust and concise answers.",
        "messages": [
            {"role": "user", "content": "Remember that I like Rust and concise answers."},
            {"role": "assistant", "content": "Stored. I will keep replies concise."},
        ],
        "ttl_seconds": 1800,
        "importance_score": 0.5,
    },
}

SEARCH_MEMORY_EXAMPLE = {
    "summary": "Hybrid memory search / 混合记忆检索",
    "value": {
        "tenant_id": "tenant_demo",
        "user_id": "user_alice",
        "agent_id": "support_agent",
        "scope": "private",
        "query": "What programming language does the user prefer?",
        "top_k": 5,
    },
}

WRITE_MEMORY_RESPONSE_EXAMPLE = {
    "content": {
        "application/json": {
            "example": {
                "success": True,
                "tenant_id": "tenant_demo",
                "user_id": "user_alice",
                "agent_id": "support_agent",
                "session_id": "session_20260401_001",
                "scope": "private",
                "importance_score": 0.5,
                "content": "The user likes Rust and concise answers.",
                "messages": [
                    {"role": "user", "content": "Remember that I like Rust and concise answers."},
                    {"role": "assistant", "content": "Stored. I will keep replies concise."},
                ],
                "appended_count": 2,
                "persisted_to_l1": True,
                "l1_id": 101,
                "message": "Memory remembered and persisted to L1",
            }
        }
    }
}

MEMS_STATUS_RESPONSE_EXAMPLE = {
    "content": {
        "application/json": {
            "example": {
                "status": "healthy",
                "version": "0.1.0",
                "timestamp": "2026-04-01T10:00:00Z",
                "checks": {
                    "database": {"status": "healthy", "detail": None},
                    "redis": {"status": "healthy", "detail": None},
                    "qdrant": {"status": "healthy", "detail": None},
                    "scheduler": {"status": "healthy", "detail": None},
                },
                "pipeline": {
                    "pending_distill": 3,
                    "pending_archive": 1,
                    "recent_failures": 0,
                    "l0_pipeline_enabled": False,
                    "profile_items": 12,
                    "fact_items": 24,
                    "summary_items": 4,
                    "conflict_count": 1,
                    "stale_profile_items": 0,
                    "stale_fact_items": 2,
                    "stale_summary_items": 0,
                },
            }
        }
    }
}

SEARCH_MEMORY_RESPONSE_EXAMPLE = {
    "content": {
        "application/json": {
            "example": {
                "query": "What programming language does the user prefer?",
                "results": [
                    {
                        "source": "l2_profile",
                        "content": "like technology Rust",
                        "score": 1.52,
                        "source_l1_ids": [101],
                        "created_at": "2026-04-01T10:00:00Z",
                        "tenant_id": "tenant_demo",
                        "user_id": "user_alice",
                        "scope": "private",
                    },
                    {
                        "source": "l1_episodic",
                        "content": "user: Remember that I like Rust and concise answers.",
                        "score": 1.14,
                        "source_l1_ids": [101],
                        "session_id": "session_20260401_001",
                        "created_at": "2026-04-01T10:00:00Z",
                        "tenant_id": "tenant_demo",
                        "user_id": "user_alice",
                        "scope": "private",
                    },
                ],
                "total": 2,
            }
        }
    }
}

TURN_WRITE_RESPONSE_EXAMPLE = {
    "content": {
        "application/json": {
            "example": {
                "success": True,
                "tenant_id": "tenant_demo",
                "user_id": "user_alice",
                "agent_id": "support_agent",
                "session_id": "session_20260401_001",
                "scope": "private",
                "importance_score": 0.5,
                "content": "user: Please answer in Chinese.\nassistant: 好的，我会继续使用中文回答。",
                "messages": [
                    {"role": "user", "content": "Please answer in Chinese."},
                    {"role": "assistant", "content": "好的，我会继续使用中文回答。"},
                ],
                "appended_count": 2,
                "persisted_to_l1": True,
                "l1_id": 102,
                "message": "Memory remembered and persisted to L1",
            }
        }
    }
}


def _to_write_command(request: MemsWriteRequest):
    from mems.application.commands import WriteMemoryCommand

    return WriteMemoryCommand(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        agent_id=request.agent_id,
        session_id=request.session_id,
        scope=request.scope,
        content=request.content,
        messages=[message.model_dump() for message in request.messages],
        ttl_seconds=request.ttl_seconds,
        importance_score=request.importance_score,
    )


def _to_query_command(request: MemsQueryRequest):
    from mems.application.commands import QueryMemoryCommand

    return QueryMemoryCommand(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        agent_id=request.agent_id,
        scope=request.scope,
        query=request.query,
        top_k=request.top_k,
    )


@router.post(
    "/write",
    response_model=MemsWriteResponse,
    summary="Remember memory / 写入记忆",
    description=(
        "Remember memory content into L0 hot memory and persist the primary SQL record into L1. "
        "Mems stores memories and leaves runtime state to the caller.\n\n"
        "把记忆内容写入 L0 热记忆，并持久化主 L1 SQL 记录。Mems 只处理记忆，运行时状态由调用方管理。"
    ),
    responses={200: {"description": "Memory was remembered successfully.", **TURN_WRITE_RESPONSE_EXAMPLE}},
)
async def write_memory(
    request: MemsWriteRequest = Body(
        openapi_examples={"basic": WRITE_MEMORY_EXAMPLE},  # type: ignore[dict-item]
    ),
    service: WriteMemoryService = Depends(get_write_service),
):
    try:
        result = await service.remember(_to_write_command(request))
        return result
    except Exception as e:
        logger.error(f"Failed to write memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/query",
    response_model=MemsQueryResponse,
    summary="Query memory / 记忆检索",
    description=(
        "Search across L0 hot cache, L1 episodic, and L2 semantic memory. L0 recent memory and active L1 are searched alongside L2 distilled knowledge. "
        "Archived L1 records are excluded from default online recall.\n\n"
        "跨 L0 热缓存、L1 情景记忆和 L2 语义记忆进行检索。L0 近期记忆与活跃 L1 一同参与检索，并连同 L2 蒸馏知识返回。"
        "默认不会召回已归档的 L1 记录。"
    ),
    responses={200: {"description": "Ranked hybrid memory results.", **SEARCH_MEMORY_RESPONSE_EXAMPLE}},
)
async def search_memory(
    request: MemsQueryRequest = Body(
        openapi_examples={"basic": SEARCH_MEMORY_EXAMPLE},  # type: ignore[dict-item]
    ),
    service: QueryMemoryService = Depends(get_query_service),
):
    try:
        result = await service.recall(_to_query_command(request))
        items = [
            QueryResultItem(
                source=item.source,
                content=item.content,
                score=item.score,
                created_at=item.created_at,
                source_l1_ids=item.source_l1_ids,
                session_id=item.session_id,
                tenant_id=item.tenant_id,
                user_id=item.user_id,
                scope=item.scope,
            )
            for item in result.items
        ]
        return MemsQueryResponse(query=result.query, results=items, total=result.total)
    except Exception as e:
        logger.error(f"Failed to search memories: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/redistill",
    response_model=RedistillResponse,
    summary="Re-distill archived memories / 再蒸馏归档记忆",
    description=(
        "Manually re-distill archived L3 records into upgraded L2 using the "
        "current OPENAI_MODEL. Records already processed with the same model "
        "are skipped (content fingerprint + model dedup), so model upgrades "
        "automatically queue old data for re-distillation.\n\n"
        "手动触发 L3 → L2 再蒸馏：用当前 OPENAI_MODEL 升级 L2。已用同一模型处理过"
        "的记录按内容指纹跳过，模型升级后旧数据会自动进入待重蒸队列。"
    ),
)
async def redistill_memories(
    request: RedistillRequest = Body(
        openapi_examples={
            "basic": {
                "summary": "Re-distill everything / 全量再蒸馏",
                "value": {"min_importance": 0.0, "batch_size": 100},
            }
        }
    ),
):
    result = await trigger_redistill(
        agent_id=request.agent_id,
        start_date=request.start_date,
        end_date=request.end_date,
        min_importance=request.min_importance,
        batch_size=request.batch_size,
    )
    if not result.success:
        raise HTTPException(status_code=500, detail=result.message)
    return result


@router.get(
    "/status",
    response_model=MonitorStatusResponse,
    summary="System status / 系统状态",
    description=(
        "Return component health checks together with memory-pipeline metrics such as pending distillation, pending archive, and stale knowledge counts.\n\n"
        "返回组件健康检查结果，以及记忆流水线指标，例如待蒸馏数量、待归档数量和陈旧知识数量。"
    ),
    responses={200: {"description": "Component health checks and pipeline metrics.", **MEMS_STATUS_RESPONSE_EXAMPLE}},
)
async def mems_status(
    service: StatusApplicationService = Depends(get_status_service),
):
    return await service.status()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "version": "0.1.0"}
