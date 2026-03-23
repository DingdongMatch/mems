from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mems.database import get_session
from mems.models import MemsL1Episodic, MemsL2Semantic
from mems.schemas import SearchRequest, SearchResponse, SearchResultItem
from mems.services.embedding import get_embedding_service
from mems.services.vector_service import get_vector_service


router = APIRouter(prefix="/search", tags=["Search"])


@router.post("", response_model=SearchResponse)
async def search_memory(
    request: SearchRequest,
    session: Session = Depends(get_session),
):
    """混合检索接口 - 向量 + L2 语义知识"""
    try:
        vector_service = await get_vector_service()
        embedding_service = await get_embedding_service()

        embeddings = await embedding_service.embed([request.query])
        query_vector = embeddings[0]

        vector_results = await vector_service.search(
            collection_name=f"agent_{request.agent_id}",
            query_vector=query_vector,
            top_k=request.top_k,
            filter_agent_id=request.agent_id,
        )

        results = []
        for r in vector_results:
            payload = r.get("payload") or {}
            results.append(
                SearchResultItem(
                    source="l1_episodic",
                    content=payload.get("content", ""),
                    score=r["score"],
                    metadata={"vector_id": r["id"]},
                )
            )

        if request.include_l2:
            l2_results = session.exec(
                select(MemsL2Semantic).where(
                    MemsL2Semantic.agent_id == request.agent_id,
                    MemsL2Semantic.is_active == True,
                )
            ).all()
            for r in l2_results:
                results.append(
                    SearchResultItem(
                        source="l2_semantic",
                        content=f"{r.subject} {r.predicate} {r.object}",
                        score=r.confidence,
                        metadata={"subject": r.subject, "predicate": r.predicate, "object": r.object},
                        created_at=r.updated_at,
                    )
                )

        results.sort(key=lambda x: x.score, reverse=True)
        results = results[:request.top_k]

        return SearchResponse(
            query=request.query,
            results=results,
            total=len(results),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))