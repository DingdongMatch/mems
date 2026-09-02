"""System status and monitoring application service."""

from __future__ import annotations

from datetime import timedelta

from mems.application.commands import (
    HealthCheckItem,
    MonitorPipelineStatus,
    MonitorStatusResponse,
)
from mems.config import settings
from mems.domain.ports import (
    Clock,
    EpisodicRepository,
    HealthCheckPort,
    KnowledgeRepository,
    SchedulerPort,
    VectorStorePort,
    WorkingMemoryStore,
)


STALE_MEMORY_DAYS = 365


class StatusApplicationService:
    """Aggregate component health checks and pipeline metrics."""

    def __init__(
        self,
        episodic: EpisodicRepository,
        knowledge: KnowledgeRepository,
        store: WorkingMemoryStore,
        vector: VectorStorePort,
        scheduler: SchedulerPort,
        clock: Clock,
        health: HealthCheckPort,
    ) -> None:
        self._episodic = episodic
        self._knowledge = knowledge
        self._store = store
        self._vector = vector
        self._scheduler = scheduler
        self._clock = clock
        self._health = health

    async def status(self) -> MonitorStatusResponse:
        checks: dict[str, HealthCheckItem] = {}

        try:
            self._health.healthcheck()
            checks["database"] = HealthCheckItem(status="healthy")
        except Exception as exc:
            checks["database"] = HealthCheckItem(status="unhealthy", detail=str(exc))

        try:
            await self._store.ping()
            checks["redis"] = HealthCheckItem(status="healthy")
        except Exception as exc:
            checks["redis"] = HealthCheckItem(status="unhealthy", detail=str(exc))

        try:
            await self._vector.get_collections()
            checks["qdrant"] = HealthCheckItem(status="healthy")
        except Exception as exc:
            checks["qdrant"] = HealthCheckItem(status="unhealthy", detail=str(exc))

        try:
            details = []
            if not self._scheduler.is_running():
                details.append("scheduler stopped")
            if not self._scheduler.has_archive_job():
                details.append("archive job missing")
            checks["scheduler"] = HealthCheckItem(
                status="healthy" if not details else "degraded",
                detail=", ".join(details) or None,
            )
        except Exception as exc:
            checks["scheduler"] = HealthCheckItem(
                status="unhealthy", detail=str(exc)
            )

        now = self._clock.now()
        cutoff = now - timedelta(days=settings.ARCHIVE_DAYS)
        stale_cutoff = now - timedelta(days=STALE_MEMORY_DAYS)

        pipeline = MonitorPipelineStatus(
            pending_distill=self._episodic.count_pending_distill(),
            pending_archive=self._episodic.count_pending_archive(cutoff),
            recent_failures=0,
            l0_pipeline_enabled=settings.L0_PIPELINE_ENABLED,
            profile_items=self._knowledge.count_active_profiles(),
            fact_items=self._knowledge.count_active_facts(),
            summary_items=self._knowledge.count_summaries(),
            conflict_count=self._knowledge.count_conflicts(),
            stale_profile_items=self._knowledge.count_stale_profiles(stale_cutoff),
            stale_fact_items=self._knowledge.count_stale_facts(stale_cutoff),
            stale_summary_items=self._knowledge.count_stale_summaries(stale_cutoff),
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
            timestamp=now,
            checks=checks,
            pipeline=pipeline,
        )
