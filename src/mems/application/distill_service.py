"""Distillation application service."""

from __future__ import annotations

import json
import logging
import re
from time import perf_counter
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from mems.application.commands import DistillResponse
from mems.config import settings
from mems.domain.identity import MemoryIdentity, MemoryNamespace
from mems.domain.knowledge import DistillExtractionResult, DistillSource, Summary
from mems.domain.outbox import (
    OutboxEvent,
    OutboxEventType,
    outbox_aggregate_key,
)
from mems.domain.ports import (
    Clock,
    EpisodicRepository,
    KnowledgeRepository,
    LLMPort,
    OutboxRepository,
    UnitOfWork,
)
from mems.domain.services.distillation import MIN_DISTILL_IMPORTANCE, filter_record
from mems.domain.services.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_PROMPT,
)
from mems.domain.services.reconciliation import ReconciliationService


logger = logging.getLogger(__name__)


class DistillApplicationService:
    """Distill L1 episodic records into structured L2 knowledge."""

    def __init__(
        self,
        episodic: EpisodicRepository,
        knowledge: KnowledgeRepository,
        outbox: OutboxRepository,
        llm: LLMPort,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._episodic = episodic
        self._knowledge = knowledge
        self._outbox = outbox
        self._llm = llm
        self._clock = clock
        self._uow = uow

    def _has_llm_client(self) -> bool:
        return settings.LLM_PROVIDER == "openai" and bool(settings.OPENAI_API_KEY)

    @staticmethod
    def _append_source(existing_ids: list[int], source_id: int) -> list[int]:
        merged = list(existing_ids)
        if source_id not in merged:
            merged.append(source_id)
        return merged

    @staticmethod
    def _extract_json_payload(response: str) -> Dict[str, Any]:
        if not response or not response.strip():
            raise ValueError("Empty LLM response")

        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
        if fenced:
            response = fenced.group(1).strip()

        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            response = json_match.group(0)

        payload = json.loads(response)
        if not isinstance(payload, dict) or not (
            payload.keys() & DistillExtractionResult.model_fields.keys()
        ):
            raise ValueError("LLM response contains no extraction fields")
        DistillExtractionResult.model_validate(payload)
        return payload

    async def _request_llm_json(self, messages: list[Dict[str, str]]) -> Dict[str, Any]:
        last_exc: Exception | None = None
        for json_mode in (True, False):
            try:
                response = await self._llm.chat(
                    messages=messages, temperature=0.1, json_mode=json_mode
                )
                return self._extract_json_payload(response)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "LLM JSON attempt failed",
                    exc_info=True,
                    extra={
                        "event": "distill.llm_json_attempt.failed",
                        "operation": "distill_llm_call",
                        "result": "retrying",
                        "error_type": type(exc).__name__,
                    },
                )
        raise RuntimeError(f"LLM JSON extraction failed: {last_exc}") from last_exc

    async def _extract_candidates(
        self,
        agent_id: str,
        source: DistillSource,
    ) -> DistillExtractionResult:
        del agent_id
        payload = await self._request_llm_json(
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": EXTRACTION_USER_PROMPT.replace("__CONTENT__", source.content),
                },
            ],
        )
        return DistillExtractionResult.model_validate(payload)

    def _commit_summary(
        self,
        source: DistillSource,
        summary: str,
        source_id: int,
    ) -> int:
        if not summary.strip():
            return 0

        existing = self._knowledge.list_summaries(source.identity)
        if existing and existing[-1].content == summary:
            existing[-1].last_verified_at = self._clock.now()
            existing[-1].source_l1_ids = self._append_source(existing[-1].source_l1_ids, source_id)
            self._knowledge.save_summary(existing[-1])
            return 0

        vector_id = str(uuid4())
        summary_record = Summary(
            tenant_id=source.tenant_id,
            user_id=source.user_id,
            agent_id=source.agent_id,
            namespace=source.identity.namespace_key,
            summary_type="long_term",
            content=summary,
            vector_id=vector_id,
            vector_status="pending",
            source_l1_ids=[source_id],
        )
        self._knowledge.save_summary(summary_record)
        self._outbox.add(
            OutboxEvent(
                event_type=OutboxEventType.VECTOR_SYNC,
                aggregate_id=outbox_aggregate_key(
                    OutboxEventType.VECTOR_SYNC,
                    tenant_id=source.tenant_id,
                    user_id=source.user_id,
                    agent_id=source.agent_id,
                    namespace=source.identity.namespace_key,
                    resource=vector_id,
                ),
                tenant_id=source.tenant_id,
                user_id=source.user_id,
                agent_id=source.agent_id,
                namespace=source.identity.namespace_key,
                payload={
                    "kind": "summary",
                    "summary_id": summary_record.id,
                    "vector_id": vector_id,
                    "content": summary,
                    "agent_id": source.agent_id,
                    "tenant_id": source.tenant_id,
                    "user_id": source.user_id,
                    "namespace": source.identity.namespace_key,
                },
            )
        )
        return 1

    @staticmethod
    def _is_l2_unique_conflict(exc: IntegrityError) -> bool:
        diagnostic = getattr(exc.orig, "diag", None)
        return getattr(diagnostic, "constraint_name", None) in {
            "uq_l2_profile_active_key",
            "uq_l2_fact_active_exact",
        }

    def _reconcile_extracted(
        self,
        source: DistillSource,
        extracted: DistillExtractionResult,
        finalize: Optional[Callable[[], None]],
    ) -> tuple[int, int]:
        reconciler = ReconciliationService(self._knowledge, self._clock)
        for attempt in range(3):
            created = 0
            updated = 0
            try:
                with self._uow.begin_nested():
                    for update in extracted.profile_updates:
                        c, u = reconciler.reconcile_profile(
                            source.identity, update, source.source_id, source.created_at
                        )
                        created += c
                        updated += u

                    for fact in extracted.facts:
                        c, u = reconciler.reconcile_fact(
                            source.identity, fact, source.source_id, source.created_at
                        )
                        created += c
                        updated += u

                    for event in extracted.events:
                        created += reconciler.commit_event(
                            source.identity, event, source.source_id
                        )

                    created += self._commit_summary(
                        source, extracted.long_term_summary, source.source_id
                    )
                    if finalize is not None:
                        finalize()
                return created, updated
            except IntegrityError as exc:
                if not self._is_l2_unique_conflict(exc) or attempt == 2:
                    raise
                logger.warning(
                    "Concurrent L2 update detected; retrying reconciliation",
                    extra={
                        "event": "distill.l2_conflict.retry",
                        "source_l1_id": source.source_id,
                        "attempt": attempt + 1,
                    },
                )
        raise RuntimeError("L2 reconciliation retries exhausted")

    async def process_source(
        self,
        source: DistillSource,
        finalize: Optional[Callable[[], None]] = None,
    ) -> tuple[int, int]:
        """Extract first, then atomically reconcile L2 and finalize the source."""
        if not self._has_llm_client():
            raise RuntimeError("LLM is not configured; distillation is disabled")
        if filter_record(source.content):
            if finalize is not None:
                with self._uow.begin_nested():
                    finalize()
            return 0, 0

        extracted = await self._extract_candidates(source.agent_id, source)
        return self._reconcile_extracted(source, extracted, finalize)

    async def distill(
        self,
        agent_id: str,
        batch_size: int = 10,
        force: bool = False,
        min_importance: float = MIN_DISTILL_IMPORTANCE,
        tenant_id: str | None = None,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> DistillResponse:
        identity = MemoryIdentity(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            memory_namespace=(
                MemoryNamespace.from_key(namespace) if namespace else None
            ),
        )
        records = self._episodic.find_for_distill(
            agent_id,
            batch_size,
            force,
            min_importance,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            namespace=identity.namespace_key,
        )
        if not records:
            busy = self._episodic.has_pending_for_distill(
                agent_id,
                force,
                min_importance,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                namespace=identity.namespace_key,
            )
            return DistillResponse(
                success=not busy,
                distilled_count=0,
                l2_created=0,
                l2_updated=0,
                message="Distill records are busy" if busy else "No records to distill",
            )

        if not self._has_llm_client():
            return DistillResponse(
                success=False,
                distilled_count=0,
                l2_created=0,
                l2_updated=0,
                message="LLM is not configured; skipping distillation",
            )

        created_count = 0
        updated_count = 0
        distilled_count = 0

        for memory in records:
            if memory.id is None:
                continue
            source = DistillSource.from_episodic(memory)
            started = perf_counter()

            def finalize() -> None:
                memory.mark_distilled()
                self._episodic.save(memory)

            try:
                local_created, local_updated = await self.process_source(source, finalize=finalize)
            except Exception as exc:
                logger.error(
                    "Memory distillation failed",
                    exc_info=True,
                    extra={
                        "event": "distill.source.failed",
                        "source_l1_id": memory.id,
                        "error_type": type(exc).__name__,
                        "result": "failed",
                    },
                )
                continue

            created_count += local_created
            updated_count += local_updated
            distilled_count += 1
            logger.info(
                "Memory distillation completed",
                extra={
                    "event": "distill.source.completed",
                    "source_l1_id": memory.id,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                    "l2_created": local_created,
                    "l2_updated": local_updated,
                    "result": "success",
                },
            )

        self._uow.commit()
        expected_count = sum(memory.id is not None for memory in records)
        success = distilled_count == expected_count
        return DistillResponse(
            success=success,
            distilled_count=distilled_count,
            l2_created=created_count,
            l2_updated=updated_count,
            message=(
                f"Distilled {distilled_count}/{expected_count} L1 records"
                if not success
                else f"Distilled {distilled_count} L1 records"
            ),
        )
