"""Distillation application service."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict
from uuid import uuid4

from mems.application.commands import DistillResponse
from mems.config import settings
from mems.domain.knowledge import DistillExtractionResult, DistillSource, Summary
from mems.domain.outbox import OutboxEvent, OutboxEventType
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
        return bool(settings.OPENAI_API_KEY)

    @staticmethod
    def _append_source(existing_ids: list[int], source_id: int) -> list[int]:
        merged = list(existing_ids)
        if source_id not in merged:
            merged.append(source_id)
        return merged

    @staticmethod
    def _extract_json_payload(response: str) -> Dict[str, Any]:
        if not response:
            return {}

        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
        if fenced:
            response = fenced.group(1).strip()

        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            response = json_match.group(0)

        return json.loads(response)

    async def _request_llm_json(
        self, messages: list[Dict[str, str]]
    ) -> Dict[str, Any]:
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
                    "LLM JSON attempt failed (json_mode=%s): %s", json_mode, exc
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
                    "content": EXTRACTION_USER_PROMPT.replace(
                        "__CONTENT__", source.content
                    ),
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
            existing[-1].source_l1_ids = self._append_source(
                existing[-1].source_l1_ids, source_id
            )
            self._knowledge.save_summary(existing[-1])
            return 0

        vector_id = str(uuid4())
        summary_record = Summary(
            tenant_id=source.tenant_id,
            user_id=source.user_id,
            agent_id=source.agent_id,
            scope=source.scope,
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
                aggregate_id=vector_id,
                payload={
                    "kind": "summary",
                    "summary_id": summary_record.id,
                    "vector_id": vector_id,
                    "content": summary,
                    "agent_id": source.agent_id,
                    "tenant_id": source.tenant_id,
                    "user_id": source.user_id,
                    "scope": source.scope,
                },
            )
        )
        return 1

    async def process_source(self, source: DistillSource) -> tuple[int, int]:
        """Extract and reconcile one source record into L2."""
        if filter_record(source.content):
            return 0, 0

        extracted = await self._extract_candidates(source.agent_id, source)
        reconciler = ReconciliationService(self._knowledge, self._clock)

        created = 0
        updated = 0
        for update in extracted.profile_updates:
            c, u = reconciler.reconcile_profile(
                source.identity, update, source.source_id
            )
            created += c
            updated += u

        for fact in extracted.facts:
            c, u = reconciler.reconcile_fact(source.identity, fact, source.source_id)
            created += c
            updated += u

        for event in extracted.events:
            created += reconciler.commit_event(source.identity, event, source.source_id)

        created += self._commit_summary(
            source, extracted.long_term_summary, source.source_id
        )
        return created, updated

    async def distill(
        self,
        agent_id: str,
        batch_size: int = 10,
        force: bool = False,
        min_importance: float = MIN_DISTILL_IMPORTANCE,
    ) -> DistillResponse:
        records = self._episodic.find_for_distill(
            agent_id, batch_size, force, min_importance
        )
        if not records:
            return DistillResponse(
                success=True,
                distilled_count=0,
                l2_created=0,
                l2_updated=0,
                message="No records to distill",
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
            try:
                local_created, local_updated = await self.process_source(source)
            except Exception as exc:
                logger.error(f"LLM Error for L1 id={memory.id}: {exc}")
                continue

            memory.mark_distilled()
            self._episodic.save(memory)
            created_count += local_created
            updated_count += local_updated
            distilled_count += 1

        self._uow.commit()
        return DistillResponse(
            success=True,
            distilled_count=distilled_count,
            l2_created=created_count,
            l2_updated=updated_count,
            message=f"Distilled {distilled_count} L1 records",
        )
