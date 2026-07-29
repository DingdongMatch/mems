import logging
import re
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from mems.config import settings
from mems.database import get_session
from mems.models import (
    MemsL1Episodic,
    MemsL2ConflictLog,
    MemsL2Event,
    MemsL2Fact,
    MemsL2ProfileItem,
    MemsL2Summary,
)
from mems.schemas import (
    HealthCheckItem,
    MemsQueryRequest,
    MemsQueryResponse,
    MemsWriteRequest,
    MemsWriteResponse,
    MonitorPipelineStatus,
    MonitorStatusResponse,
    QueryResultItem,
)
from mems.services.embedding import get_embedding_service
from mems.services.l0_sync import sync_l0_to_l1
from mems.services.redis_service import RedisService, get_redis_service
from mems.services.scheduler import scheduler_service
from mems.services.vector_service import get_vector_service


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/mems", tags=["Mems"])
DEFAULT_CONTEXT_WINDOW = 20
SEARCH_CANDIDATE_LIMIT_MULTIPLIER = 10
SEARCH_MIN_CANDIDATE_LIMIT = 50
STALE_MEMORY_DAYS = 365

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
            {
                "role": "user",
                "content": "Remember that I like Rust and concise answers.",
            },
            {"role": "assistant", "content": "Stored. I will keep replies concise."},
        ],
        "ttl_seconds": 1800,
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
                "content": "The user likes Rust and concise answers.",
                "messages": [
                    {
                        "role": "user",
                        "content": "Remember that I like Rust and concise answers.",
                    },
                    {
                        "role": "assistant",
                        "content": "Stored. I will keep replies concise.",
                    },
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

INTENT_KEYWORDS = {
    "profile": {
        "like",
        "likes",
        "dislike",
        "dislikes",
        "prefer",
        "preference",
        "habit",
        "style",
        "喜欢",
        "讨厌",
        "偏好",
        "习惯",
        "风格",
    },
    "fact": {
        "who",
        "what",
        "where",
        "project",
        "language",
        "relationship",
        "事实",
        "项目",
        "技术",
        "关系",
        "语言",
    },
    "event": {
        "recent",
        "today",
        "yesterday",
        "doing",
        "working",
        "latest",
        "最近",
        "今天",
        "昨天",
        "正在",
        "进展",
    },
    "summary": {
        "summary",
        "overview",
        "focus",
        "trend",
        "总结",
        "概括",
        "关注",
        "趋势",
    },
}

SOURCE_INTENT = {
    "l1_episodic": "event",
    "l2_profile": "profile",
    "l2_fact": "fact",
    "l2_event": "event",
    "l2_summary": "summary",
}


def _keyword_score(query: str, text: str, base_score: float) -> float:
    """Blend lexical term overlap into a base retrieval score.

    将关键词重叠度叠加到基础检索分数上。
    """
    query_terms = {term for term in re.split(r"\W+", query.lower()) if term}
    text_terms = {term for term in re.split(r"\W+", text.lower()) if term}
    overlap = len(query_terms & text_terms)
    if not query_terms:
        return base_score
    return base_score + overlap / max(len(query_terms), 1)


def _detect_query_intents(query: str) -> set[str]:
    """Infer coarse search intents from the user query.

    根据用户查询推断粗粒度检索意图。
    """
    lowered = query.lower()
    terms = {term for term in re.split(r"\W+", lowered) if term}
    intents = set()
    for intent, keywords in INTENT_KEYWORDS.items():
        if terms & keywords:
            intents.add(intent)
    if not intents:
        intents = {"fact", "event"}
    return intents


def _freshness_bonus(created_at: datetime | None, horizon_days: int = 365) -> float:
    """Reward newer memories with a bounded freshness bonus.

    为较新的记忆提供一个有上限的新鲜度加分。
    """
    if created_at is None:
        return 0.0
    now = datetime.now(timezone.utc)
    timestamp = created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_days = max((now - timestamp).total_seconds() / 86400.0, 0.0)
    freshness = max(0.0, 1.0 - min(age_days / horizon_days, 1.0))
    return freshness * 0.25


def _verification_decay(verified_at: datetime | None, stale_days: int = 365) -> float:
    """Decay trust for memories that have not been re-verified recently.

    对长期未再次验证的记忆降低可信度权重。
    """
    if verified_at is None:
        return 0.55
    now = datetime.now(timezone.utc)
    timestamp = verified_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_days = max((now - timestamp).total_seconds() / 86400.0, 0.0)
    decay = min(age_days / stale_days, 1.0)
    return 1.0 - decay * 0.45


def _intent_bonus(source: str, intents: set[str]) -> float:
    """Boost items whose memory type matches the inferred intents.

    为与推断意图匹配的记忆类型增加额外分值。
    """
    mapped = SOURCE_INTENT.get(source)
    if mapped == "summary" and "summary" in intents:
        return 0.7
    if mapped in intents:
        return 0.45
    if source == "l1_episodic" and "summary" in intents:
        return 0.1
    if source == "l2_event" and "summary" in intents:
        return 0.05
    return 0.0


def _rank_score(
    base_score: float, source: str, created_at: datetime | None, intents: set[str]
) -> float:
    """Rank an item with intent relevance plus freshness.

    结合意图匹配与新鲜度对结果进行排序。
    """
    return base_score + _intent_bonus(source, intents) + _freshness_bonus(created_at)


def _rank_verified_score(
    base_score: float,
    source: str,
    created_at: datetime | None,
    verified_at: datetime | None,
    intents: set[str],
) -> float:
    """Rank a verified item with decay, intent relevance, and freshness.

    对已验证结果结合衰减、意图和新鲜度进行排序。
    """
    return (
        base_score * _verification_decay(verified_at)
        + _intent_bonus(source, intents)
        + _freshness_bonus(created_at)
    )


def _with_identity_filters(query, model, request) -> Any:
    """Apply tenant, user, and scope filters to a query.

    将 tenant、user 与 scope 身份过滤条件应用到查询中。
    """
    if request.tenant_id is not None:
        query = query.where(model.tenant_id == request.tenant_id)
    if request.user_id is not None:
        query = query.where(model.user_id == request.user_id)
    if request.scope is not None:
        query = query.where(model.scope == request.scope)
    return query


def _count_rows(session: Session, statement) -> int:
    """Count rows produced by a selectable statement.

    统计一个可查询语句返回的记录数。
    """
    return int(
        session.exec(select(func.count()).select_from(statement.subquery())).one()
    )


def _messages_to_content(messages: list[dict[str, str]]) -> str:
    """Flatten source messages into memory text.

    将来源消息展平成记忆文本。
    """
    return "\n".join(
        f"{message.get('role', 'user')}: {message.get('content', '')}"
        for message in messages
        if message.get("content")
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
    responses={
        200: {
            "description": "Memory was remembered successfully. / 记忆已成功写入。",
            **TURN_WRITE_RESPONSE_EXAMPLE,
        }
    },
)
async def write_memory(
    request: MemsWriteRequest = Body(openapi_examples={"basic": WRITE_MEMORY_EXAMPLE}),
    redis: RedisService = Depends(get_redis_service),
    session: Session = Depends(get_session),
):
    """Remember content into L0 and persist a new L1 record.

    将记忆写入 L0，并落一条新的 L1 记录。
    """
    try:
        new_messages = [message.model_dump() for message in request.messages]
        memory_content = request.content or _messages_to_content(new_messages)
        l0 = await redis.append_messages(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            messages=new_messages,
            content=memory_content,
            ttl_seconds=request.ttl_seconds,
            scope=request.scope,
            max_buffer_size=DEFAULT_CONTEXT_WINDOW,
        )

        l1_id = await sync_l0_to_l1(
            l0_data=l0.model_copy(update={"short_term_buffer": new_messages}),
            session=session,
            importance_score=0.5,
        )
        persisted = l1_id is not None

        return MemsWriteResponse(
            success=True,
            tenant_id=l0.tenant_id,
            user_id=l0.user_id,
            agent_id=l0.agent_id,
            session_id=l0.session_id,
            scope=l0.scope,
            content=memory_content,
            messages=new_messages,
            appended_count=len(new_messages),
            persisted_to_l1=persisted,
            l1_id=l1_id,
            message=(
                "Memory remembered and persisted to L1"
                if persisted
                else "Memory remembered in L0"
            ),
        )
    except Exception as e:
        logger.error(f"Failed to write memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/query",
    response_model=MemsQueryResponse,
    summary="Query memory / 记忆检索",
    description=(
        "Search across L0 hot cache, L1 episodic, and L2 semantic memory. L0 recent memory and active L1 are searched alongside L2 distilled knowledge. "
        "Archived L1 records are excluded from default online recall. "
        "Use this endpoint when the caller needs to recall relevant memories.\n\n"
        "跨 L0 热缓存、L1 情景记忆和 L2 语义记忆进行检索。L0 近期记忆与活跃 L1 一同参与检索，并连同 L2 蒸馏知识返回。"
        "默认不会召回已归档的 L1 记录。"
        "调用方需要召回相关记忆时使用该接口。"
    ),
    responses={
        200: {
            "description": "Ranked hybrid memory results. / 排序后的混合记忆结果。",
            **SEARCH_MEMORY_RESPONSE_EXAMPLE,
        }
    },
)
async def search_memory(
    request: MemsQueryRequest = Body(openapi_examples={"basic": SEARCH_MEMORY_EXAMPLE}),
    redis: RedisService = Depends(get_redis_service),
    session: Session = Depends(get_session),
):
    """Search memory across L0 hot cache, L1 episodic, and L2 semantic layers.

    在 L0 热缓存、L1 情景层和 L2 语义层之间执行统一混合检索。
    """
    try:
        query_intents = _detect_query_intents(request.query)

        # ---- L0 hot memory cache ----
        l0 = await redis.read(
            request.agent_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            scope=request.scope,
        )
        results: list[QueryResultItem] = []
        if l0 and l0.content:
            l0_score = _keyword_score(request.query, l0.content, 0.7)
            if l0_score > 0:
                results.append(
                    QueryResultItem(
                        source="l0_hot",
                        content=l0.content,
                        score=l0_score,
                        session_id=l0.session_id,
                        tenant_id=l0.tenant_id,
                        user_id=l0.user_id,
                        scope=l0.scope,
                    )
                )

        # ---- L1 + L2 ----
        vector_service = await get_vector_service()
        embedding_service = await get_embedding_service()

        embeddings = await embedding_service.embed([request.query])
        query_vector = embeddings[0]

        try:
            vector_results = await vector_service.search(
                collection_name=f"agent_{request.agent_id}",
                query_vector=query_vector,
                top_k=request.top_k * 3,
                filter_agent_id=request.agent_id,
                filters={
                    key: value
                    for key, value in {
                        "tenant_id": request.tenant_id,
                        "user_id": request.user_id,
                        "scope": request.scope,
                    }.items()
                    if value is not None
                },
            )
        except Exception as exc:
            logger.warning(
                f"Vector search unavailable for agent {request.agent_id}: {exc}"
            )
            vector_results = []

        summary_vector_scores = {
            (payload.get("vector_id") or result.get("id")): result["score"]
            for result in vector_results
            for payload in [result.get("payload") or {}]
            if payload.get("memory_type") == "l2_summary"
        }

        candidate_limit = max(
            request.top_k * SEARCH_CANDIDATE_LIMIT_MULTIPLIER,
            SEARCH_MIN_CANDIDATE_LIMIT,
        )

        seen_l1_ids = set()
        vector_ids = []

        for result in vector_results:
            payload = result.get("payload") or {}
            vector_id = payload.get("vector_id") or result.get("id")
            if vector_id is None:
                continue
            vector_ids.append(str(vector_id))

        l1_records_by_vector_id: dict[str, MemsL1Episodic] = {}
        if vector_ids:
            l1_query = select(MemsL1Episodic).where(
                MemsL1Episodic.agent_id == request.agent_id,
                MemsL1Episodic.vector_id.in_(vector_ids),
                MemsL1Episodic.is_archived == False,  # noqa: E712
            )
            l1_records = session.exec(
                _with_identity_filters(l1_query, MemsL1Episodic, request)
            ).all()
            l1_records_by_vector_id = {
                record.vector_id: record for record in l1_records if record.vector_id
            }

        for result in vector_results:
            payload = result.get("payload") or {}
            vector_id = payload.get("vector_id") or result.get("id")
            if vector_id is None:
                continue
            l1_record = l1_records_by_vector_id.get(str(vector_id))
            if not l1_record or l1_record.id in seen_l1_ids:
                continue

            seen_l1_ids.add(l1_record.id)
            results.append(
                QueryResultItem(
                    source="l1_episodic",
                    content=l1_record.content,
                    score=_rank_score(
                        result["score"],
                        "l1_episodic",
                        l1_record.created_at,
                        query_intents,
                    ),
                    source_l1_ids=[l1_record.id] if l1_record.id is not None else [],
                    session_id=l1_record.session_id,
                    created_at=l1_record.created_at,
                    tenant_id=l1_record.tenant_id,
                    user_id=l1_record.user_id,
                    scope=l1_record.scope,
                )
            )

        profile_items = session.exec(
            _with_identity_filters(
                select(MemsL2ProfileItem)
                .where(
                    MemsL2ProfileItem.agent_id == request.agent_id,
                    MemsL2ProfileItem.status == "active",
                )
                .order_by(MemsL2ProfileItem.last_verified_at.desc())
                .limit(candidate_limit),
                MemsL2ProfileItem,
                request,
            )
        ).all()
        for item in profile_items:
            results.append(
                QueryResultItem(
                    source="l2_profile",
                    content=f"{item.category} {item.key} {item.value}",
                    score=_rank_verified_score(
                        _keyword_score(
                            request.query,
                            f"{item.category} {item.key} {item.value}",
                            item.confidence,
                        ),
                        "l2_profile",
                        item.last_verified_at,
                        item.last_verified_at,
                        query_intents,
                    ),
                    source_l1_ids=item.source_l1_ids,
                    created_at=item.last_verified_at,
                    tenant_id=item.tenant_id,
                    user_id=item.user_id,
                    scope=item.scope,
                )
            )

        fact_items = session.exec(
            _with_identity_filters(
                select(MemsL2Fact)
                .where(
                    MemsL2Fact.agent_id == request.agent_id,
                    MemsL2Fact.status == "active",
                )
                .order_by(MemsL2Fact.last_verified_at.desc())
                .limit(candidate_limit),
                MemsL2Fact,
                request,
            )
        ).all()
        for item in fact_items:
            fact_text = f"{item.subject} {item.predicate} {item.object}"
            results.append(
                QueryResultItem(
                    source="l2_fact",
                    content=fact_text,
                    score=_rank_verified_score(
                        _keyword_score(request.query, fact_text, item.confidence),
                        "l2_fact",
                        item.last_verified_at,
                        item.last_verified_at,
                        query_intents,
                    ),
                    source_l1_ids=item.source_l1_ids,
                    created_at=item.last_verified_at,
                    tenant_id=item.tenant_id,
                    user_id=item.user_id,
                    scope=item.scope,
                )
            )

        event_items = session.exec(
            _with_identity_filters(
                select(MemsL2Event)
                .where(MemsL2Event.agent_id == request.agent_id)
                .order_by(MemsL2Event.created_at.desc())
                .limit(candidate_limit),
                MemsL2Event,
                request,
            )
        ).all()
        for item in event_items:
            event_text = f"{item.subject} {item.action} {item.object}"
            results.append(
                QueryResultItem(
                    source="l2_event",
                    content=event_text,
                    score=_rank_score(
                        _keyword_score(
                            request.query,
                            event_text,
                            min(item.importance_score / 10.0, 1.0),
                        ),
                        "l2_event",
                        item.created_at,
                        query_intents,
                    ),
                    source_l1_ids=item.source_l1_ids,
                    created_at=item.created_at,
                    tenant_id=item.tenant_id,
                    user_id=item.user_id,
                    scope=item.scope,
                )
            )

        summary_items = session.exec(
            _with_identity_filters(
                select(MemsL2Summary)
                .where(MemsL2Summary.agent_id == request.agent_id)
                .order_by(MemsL2Summary.last_verified_at.desc())
                .limit(candidate_limit),
                MemsL2Summary,
                request,
            )
        ).all()
        for item in summary_items:
            summary_score = _keyword_score(request.query, item.content, 0.6)
            if item.vector_id and item.vector_id in summary_vector_scores:
                summary_score = max(
                    summary_score, summary_vector_scores[item.vector_id] + 0.15
                )
            results.append(
                QueryResultItem(
                    source="l2_summary",
                    content=item.content,
                    score=_rank_verified_score(
                        summary_score,
                        "l2_summary",
                        item.created_at,
                        item.last_verified_at,
                        query_intents,
                    ),
                    source_l1_ids=item.source_l1_ids,
                    created_at=item.created_at,
                    tenant_id=item.tenant_id,
                    user_id=item.user_id,
                    scope=item.scope,
                )
            )

        results.sort(key=lambda item: item.score, reverse=True)
        results = results[: request.top_k]

        return MemsQueryResponse(
            query=request.query,
            results=results,
            total=len(results),
        )
    except Exception as e:
        logger.error(f"Failed to search memories: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/status",
    response_model=MonitorStatusResponse,
    summary="System status / 系统状态",
    description=(
        "Return component health checks together with memory-pipeline metrics such as pending distillation, pending archive, and stale knowledge counts.\n\n"
        "返回组件健康检查结果，以及记忆流水线指标，例如待蒸馏数量、待归档数量和陈旧知识数量。"
    ),
    responses={
        200: {
            "description": "Component health checks and pipeline metrics. / 组件健康检查与流水线指标。",
            **MEMS_STATUS_RESPONSE_EXAMPLE,
        }
    },
)
async def mems_status(
    redis: RedisService = Depends(get_redis_service),
    vector_service=Depends(get_vector_service),
    session: Session = Depends(get_session),
) -> MonitorStatusResponse:
    """Return health checks and pipeline metrics for the system.

    返回系统健康检查结果以及流水线关键指标。
    """
    checks = {}

    try:
        session.exec(select(MemsL1Episodic).limit(1)).all()
        checks["database"] = HealthCheckItem(status="healthy")
    except Exception as exc:
        checks["database"] = HealthCheckItem(status="unhealthy", detail=str(exc))

    try:
        client = await redis.get_client()
        ping_result = client.ping()
        if inspect.isawaitable(ping_result):
            await ping_result
        checks["redis"] = HealthCheckItem(status="healthy")
    except Exception as exc:
        checks["redis"] = HealthCheckItem(status="unhealthy", detail=str(exc))

    try:
        await vector_service.get_collections()
        checks["qdrant"] = HealthCheckItem(status="healthy")
    except Exception as exc:
        checks["qdrant"] = HealthCheckItem(status="unhealthy", detail=str(exc))

    try:
        scheduler = scheduler_service.scheduler
        details = []
        if not scheduler.running:
            details.append("scheduler stopped")
        if scheduler.get_job("distill_job") is None:
            details.append("distill job missing")
        if scheduler.get_job("archive_job") is None:
            details.append("archive job missing")

        checks["scheduler"] = HealthCheckItem(
            status="healthy" if not details else "degraded",
            detail=", ".join(details) or None,
        )
    except Exception as exc:
        checks["scheduler"] = HealthCheckItem(status="unhealthy", detail=str(exc))

    # Pipeline metrics use the injected session
    pending_distill = _count_rows(
        session,
        select(MemsL1Episodic).where(MemsL1Episodic.is_distilled == False),  # noqa: E712
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.ARCHIVE_DAYS)
    pending_archive = _count_rows(
        session,
        select(MemsL1Episodic).where(
            MemsL1Episodic.created_at < cutoff,
            MemsL1Episodic.is_archived == False,  # noqa: E712
        ),
    )
    profile_items = _count_rows(
        session,
        select(MemsL2ProfileItem).where(MemsL2ProfileItem.status == "active"),
    )
    fact_items = _count_rows(
        session,
        select(MemsL2Fact).where(MemsL2Fact.status == "active"),
    )
    summary_items = _count_rows(session, select(MemsL2Summary))
    conflict_count = _count_rows(session, select(MemsL2ConflictLog))
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_MEMORY_DAYS)
    stale_profile_items = _count_rows(
        session,
        select(MemsL2ProfileItem).where(
            MemsL2ProfileItem.status == "active",
            MemsL2ProfileItem.last_verified_at < stale_cutoff,
        ),
    )
    stale_fact_items = _count_rows(
        session,
        select(MemsL2Fact).where(
            MemsL2Fact.status == "active",
            MemsL2Fact.last_verified_at < stale_cutoff,
        ),
    )
    stale_summary_items = _count_rows(
        session,
        select(MemsL2Summary).where(MemsL2Summary.last_verified_at < stale_cutoff),
    )

    statuses = {item.status for item in checks.values()}
    if "unhealthy" in statuses:
        overall_status = "unhealthy"
    elif "degraded" in statuses:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    return MonitorStatusResponse(
        status=overall_status,
        version="0.1.0",
        timestamp=datetime.now(timezone.utc),
        checks=checks,
        pipeline=MonitorPipelineStatus(
            pending_distill=pending_distill,
            pending_archive=pending_archive,
            recent_failures=0,
            profile_items=profile_items,
            fact_items=fact_items,
            summary_items=summary_items,
            conflict_count=conflict_count,
            stale_profile_items=stale_profile_items,
            stale_fact_items=stale_fact_items,
            stale_summary_items=stale_summary_items,
        ),
    )


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Return a lightweight process health response.

    返回轻量级进程健康状态。
    """
    return {"status": "healthy", "version": "0.1.0"}
