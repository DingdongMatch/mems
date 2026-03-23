import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mems.config import settings
from mems.database import get_session
from mems.models import (
    MemsL1Episodic,
    MemsL2Event,
    MemsL2Fact,
    MemsL2ProfileItem,
    MemsL2Summary,
)
from mems.schemas import (
    MemorySearchRequest,
    MemorySearchResponse,
    MemoryWriteRequest,
    MemoryWriteResponse,
    SearchResultItem,
)
from mems.services.embedding import get_embedding_service
from mems.services.l0_sync import sync_l0_to_l1
from mems.services.redis_service import RedisService, get_redis_service
from mems.services.vector_service import get_vector_service


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memories", tags=["Memories"])

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
    query_terms = {term for term in re.split(r"\W+", query.lower()) if term}
    text_terms = {term for term in re.split(r"\W+", text.lower()) if term}
    overlap = len(query_terms & text_terms)
    if not query_terms:
        return base_score
    return base_score + overlap / max(len(query_terms), 1)


def _detect_query_intents(query: str) -> set[str]:
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
    return base_score + _intent_bonus(source, intents) + _freshness_bonus(created_at)


def _rank_verified_score(
    base_score: float,
    source: str,
    created_at: datetime | None,
    verified_at: datetime | None,
    intents: set[str],
) -> float:
    return (
        base_score * _verification_decay(verified_at)
        + _intent_bonus(source, intents)
        + _freshness_bonus(created_at)
    )


@router.post("/write", response_model=MemoryWriteResponse)
async def write_memory(
    request: MemoryWriteRequest,
    redis: RedisService = Depends(get_redis_service),
    session: Session = Depends(get_session),
):
    """统一记忆写入入口。"""
    try:
        ttl_seconds = request.ttl_seconds or settings.L0_DEFAULT_TTL_SECONDS
        l0 = await redis.write(
            agent_id=request.agent_id,
            session_id=request.session_id,
            messages=request.messages,
            active_plan=request.active_plan,
            temp_variables=request.temp_variables,
            ttl_seconds=ttl_seconds,
        )

        l1_id = await sync_l0_to_l1(
            l0_data=l0,
            session=session,
            importance_score=0.5,
            metadata={
                "ttl_seconds": ttl_seconds,
                **request.metadata,
            },
        )
        persisted = l1_id is not None

        return MemoryWriteResponse(
            success=True,
            agent_id=l0.agent_id,
            session_id=l0.session_id,
            short_term_buffer=l0.short_term_buffer,
            active_plan=l0.active_plan,
            temp_variables=l0.temp_variables,
            persisted_to_l1=persisted,
            l1_id=l1_id,
            message=(
                "Memory written and persisted to L1"
                if persisted
                else "Memory written to L0 but L1 persistence is pending"
            ),
        )
    except Exception as e:
        logger.error(f"Failed to write memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search", response_model=MemorySearchResponse)
async def search_memory(
    request: MemorySearchRequest,
    session: Session = Depends(get_session),
):
    """统一记忆查询入口，内部固定执行 L1 + L2 混合检索。"""
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

        results = []
        seen_l1_ids = set()

        for result in vector_results:
            payload = result.get("payload") or {}
            vector_id = payload.get("vector_id") or result.get("id")
            l1_record = session.exec(
                select(MemsL1Episodic).where(
                    MemsL1Episodic.agent_id == request.agent_id,
                    MemsL1Episodic.vector_id == str(vector_id),
                )
            ).first()
            if not l1_record or l1_record.id in seen_l1_ids:
                continue

            seen_l1_ids.add(l1_record.id)
            results.append(
                SearchResultItem(
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
                )
            )

        profile_items = session.exec(
            select(MemsL2ProfileItem).where(
                MemsL2ProfileItem.agent_id == request.agent_id,
                MemsL2ProfileItem.status == "active",
            )
        ).all()
        for item in profile_items:
            results.append(
                SearchResultItem(
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
                )
            )

        fact_items = session.exec(
            select(MemsL2Fact).where(
                MemsL2Fact.agent_id == request.agent_id,
                MemsL2Fact.status == "active",
            )
        ).all()
        for item in fact_items:
            fact_text = f"{item.subject} {item.predicate} {item.object}"
            results.append(
                SearchResultItem(
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
                )
            )

        event_items = session.exec(
            select(MemsL2Event).where(MemsL2Event.agent_id == request.agent_id)
        ).all()
        for item in event_items:
            event_text = f"{item.subject} {item.action} {item.object}"
            results.append(
                SearchResultItem(
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
                )
            )

        summary_items = session.exec(
            select(MemsL2Summary).where(MemsL2Summary.agent_id == request.agent_id)
        ).all()
        for item in summary_items:
            summary_score = _keyword_score(request.query, item.content, 0.6)
            if item.vector_id and item.vector_id in summary_vector_scores:
                summary_score = max(
                    summary_score, summary_vector_scores[item.vector_id] + 0.15
                )
            results.append(
                SearchResultItem(
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
                )
            )

        results.sort(key=lambda item: item.score, reverse=True)
        results = results[: request.top_k]

        return MemorySearchResponse(
            query=request.query,
            results=results,
            total=len(results),
        )
    except Exception as e:
        logger.error(f"Failed to search memories: {e}")
        raise HTTPException(status_code=500, detail=str(e))
