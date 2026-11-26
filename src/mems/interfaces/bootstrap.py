"""Composition root for background services, triggers, and the outbox worker."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from datetime import timedelta
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from mems.application.archive_service import ArchiveApplicationService
from mems.application.distill_service import DistillApplicationService
from mems.application.vector_sync_service import VectorSyncApplicationService
from mems.config import settings
from mems.domain.outbox import OutboxEvent, OutboxEventType, outbox_aggregate_key
from mems.domain.services.distillation import MIN_DISTILL_IMPORTANCE
from mems.infrastructure.external.clock import get_clock
from mems.infrastructure.external.embedding import get_embedding_service
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
        outbox=repos.outbox,
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
            identities = repos.episodic.list_archive_identities(cutoff)
            targets = [
                (t, u, a, s)
                for t, u, a, s in identities
                if a and a == agent_id
            ]

    if not targets:
        logger.info(
            "Archive trigger found no expired records",
            extra={"event": "archive.trigger.empty", "operation": "archive_trigger"},
        )
        return {"triggered": False, "reason": "no expired records", "enqueued": 0}

    enqueued = 0
    for tenant_id, user_id, aid, namespace in targets:
        aggregate_id = outbox_aggregate_key(
            OutboxEventType.ARCHIVE_AGENT,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=aid,
            namespace=namespace,
            resource="archive",
        )
        with sql_session_factory.create() as session:
            repos = build_repositories(session)
            event = repos.outbox.add(
                OutboxEvent(
                    event_type=OutboxEventType.ARCHIVE_AGENT,
                    aggregate_id=aggregate_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    agent_id=aid,
                    namespace=namespace,
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
            if event.id is not None:
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


class OutboxWorker:
    """Claim and dispatch outbox events with idempotent retry."""

    def __init__(
        self,
        session_factory,
        max_backoff_seconds: int = 3600,
    ) -> None:
        self._session_factory = session_factory
        self._max_backoff_seconds = max_backoff_seconds

    async def _handle_vector_sync(self, session, event: OutboxEvent):
        embedding = await get_embedding_service()
        vector = await get_vector_service()
        return await build_vector_sync_service(session, embedding, vector).sync(event)

    async def _handle_vector_delete(self, session, event: OutboxEvent) -> bool:
        del session
        vector = await get_vector_service()
        return await vector.delete_points(
            f"agent_{event.agent_id}", [event.payload["vector_id"]]
        )

    async def _handle_archive_agent(self, session, event: OutboxEvent):
        payload = event.payload
        return await make_archive_service(session).archive(
            agent_id=payload["agent_id"],
            tenant_id=payload.get("tenant_id"),
            user_id=payload.get("user_id"),
            namespace=payload.get("namespace"),
            days=payload.get("days", settings.ARCHIVE_DAYS),
            batch_id=f"outbox-{event.id}",
        )

    async def _handle_distill_agent(self, session, event: OutboxEvent) -> None:
        if settings.LLM_PROVIDER != "openai":
            return
        payload = event.payload
        result = await build_distill_service(session).distill(
            agent_id=payload["agent_id"],
            tenant_id=payload.get("tenant_id"),
            user_id=payload.get("user_id"),
            namespace=payload.get("namespace"),
            batch_size=payload.get("batch_size", settings.DISTILL_BATCH_SIZE),
            force=payload.get("force", False),
        )
        if not result.success:
            raise RuntimeError(result.message)
        repos = build_repositories(session)
        if not payload.get("force", False) and repos.episodic.has_pending_for_distill(
            payload["agent_id"],
            False,
            MIN_DISTILL_IMPORTANCE,
            tenant_id=payload.get("tenant_id"),
            user_id=payload.get("user_id"),
            namespace=payload.get("namespace"),
        ):
            if event.id is None or not repos.outbox.request_rerun(event.id, event.owner or ""):
                raise RuntimeError("Outbox lease lost before distill continuation")

    async def _handle_distill_due(self, session, event: OutboxEvent) -> None:
        del event
        if settings.LLM_PROVIDER != "openai":
            return
        repos = build_repositories(session)
        if repos.episodic.count_pending_distill() < settings.DISTILL_THRESHOLD:
            return
        for tenant_id, user_id, agent_id, namespace in (
            repos.episodic.list_pending_distill_identities()
        ):
            aggregate_id = outbox_aggregate_key(
                OutboxEventType.DISTILL_AGENT,
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                namespace=namespace,
            )
            repos.outbox.add(
                OutboxEvent(
                    event_type=OutboxEventType.DISTILL_AGENT,
                    aggregate_id=aggregate_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    namespace=namespace,
                    payload={
                        "agent_id": agent_id,
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "namespace": namespace,
                        "batch_size": settings.DISTILL_BATCH_SIZE,
                        "force": False,
                    },
                )
            )
        SqlUnitOfWork(session).commit()

    async def _dispatch(self, session, event: OutboxEvent) -> Any:
        handlers: Dict[str, Handler] = {
            OutboxEventType.VECTOR_SYNC: self._handle_vector_sync,
            OutboxEventType.VECTOR_DELETE: self._handle_vector_delete,
            OutboxEventType.ARCHIVE_AGENT: self._handle_archive_agent,
            OutboxEventType.DISTILL_AGENT: self._handle_distill_agent,
            OutboxEventType.DISTILL_DUE: self._handle_distill_due,
        }
        handler = handlers.get(event.event_type)
        if handler is None:
            raise ValueError(f"Unknown outbox event type: {event.event_type}")
        return await handler(session, event)

    async def _renew_claim(
        self,
        event: OutboxEvent,
        owner: str,
        stop: asyncio.Event,
        renew_interval: float,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=renew_interval)
                return
            except TimeoutError:
                pass
            if event.id is None:
                raise RuntimeError("Cannot renew an event without an id")
            with self._session_factory.create() as session:
                renewed = build_repositories(session).outbox.renew(
                    event.id,
                    owner,
                    get_clock().now(),
                    settings.OUTBOX_LEASE_SECONDS,
                )
                SqlUnitOfWork(session).commit()
            if not renewed:
                raise RuntimeError("Outbox lease lost during dispatch")

    async def _dispatch_with_heartbeat(
        self,
        session,
        event: OutboxEvent,
        renew_interval: float | None = None,
    ) -> Any:
        stop = asyncio.Event()
        interval = renew_interval or max(1.0, settings.OUTBOX_LEASE_SECONDS / 3)
        heartbeat = asyncio.create_task(
            self._renew_claim(event, event.owner or "", stop, interval)
        )
        dispatch = asyncio.create_task(self._dispatch(session, event))
        try:
            done, _ = await asyncio.wait(
                (heartbeat, dispatch), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                await heartbeat
                raise RuntimeError("Outbox heartbeat stopped unexpectedly")
            return await dispatch
        finally:
            stop.set()
            for task in (heartbeat, dispatch):
                if not task.done():
                    task.cancel()
            await asyncio.gather(heartbeat, dispatch, return_exceptions=True)

    async def run_once(self) -> int:
        processed = 0
        while True:
            owner = uuid4().hex
            with self._session_factory.create() as session:
                events = build_repositories(session).outbox.claim_ready(
                    owner,
                    1,
                    get_clock().now(),
                    settings.OUTBOX_LEASE_SECONDS,
                    settings.OUTBOX_MAX_ATTEMPTS,
                )
                SqlUnitOfWork(session).commit()
            if not events:
                return processed

            event = events[0]
            if event.id is None:
                return processed
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
                        result = await self._dispatch_with_heartbeat(session, event)
                        if result is False or (
                            hasattr(result, "success") and not result.success
                        ):
                            raise RuntimeError("Outbox handler reported failure")
                        acknowledged = build_repositories(session).outbox.mark_done(
                            event.id, owner
                        )
                        SqlUnitOfWork(session).commit()
                    if not acknowledged:
                        logger.warning(
                            "Outbox lease lost before completion",
                            extra={
                                "event": "outbox.event.lease_lost",
                                "outbox_event_id": event.id,
                                "result": "abandoned",
                            },
                        )
                        return processed
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
                            event.id,
                            owner,
                            f"{type(exc).__name__}: {exc}"[:2000],
                            available_at,
                            settings.OUTBOX_MAX_ATTEMPTS,
                        )
                        SqlUnitOfWork(session).commit()
                processed += 1

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
