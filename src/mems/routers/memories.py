import logging
import re
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func
from sqlmodel import Session, select

from mems.config import settings
from mems.database import engine, get_session
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
    MemsContextRequest,
    MemsContextResponse,
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
    "summary": "Append user and assistant turns / 按轮次追加消息",
    "value": {
        "tenant_id": "tenant_demo",
        "user_id": "user_alice",
        "agent_id": "support_agent",
        "session_id": "session_20260401_001",
        "scope": "private",
        "messages": [
            {
                "role": "user",
                "content": "Remember that I like Rust and concise answers.",
            },
            {"role": "assistant", "content": "Stored. I will keep replies concise."},
        ],
        "active_plan": "Collect user preferences and prepare the next reply.",
        "temp_variables": {"ticket_id": "T-1001", "step": "collect_preferences"},
        "ttl_seconds": 1800,
        "metadata": {"source": "swagger_example", "channel": "web"},
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

TURN_WRITE_EXAMPLE = {
    "summary": "Append user and assistant turns / 按轮次追加消息",
    "value": {
        "tenant_id": "tenant_demo",
        "user_id": "user_alice",
        "agent_id": "support_agent",
        "session_id": "session_20260401_001",
        "scope": "private",
        "messages": [
            {"role": "user", "content": "Please answer in Chinese."},
            {"role": "assistant", "content": "好的，我会继续使用中文回答。"},
        ],
        "active_plan": "Continue the support conversation in Chinese.",
        "temp_variables": {"language": "zh-CN"},
        "ttl_seconds": 1800,
        "metadata": {"source": "swagger_example"},
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
                "short_term_buffer": [
                    {
                        "role": "user",
                        "content": "Remember that I like Rust and concise answers.",
                    },
                    {
                        "role": "assistant",
                        "content": "Stored. I will keep replies concise.",
                    },
                ],
                "active_plan": "Collect user preferences and prepare the next reply.",
                "temp_variables": {
                    "ticket_id": "T-1001",
                    "step": "collect_preferences",
                },
                "persisted_to_l1": True,
                "l1_id": 101,
                "message": "Memory written and persisted to L1",
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
                        "metadata": {
                            "category": "like",
                            "key": "technology",
                            "value": "Rust",
                            "source_ids": [101],
                        },
                        "created_at": "2026-04-01T10:00:00Z",
                        "tenant_id": "tenant_demo",
                        "user_id": "user_alice",
                        "scope": "private",
                    },
                    {
                        "source": "l1_episodic",
                        "content": "user: Remember that I like Rust and concise answers.",
                        "score": 1.14,
                        "metadata": {
                            "l1_id": 101,
                            "vector_id": "vec_101",
                            "session_id": "session_20260401_001",
                        },
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

CONTEXT_RESPONSE_EXAMPLE = {
    "content": {
        "application/json": {
            "example": {
                "tenant_id": "tenant_demo",
                "user_id": "user_alice",
                "agent_id": "support_agent",
                "session_id": "session_20260401_001",
                "scope": "private",
                "source": "mixed",
                "page_type": "live",
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
                "total": 2,
                "has_more": False,
                "next_before_id": None,
                "expires_at": "2026-04-01T10:30:00Z",
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
                "short_term_buffer": [
                    {"role": "user", "content": "Please answer in Chinese."},
                    {"role": "assistant", "content": "好的，我会继续使用中文回答。"},
                ],
                "appended_count": 2,
                "persisted_to_l1": True,
                "l1_id": 102,
                "message": "Turns appended and persisted to L1",
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


def _parse_l1_content_to_messages(content: str) -> list[dict[str, str]]:
    """Parse stored L1 text back into role/content messages.

    将存储在 L1 中的文本重新解析为 role/content 消息。
    """
    messages: list[dict[str, str]] = []
    for line in content.splitlines():
        if ": " in line:
            role, text = line.split(": ", 1)
            if role and text:
                messages.append({"role": role, "content": text})
        elif line.strip():
            messages.append({"role": "system", "content": line.strip()})
    return messages


def _extract_l1_messages(record: MemsL1Episodic) -> list[dict[str, str]]:
    """Extract normalized message objects from one L1 record.

    从单条 L1 记录中提取标准化消息对象。
    """
    metadata_messages = (record.metadata_json or {}).get("messages")
    if isinstance(metadata_messages, list):
        normalized_messages = []
        for item in metadata_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if role and content:
                normalized_messages.append({"role": role, "content": content})
        if normalized_messages:
            return normalized_messages
    return _parse_l1_content_to_messages(record.content)


def _merge_live_messages(
    history_messages: list[dict[str, str]], live_messages: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Merge L1 history and live L0 messages without duplicating overlap.

    合并 L1 历史与 L0 实时消息，并避免重复重叠片段。
    """
    if not history_messages:
        return list(live_messages)
    if not live_messages:
        return list(history_messages)

    if len(live_messages) >= len(history_messages):
        for start in range(len(live_messages) - len(history_messages) + 1):
            if live_messages[start : start + len(history_messages)] == history_messages:
                return list(live_messages)

    max_overlap = min(len(history_messages), len(live_messages))
    for overlap in range(max_overlap, 0, -1):
        if history_messages[-overlap:] == live_messages[:overlap]:
            return history_messages + live_messages[overlap:]
    return history_messages + live_messages


def _expand_l1_page(
    records: list[MemsL1Episodic], page_limit: int
) -> tuple[list[dict[str, str]], bool, int | None]:
    """Expand an L1 page of records into flat message history.

    将一页 L1 记录展开为扁平化消息历史。
    """
    has_more = len(records) > page_limit
    page_records = records[:page_limit]

    page_records = list(reversed(page_records))
    messages: list[dict[str, str]] = []
    for record in page_records:
        messages.extend(_extract_l1_messages(record))

    next_before_id = page_records[0].id if has_more and page_records else None
    return messages, has_more, next_before_id


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


@router.post(
    "/write",
    response_model=MemsWriteResponse,
    summary="Write memory turns / 标准消息写入",
    description=(
        "Append user and assistant turns into live L0 memory and persist the primary SQL record into L1. "
        "This is the standard write endpoint for third-party agent integrations.\n\n"
        "把 user / assistant 消息追加到实时 L0 记忆，并持久化主 L1 SQL 记录。"
        "这是第三方 Agent 标准写入接口。"
    ),
    responses={
        200: {
            "description": "Turns were appended successfully. / 会话轮次已成功追加。",
            **TURN_WRITE_RESPONSE_EXAMPLE,
        }
    },
)
async def write_memory(
    request: MemsWriteRequest = Body(openapi_examples={"basic": WRITE_MEMORY_EXAMPLE}),
    redis: RedisService = Depends(get_redis_service),
    session: Session = Depends(get_session),
):
    """Append chat turns into L0 and persist a new L1 record.

    按轮次向 L0 追加消息，并落一条新的 L1 记录。
    """
    try:
        new_messages = [message.model_dump() for message in request.messages]
        l0 = await redis.append_messages(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            messages=new_messages,
            ttl_seconds=request.ttl_seconds,
            scope=request.scope,
            active_plan=request.active_plan,
            temp_variables=request.temp_variables,
            max_buffer_size=DEFAULT_CONTEXT_WINDOW,
        )

        l1_id = await sync_l0_to_l1(
            l0_data=l0.model_copy(update={"short_term_buffer": new_messages}),
            session=session,
            importance_score=0.5,
            metadata={
                "append_mode": "turns",
                "ttl_seconds": request.ttl_seconds,
                "messages": new_messages,
                **request.metadata,
            },
        )
        persisted = l1_id is not None

        return MemsWriteResponse(
            success=True,
            tenant_id=l0.tenant_id,
            user_id=l0.user_id,
            agent_id=l0.agent_id,
            session_id=l0.session_id,
            scope=l0.scope,
            short_term_buffer=l0.short_term_buffer,
            appended_count=len(new_messages),
            persisted_to_l1=persisted,
            l1_id=l1_id,
            message=(
                "Turns appended and persisted to L1"
                if persisted
                else "Turns appended to L0"
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
        "Search active L1 episodic memory together with L2 semantic memory. Archived L1 records are excluded from default online recall. "
        "Use this endpoint after loading context and before generating the next answer.\n\n"
        "同时检索活跃的 L1 情景记忆和 L2 语义记忆。默认不会召回已归档的 L1 记录。"
        "建议在读取上下文后、生成回答前调用该接口。"
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
    session: Session = Depends(get_session),
):
    """Search active memory across L1 episodic and L2 semantic layers.

    在 L1 情景层和 L2 语义层之间执行统一混合检索。
    """
    try:
        query_intents = _detect_query_intents(request.query)
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

        results = []
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
                    metadata={
                        "l1_id": l1_record.id,
                        "vector_id": l1_record.vector_id,
                        "session_id": l1_record.session_id,
                    },
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
                    metadata={
                        "category": item.category,
                        "key": item.key,
                        "value": item.value,
                        "source_ids": item.source_l1_ids,
                    },
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
                    metadata={
                        "fact_type": item.fact_type,
                        "subject": item.subject,
                        "predicate": item.predicate,
                        "object": item.object,
                        "source_ids": item.source_l1_ids,
                    },
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
                    metadata={
                        "time_hint": item.time_hint,
                        "source_ids": item.source_l1_ids,
                    },
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
                    metadata={
                        "summary_type": item.summary_type,
                        "source_ids": item.source_l1_ids,
                        "vector_id": item.vector_id,
                    },
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
    "/context",
    response_model=MemsContextResponse,
    summary="Get paginated session context / 获取分页会话上下文",
    description=(
        "Return the live context page for a session. The first page merges Redis L0 live context with the latest L1 records when needed, "
        "while `before_id` loads older L1 history pages. The `limit` parameter is the number of L1 records per page.\n\n"
        "返回指定会话的实时上下文首页。首页会优先结合 Redis L0 与最新的 L1 记录；传入 `before_id` 时会继续加载更早的 L1 历史分页。"
        "`limit` 表示每页包含多少条 L1 记录。"
    ),
    responses={
        200: {
            "description": "Live or historical context page. / 实时或历史上下文分页结果。",
            **CONTEXT_RESPONSE_EXAMPLE,
        }
    },
)
async def get_memory_context(
    tenant_id: str | None = Query(
        default=None,
        description="Optional tenant boundary. / 可选租户边界。",
        examples=["tenant_demo"],
    ),
    user_id: str | None = Query(
        default=None,
        description="Optional user boundary. / 可选用户边界。",
        examples=["user_alice"],
    ),
    agent_id: str = Query(
        ...,
        description="Agent boundary for context isolation. / 用于上下文隔离的 Agent 边界。",
        examples=["support_agent"],
    ),
    session_id: str = Query(
        ...,
        description="Session id whose context should be loaded. / 需要加载上下文的 session id。",
        examples=["session_20260401_001"],
    ),
    scope: str | None = Query(
        default=None,
        description="Soft visibility tag for filtering. / 用于过滤的软可见性标签。",
        examples=["private"],
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Number of L1 records per page. / 每页返回的 L1 记录数。",
    ),
    before_id: int | None = Query(
        default=None,
        ge=1,
        description="History cursor for older L1 pages. / 更早 L1 历史页的游标。",
    ),
    redis: RedisService = Depends(get_redis_service),
    session: Session = Depends(get_session),
):
    """Return the live session context or an older L1 history page.

    返回当前会话的实时上下文，或更早的 L1 历史分页。
    """
    try:
        request = MemsContextRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            scope=scope,
            limit=limit,
            before_id=before_id,
        )

        l1_query = select(MemsL1Episodic).where(
            MemsL1Episodic.agent_id == request.agent_id,
            MemsL1Episodic.session_id == request.session_id,
        )
        if request.before_id is not None:
            l1_query = l1_query.where(MemsL1Episodic.id < request.before_id)
        l1_records = session.exec(
            _with_identity_filters(
                l1_query.order_by(MemsL1Episodic.id.desc()).limit(request.limit + 1),
                MemsL1Episodic,
                request,
            )
        ).all()
        l1_messages, has_more, next_before_id = _expand_l1_page(
            l1_records, request.limit
        )

        if request.before_id is not None:
            return MemsContextResponse(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                agent_id=request.agent_id,
                session_id=request.session_id,
                scope=request.scope,
                source="l1",
                page_type="history",
                messages=l1_messages,
                total=len(l1_messages),
                has_more=has_more,
                next_before_id=next_before_id,
                expires_at=None,
            )

        l0 = await redis.read(
            request.agent_id,
            request.session_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            scope=request.scope,
        )
        live_messages = l0.short_term_buffer if l0 is not None else []
        messages = _merge_live_messages(l1_messages, live_messages)
        if live_messages and messages == live_messages:
            source = "l0"
        elif l1_messages and live_messages:
            source = "mixed"
        elif live_messages:
            source = "l0"
        else:
            source = "l1"

        return MemsContextResponse(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            scope=l0.scope if l0 is not None else request.scope,
            source=source,
            page_type="live",
            messages=messages,
            total=len(messages),
            has_more=has_more,
            next_before_id=next_before_id,
            expires_at=l0.expires_at if l0 is not None else None,
        )
    except Exception as e:
        logger.error(f"Failed to get memory context: {e}")
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
async def mems_status() -> MonitorStatusResponse:
    """Return health checks and pipeline metrics for the system.

    返回系统健康检查结果以及流水线关键指标。
    """
    checks = {}

    try:
        with Session(engine) as session:
            session.exec(select(MemsL1Episodic).limit(1)).all()
        checks["database"] = HealthCheckItem(status="healthy")
    except Exception as exc:
        checks["database"] = HealthCheckItem(status="unhealthy", detail=str(exc))

    try:
        redis = await get_redis_service()
        client = await redis.get_client()
        ping_result = client.ping()
        if inspect.isawaitable(ping_result):
            await ping_result
        checks["redis"] = HealthCheckItem(status="healthy")
    except Exception as exc:
        checks["redis"] = HealthCheckItem(status="unhealthy", detail=str(exc))

    try:
        vector_service = await get_vector_service()
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

    with Session(engine) as session:
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
