"""Composition root for background services, triggers, and the outbox worker."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from mems.application.archive_service import ArchiveApplicationService
from mems.application.commands import RedistillResponse
from mems.application.distill_service import DistillApplicationService
from mems.application.redistill_service import (
    _as_utc,
    _record_created_at,
    content_fingerprint,
)
from mems.application.vector_sync_service import VectorSyncApplicationService
from mems.config import settings
from mems.domain.knowledge import DistillSource
from mems.domain.outbox import OutboxEvent, OutboxEventType
from mems.infrastructure.external.clock import get_clock
from mems.infrastructure.external.embedding import get_embedding_service
from mems.infrastructure.external.jsonl import JsonlReader
from mems.infrastructure.external.llm_client import get_llm_port
from mems.infrastructure.external.observability import bind_log_context
from mems.infrastructure.external.vector_service import get_vector_service
from mems.infrastructure.persistence.database import sql_session_factory
from mems.infrastructure.persistence.repositories import build_repositories
from mems.infrastructure.persistence.uow import SqlUnitOfWork


logger = logging.getLogger(__name__)

Handler = Callable[[Any, OutboxEvent], Any]


def build_distill_service(session) -> DistillApplicationService:
    """Build a distill service bound to ``session``."""
    repos = build_repositories(session)
    return DistillApplicationService(
        episodic=repos.episodic,
        knowledge=repos.knowledge,
        outbox=repos.outbox,
        llm=get_llm_port(),
        clock=get_clock(),
        uow=SqlUnitOfWork(session),
    )


async def make_distill_service(session) -> DistillApplicationService:
    """Async-friendly builder kept for existing callers and tests."""
    return build_distill_service(session)


def make_archive_service(session) -> ArchiveApplicationService:
    """Build an archive service bound to ``session``."""
    repos = build_repositories(session)
    return ArchiveApplicationService(
        episodic=repos.episodic,
        archive=repos.archive,
        clock=get_clock(),
        uow=SqlUnitOfWork(session),
    )


def build_vector_sync_service(
    session, embedding, vector
) -> VectorSyncApplicationService:
    """Build a vector-sync service bound to ``session``."""
    repos = build_repositories(session)
    return VectorSyncApplicationService(
        episodic=repos.episodic,
        knowledge=repos.knowledge,
        vector=vector,
        embedding=embedding,
        clock=get_clock(),
        uow=SqlUnitOfWork(session),
    )


async def trigger_archive_automatically(
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Enqueue archive jobs for expired L1 identities."""
    cutoff = get_clock().now() - timedelta(days=settings.ARCHIVE_DAYS)
    with sql_session_factory.create() as session:
        repos = build_repositories(session)
        if agent_id is None:
            identities = repos.episodic.list_archive_identities(cutoff)
            targets = [(t, u, a, s) for t, u, a, s in identities if a]
        else:
            targets = [(None, None, agent_id, None)]

    if not targets:
        logger.info(
            "Archive trigger found no expired records",
            extra={"event": "archive.trigger.empty", "operation": "archive_trigger"},
        )
        return {"triggered": False, "reason": "no expired records", "enqueued": 0}

    enqueued = 0
    for tenant_id, user_id, aid, namespace in targets:
        aggregate_id = f"{aid}:{tenant_id or '_'}:{user_id or '_'}:{namespace or '_'}"
        with sql_session_factory.create() as session:
            repos = build_repositories(session)
            if repos.outbox.exists_active(OutboxEventType.ARCHIVE_AGENT, aggregate_id):
                continue
            repos.outbox.add(
                OutboxEvent(
                    event_type=OutboxEventType.ARCHIVE_AGENT,
                    aggregate_id=aggregate_id,
                    payload={
                        "agent_id": aid,
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "namespace": namespace,
                        "days": settings.ARCHIVE_DAYS,
                    },
                )
            )
            SqlUnitOfWork(session).commit()
            enqueued += 1

    logger.info(
        "Archive trigger enqueued jobs",
        extra={
            "event": "archive.trigger.completed",
            "operation": "archive_trigger",
            "count": enqueued,
            "result": "success",
        },
    )
    return {"triggered": True, "reason": "scheduled", "enqueued": enqueued}


async def trigger_redistill(
    agent_id: Optional[str] = None,
    start_date=None,
    end_date=None,
    min_importance: float = 0.0,
    batch_size: Optional[int] = None,
) -> RedistillResponse:
    started = perf_counter()
    batch_size = batch_size or settings.REDISTILL_BATCH_SIZE
    model = settings.OPENAI_MODEL
    start_date = _as_utc(start_date) if start_date else None
    end_date = _as_utc(end_date) if end_date else None

    try:
        with sql_session_factory.create() as session:
            archives = build_repositories(session).archive.list(agent_id)

        if not archives:
            return RedistillResponse(
                success=True,
                processed=0,
                created=0,
                updated=0,
                skipped=0,
                message="No archive batches found",
            )

        reader = JsonlReader(settings.storage_l3_path)
        processed = created = updated = skipped = 0
        with sql_session_factory.create() as session:
            repos = build_repositories(session)
            service = build_distill_service(session)
            for archive in archives:
                path = Path(archive.file_path)
                if not path.exists():
                    logger.warning(
                        "Archive file is missing",
                        extra={
                            "event": "redistill.archive_missing",
                            "operation": "redistill",
                            "result": "skipped",
                        },
                    )
                    continue
                records = reader.read(path)
                for line_no, record in enumerate(records, start=1):
                    if processed >= batch_size:
                        break
                    if agent_id and record.get("agent_id") != agent_id:
                        continue
                    if (
                        min_importance > 0
                        and float(record.get("importance_score", 0.0)) < min_importance
                    ):
                        continue
                    record_created = _record_created_at(record)
                    if start_date and record_created < start_date:
                        continue
                    if end_date and record_created > end_date:
                        continue

                    fingerprint = content_fingerprint(record)
                    if repos.redistill.exists(fingerprint, model):
                        skipped += 1
                        continue
                    if not record.get("content"):
                        skipped += 1
                        continue

                    source = DistillSource.from_archive_record(
                        record, str(path), line_no
                    )
                    try:
                        with bind_log_context(
                            operation="redistill",
                            source_l1_id=source.source_id,
                        ):
                            created_in, updated_in = await service.process_source(
                                source
                            )
                    except Exception as exc:
                        logger.error(
                            "Re-distillation source failed",
                            exc_info=True,
                            extra={
                                "event": "redistill.source.failed",
                                "operation": "redistill",
                                "source_l1_id": source.source_id,
                                "error_type": type(exc).__name__,
                                "result": "failed",
                            },
                        )
                        continue

                    repos.redistill.add(fingerprint, model, str(path), line_no)
                    processed += 1
                    created += created_in
                    updated += updated_in
                if processed >= batch_size:
                    break
            SqlUnitOfWork(session).commit()

        logger.info(
            "Re-distillation run completed",
            extra={
                "event": "redistill.completed",
                "operation": "redistill",
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "count": processed,
                "l2_created": created,
                "l2_updated": updated,
                "skipped": skipped,
                "model": model,
                "result": "success",
            },
        )
        return RedistillResponse(
            success=True,
            processed=processed,
            created=created,
            updated=updated,
            skipped=skipped,
            message=(
                f"Re-distilled {processed} records with {model}"
                f" (created {created}, updated {updated}, skipped {skipped})"
            ),
        )
    except Exception as exc:
        logger.error(
            "Re-distillation run failed",
            exc_info=True,
            extra={
                "event": "redistill.failed",
                "operation": "redistill",
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "error_type": type(exc).__name__,
                "result": "failed",
            },
        )
        return RedistillResponse(
            success=False,
            processed=0,
            created=0,
            updated=0,
            skipped=0,
            message=str(exc),
        )


class OutboxWorker:
    """Claim and dispatch outbox events with idempotent retry."""

    def __init__(
        self,
        session_factory,
        claim_limit: int = 20,
        max_backoff_seconds: int = 3600,
    ) -> None:
        self._session_factory = session_factory
        self._claim_limit = claim_limit
        self._max_backoff_seconds = max_backoff_seconds

    async def _handle_vector_sync(self, session, event: OutboxEvent) -> None:
        embedding = await get_embedding_service()
        vector = await get_vector_service()
        await build_vector_sync_service(session, embedding, vector).sync(event)

    async def _handle_archive_agent(self, session, event: OutboxEvent) -> None:
        payload = event.payload
        await make_archive_service(session).archive(
            agent_id=payload["agent_id"],
            tenant_id=payload.get("tenant_id"),
            user_id=payload.get("user_id"),
            namespace=payload.get("namespace"),
            days=payload.get("days", settings.ARCHIVE_DAYS),
        )

    async def _handle_distill_agent(self, session, event: OutboxEvent) -> None:
        payload = event.payload
        await build_distill_service(session).distill(
            agent_id=payload["agent_id"],
            batch_size=payload.get("batch_size", settings.DISTILL_BATCH_SIZE),
            force=payload.get("force", False),
        )

    async def _handle_distill_due(self, session, event: OutboxEvent) -> None:
        del event
        repos = build_repositories(session)
        if repos.episodic.count_pending_distill() < settings.DISTILL_THRESHOLD:
            return
        for agent_id in repos.episodic.list_agents_with_pending_distill():
            if repos.outbox.exists_active(OutboxEventType.DISTILL_AGENT, agent_id):
                continue
            repos.outbox.add(
                OutboxEvent(
                    event_type=OutboxEventType.DISTILL_AGENT,
                    aggregate_id=agent_id,
                    payload={
                        "agent_id": agent_id,
                        "batch_size": settings.DISTILL_BATCH_SIZE,
                        "force": False,
                    },
                )
            )
        SqlUnitOfWork(session).commit()

    async def _dispatch(self, session, event: OutboxEvent) -> None:
        handlers: Dict[str, Handler] = {
            OutboxEventType.VECTOR_SYNC: self._handle_vector_sync,
            OutboxEventType.ARCHIVE_AGENT: self._handle_archive_agent,
            OutboxEventType.DISTILL_AGENT: self._handle_distill_agent,
            OutboxEventType.DISTILL_DUE: self._handle_distill_due,
        }
        handler = handlers.get(event.event_type)
        if handler is None:
            raise ValueError(f"Unknown outbox event type: {event.event_type}")
        await handler(session, event)

    async def run_once(self) -> int:
        owner = uuid4().hex
        now = get_clock().now()
        with self._session_factory.create() as session:
            events = build_repositories(session).outbox.claim_ready(
                owner, self._claim_limit, now
            )
            SqlUnitOfWork(session).commit()

        processed = 0
        for event in events:
            if event.id is None:
                continue
            event_started = perf_counter()
            with bind_log_context(
                outbox_event_id=event.id,
                outbox_type=event.event_type,
                operation="outbox_dispatch",
            ):
                logger.info(
                    "Outbox event processing started",
                    extra={"event": "outbox.event.started", "attempts": event.attempts},
                )
                try:
                    with self._session_factory.create() as session:
                        await self._dispatch(session, event)
                        build_repositories(session).outbox.mark_done(event.id)
                        SqlUnitOfWork(session).commit()
                    logger.info(
                        "Outbox event processing completed",
                        extra={
                            "event": "outbox.event.completed",
                            "attempts": event.attempts,
                            "duration_ms": round(
                                (perf_counter() - event_started) * 1000, 2
                            ),
                            "result": "success",
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "Outbox event processing failed",
                        exc_info=True,
                        extra={
                            "event": "outbox.event.failed",
                            "attempts": event.attempts,
                            "duration_ms": round(
                                (perf_counter() - event_started) * 1000, 2
                            ),
                            "error_type": type(exc).__name__,
                            "result": "retrying",
                        },
                    )
                    backoff = min(
                        2 ** max(event.attempts, 0), self._max_backoff_seconds
                    )
                    available_at = get_clock().now() + timedelta(seconds=backoff)
                    with self._session_factory.create() as session:
                        build_repositories(session).outbox.mark_failed(
                            event.id, str(exc), available_at
                        )
                        SqlUnitOfWork(session).commit()
            processed += 1
        return processed

    async def run_forever(self, poll_interval: float = 1.0) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception(
                    "Outbox worker loop failed",
                    extra={
                        "event": "outbox.worker.loop_failed",
                        "operation": "outbox_worker",
                    },
                )
            await asyncio.sleep(poll_interval)


def build_outbox_worker(session_factory=None) -> OutboxWorker:
    return OutboxWorker(session_factory or sql_session_factory)
