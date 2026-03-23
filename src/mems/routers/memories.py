import logging
import re

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


def _keyword_score(query: str, text: str, base_score: float) -> float:
    query_terms = {term for term in re.split(r"\W+", query.lower()) if term}
    text_terms = {term for term in re.split(r"\W+", text.lower()) if term}
    overlap = len(query_terms & text_terms)
    if not query_terms:
        return base_score
    return base_score + overlap / max(len(query_terms), 1)


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
                    score=result["score"],
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
                    score=_keyword_score(
                        request.query,
                        f"{item.category} {item.key} {item.value}",
                        item.confidence,
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
                    score=_keyword_score(request.query, fact_text, item.confidence),
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
                    score=_keyword_score(
                        request.query,
                        event_text,
                        min(item.importance_score / 10.0, 1.0),
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
            results.append(
                SearchResultItem(
                    source="l2_summary",
                    content=item.content,
                    score=_keyword_score(request.query, item.content, 0.6),
                    metadata={
                        "summary_type": item.summary_type,
                        "source_ids": item.source_l1_ids,
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
