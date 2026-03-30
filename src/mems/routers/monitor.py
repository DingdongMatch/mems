from datetime import datetime, timedelta, timezone
import inspect

from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import Session, select

from mems.config import settings
from mems.database import engine
from mems.models import (
    MemsL1Episodic,
    MemsL2ConflictLog,
    MemsL2Fact,
    MemsL2ProfileItem,
    MemsL2Summary,
)
from mems.schemas import (
    HealthCheckItem,
    MonitorPipelineStatus,
    MonitorStatusResponse,
)
from mems.services.redis_service import get_redis_service
from mems.services.scheduler import scheduler_service
from mems.services.vector_service import get_vector_service


router = APIRouter(prefix="/monitor", tags=["Monitor"])

STALE_MEMORY_DAYS = 365


def _count_rows(session: Session, statement) -> int:
    return int(
        session.exec(select(func.count()).select_from(statement.subquery())).one()
    )


@router.get("/status", response_model=MonitorStatusResponse)
async def monitor_status() -> MonitorStatusResponse:
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
