import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlmodel import Session, select

from mems.config import settings
from mems.database import engine
from mems.models import (
    MemsL1Episodic,
    MemsL2ConflictLog,
    MemsL2Event,
    MemsL2Fact,
    MemsL2ProfileItem,
    MemsL2Summary,
)
from mems.schemas import (
    DistillEventItem,
    DistillExtractionResult,
    DistillFactItem,
    DistillProfileUpdate,
    DistillResponse,
)
from mems.services.embedding import EmbeddingProvider
from mems.services.jsonl_utils import JsonlWriter
from mems.services.llm_client import chat as llm_chat
from mems.services.vector_service import get_vector_service


logger = logging.getLogger(__name__)

MULTI_VALUE_PREDICATES = {
    "喜欢",
    "讨厌",
    "擅长",
    "不擅长",
    "相关",
    "uses",
    "likes",
    "dislikes",
}

EXTRACTION_PROMPT = """你是一个高级记忆分析专家，负责从 Agent 的原始对话（L1）中提炼长期语义知识（L2）。

任务：
1. 过滤寒暄、低价值闲聊、一次性短期任务。
2. 提取永久性事实、稳定偏好、重要事件。
3. 为未来 1-10 年交互保留高价值信息。
4. 生成一句长期摘要。

输出必须是合法 JSON，格式如下：
{
  "discarded": [{"text": "", "reason": "greeting|low_signal|ephemeral|duplicate"}],
  "profile_updates": [{"category": "like|dislike|habit|style|identity|value", "key": "", "value": "", "confidence": 0.0, "evidence": ""}],
  "facts": [{"subject": "", "relation": "", "object": "", "fact_type": "general|project|location|tech|relationship", "confidence": 0.0, "evidence": ""}],
  "events": [{"subject": "", "action": "", "object": "", "time_hint": "", "importance": 1, "evidence": ""}],
  "conflict_candidates": [{"memory_type": "profile|fact", "old": "", "new": "", "reason": ""}],
  "long_term_summary": ""
}

L1 Fragments:
__CONTENT__

Existing L2 Context:
__EXISTING_CONTEXT__
"""

RECONCILE_PROMPT = """你是长期记忆对账专家。请根据新知识与已有 L2 背景，判断哪些是重复、增强、更新或冲突。

输出必须是合法 JSON，格式如下：
{
  "profile_updates": [{"category": "", "key": "", "value": "", "confidence": 0.0, "evidence": ""}],
  "facts": [{"subject": "", "relation": "", "object": "", "fact_type": "", "confidence": 0.0, "evidence": ""}],
  "events": [{"subject": "", "action": "", "object": "", "time_hint": "", "importance": 1, "evidence": ""}],
  "conflict_candidates": [{"memory_type": "profile|fact", "old": "", "new": "", "reason": ""}],
  "long_term_summary": ""
}

New Candidates:
__CANDIDATES__

Existing L2 Context:
__EXISTING_CONTEXT__
"""


class DistillService:
    """记忆蒸馏服务 - L1 → L2"""

    def __init__(self, session: Session):
        self.session = session

    def _has_llm_client(self) -> bool:
        return bool(settings.OPENAI_API_KEY)

    @staticmethod
    def _identity_payload(l1: MemsL1Episodic) -> Dict[str, Any]:
        return {
            "tenant_id": l1.tenant_id,
            "user_id": l1.user_id,
            "agent_id": l1.agent_id,
            "scope": l1.scope,
        }

    def _build_existing_context(self, l1: MemsL1Episodic) -> Dict[str, Any]:
        profile_items = self.session.exec(
            select(MemsL2ProfileItem).where(
                MemsL2ProfileItem.tenant_id == l1.tenant_id,
                MemsL2ProfileItem.user_id == l1.user_id,
                MemsL2ProfileItem.agent_id == l1.agent_id,
                MemsL2ProfileItem.scope == l1.scope,
                MemsL2ProfileItem.status == "active",
            )
        ).all()
        fact_items = self.session.exec(
            select(MemsL2Fact).where(
                MemsL2Fact.tenant_id == l1.tenant_id,
                MemsL2Fact.user_id == l1.user_id,
                MemsL2Fact.agent_id == l1.agent_id,
                MemsL2Fact.scope == l1.scope,
                MemsL2Fact.status == "active",
            )
        ).all()
        summaries = self.session.exec(
            select(MemsL2Summary).where(
                MemsL2Summary.tenant_id == l1.tenant_id,
                MemsL2Summary.user_id == l1.user_id,
                MemsL2Summary.agent_id == l1.agent_id,
                MemsL2Summary.scope == l1.scope,
            )
        ).all()

        return {
            "profile": [
                {
                    "category": item.category,
                    "key": item.key,
                    "value": item.value,
                    "version": item.version,
                }
                for item in profile_items[-20:]
            ],
            "facts": [
                {
                    "subject": item.subject,
                    "predicate": item.predicate,
                    "object": item.object,
                    "version": item.version,
                }
                for item in fact_items[-20:]
            ],
            "summaries": [item.content for item in summaries[-5:]],
        }

    def _filter_record(self, l1: MemsL1Episodic) -> Optional[str]:
        content = l1.content.strip()
        lowered = content.lower()
        noise_markers = ["你好", "谢谢", "thanks", "thank you", "收到", "ok", "好的"]

        if not content:
            return "empty"
        if len(content) < 8:
            return "low_signal"
        if (
            all(marker in lowered for marker in ["user:", "assistant:"])
            and len(content) < 30
        ):
            return "low_signal"
        if any(marker in lowered for marker in noise_markers) and len(content) < 40:
            return "greeting"
        return None

    def _extract_json_payload(self, response: str) -> Dict[str, Any]:
        if not response:
            return {}

        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
        if fenced:
            response = fenced.group(1).strip()

        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            response = json_match.group(0)

        return json.loads(response)

    async def _extract_candidates(
        self,
        agent_id: str,
        l1: MemsL1Episodic,
        existing_context: Dict[str, Any],
    ) -> DistillExtractionResult:
        response = await llm_chat(
            messages=[
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT.replace(
                        "__CONTENT__", l1.content
                    ).replace(
                        "__EXISTING_CONTEXT__",
                        json.dumps(existing_context, ensure_ascii=False),
                    ),
                }
            ],
            temperature=0.1,
        )
        payload = self._extract_json_payload(response)
        return DistillExtractionResult.model_validate(payload)

    async def _reconcile_candidates(
        self,
        extracted: DistillExtractionResult,
        existing_context: Dict[str, Any],
    ) -> DistillExtractionResult:
        response = await llm_chat(
            messages=[
                {
                    "role": "user",
                    "content": RECONCILE_PROMPT.replace(
                        "__CANDIDATES__", extracted.model_dump_json(indent=2)
                    ).replace(
                        "__EXISTING_CONTEXT__",
                        json.dumps(existing_context, ensure_ascii=False),
                    ),
                }
            ],
            temperature=0.1,
        )
        payload = self._extract_json_payload(response)
        merged = extracted.model_dump()
        for key, value in payload.items():
            merged[key] = value
        return DistillExtractionResult.model_validate(merged)

    def _append_source(self, existing_ids: list[int], source_id: int) -> list[int]:
        merged = list(existing_ids)
        if source_id not in merged:
            merged.append(source_id)
        return merged

    def _reconcile_profile_item(
        self, l1: MemsL1Episodic, update: DistillProfileUpdate, source_id: int
    ) -> tuple[int, int]:
        existing = self.session.exec(
            select(MemsL2ProfileItem).where(
                MemsL2ProfileItem.tenant_id == l1.tenant_id,
                MemsL2ProfileItem.user_id == l1.user_id,
                MemsL2ProfileItem.agent_id == l1.agent_id,
                MemsL2ProfileItem.scope == l1.scope,
                MemsL2ProfileItem.category == update.category,
                MemsL2ProfileItem.key == update.key,
                MemsL2ProfileItem.status == "active",
            )
        ).first()

        now = datetime.now(timezone.utc)
        if existing and existing.value == update.value:
            existing.last_verified_at = now
            existing.confidence = max(existing.confidence, update.confidence)
            existing.source_l1_ids = self._append_source(
                existing.source_l1_ids, source_id
            )
            self.session.add(existing)
            return 0, 1

        if existing and existing.value != update.value:
            existing.status = "superseded"
            existing.last_verified_at = now
            self.session.add(existing)

            conflict = MemsL2ConflictLog(
                **self._identity_payload(l1),
                memory_type="profile",
                old_value=f"{existing.category}:{existing.key}={existing.value}",
                new_value=f"{update.category}:{update.key}={update.value}",
                resolution="superseded",
                reason="value updated by newer memory",
                source_l1_ids=[source_id],
            )
            self.session.add(conflict)

            new_item = MemsL2ProfileItem(
                **self._identity_payload(l1),
                category=update.category,
                key=update.key,
                value=update.value,
                confidence=update.confidence,
                source_l1_ids=[source_id],
                supersedes_id=existing.id,
                version=existing.version + 1,
            )
            self.session.add(new_item)
            return 1, 1

        new_item = MemsL2ProfileItem(
            **self._identity_payload(l1),
            category=update.category,
            key=update.key,
            value=update.value,
            confidence=update.confidence,
            source_l1_ids=[source_id],
        )
        self.session.add(new_item)
        return 1, 0

    def _reconcile_fact_item(
        self, l1: MemsL1Episodic, fact: DistillFactItem, source_id: int
    ) -> tuple[int, int]:
        existing_exact = self.session.exec(
            select(MemsL2Fact).where(
                MemsL2Fact.tenant_id == l1.tenant_id,
                MemsL2Fact.user_id == l1.user_id,
                MemsL2Fact.agent_id == l1.agent_id,
                MemsL2Fact.scope == l1.scope,
                MemsL2Fact.subject == fact.subject,
                MemsL2Fact.predicate == fact.relation,
                MemsL2Fact.object == fact.object,
                MemsL2Fact.status == "active",
            )
        ).first()

        now = datetime.now(timezone.utc)
        if existing_exact:
            existing_exact.last_verified_at = now
            existing_exact.confidence = max(existing_exact.confidence, fact.confidence)
            existing_exact.source_l1_ids = self._append_source(
                existing_exact.source_l1_ids, source_id
            )
            self.session.add(existing_exact)
            return 0, 1

        conflicting = self.session.exec(
            select(MemsL2Fact).where(
                MemsL2Fact.tenant_id == l1.tenant_id,
                MemsL2Fact.user_id == l1.user_id,
                MemsL2Fact.agent_id == l1.agent_id,
                MemsL2Fact.scope == l1.scope,
                MemsL2Fact.subject == fact.subject,
                MemsL2Fact.predicate == fact.relation,
                MemsL2Fact.status == "active",
            )
        ).all()

        if fact.relation in MULTI_VALUE_PREDICATES:
            new_item = MemsL2Fact(
                **self._identity_payload(l1),
                subject=fact.subject,
                predicate=fact.relation,
                object=fact.object,
                fact_type=fact.fact_type,
                confidence=fact.confidence,
                source_l1_ids=[source_id],
            )
            self.session.add(new_item)
            return 1, 0

        for existing in conflicting:
            if existing.object == fact.object:
                continue
            existing.status = "superseded"
            existing.last_verified_at = now
            self.session.add(existing)

            conflict = MemsL2ConflictLog(
                **self._identity_payload(l1),
                memory_type="fact",
                old_value=f"{existing.subject} {existing.predicate} {existing.object}",
                new_value=f"{fact.subject} {fact.relation} {fact.object}",
                resolution="superseded",
                reason="fact updated by newer memory",
                source_l1_ids=[source_id],
            )
            self.session.add(conflict)

            new_item = MemsL2Fact(
                **self._identity_payload(l1),
                subject=fact.subject,
                predicate=fact.relation,
                object=fact.object,
                fact_type=fact.fact_type,
                confidence=fact.confidence,
                source_l1_ids=[source_id],
                supersedes_id=existing.id,
                version=existing.version + 1,
            )
            self.session.add(new_item)
            return 1, 1

        new_item = MemsL2Fact(
            **self._identity_payload(l1),
            subject=fact.subject,
            predicate=fact.relation,
            object=fact.object,
            fact_type=fact.fact_type,
            confidence=fact.confidence,
            source_l1_ids=[source_id],
        )
        self.session.add(new_item)
        return 1, 0

    def _commit_event(
        self, l1: MemsL1Episodic, event: DistillEventItem, source_id: int
    ) -> int:
        new_event = MemsL2Event(
            **self._identity_payload(l1),
            subject=event.subject,
            action=event.action,
            object=event.object,
            time_hint=event.time_hint,
            importance_score=event.importance,
            source_l1_ids=[source_id],
        )
        self.session.add(new_event)
        return 1

    async def _commit_summary(
        self,
        l1: MemsL1Episodic,
        summary: str,
        source_id: int,
        embedding_service: Optional[EmbeddingProvider],
    ) -> int:
        if not summary.strip():
            return 0

        existing = self.session.exec(
            select(MemsL2Summary).where(
                MemsL2Summary.tenant_id == l1.tenant_id,
                MemsL2Summary.user_id == l1.user_id,
                MemsL2Summary.agent_id == l1.agent_id,
                MemsL2Summary.scope == l1.scope,
                MemsL2Summary.summary_type == "long_term",
            )
        ).all()
        if existing and existing[-1].content == summary:
            existing[-1].last_verified_at = datetime.now(timezone.utc)
            existing[-1].source_l1_ids = self._append_source(
                existing[-1].source_l1_ids, source_id
            )
            self.session.add(existing[-1])
            return 0

        vector_id = str(uuid.uuid4())
        summary_record = MemsL2Summary(
            **self._identity_payload(l1),
            summary_type="long_term",
            content=summary,
            vector_id=vector_id,
            vector_status="pending",
            source_l1_ids=[source_id],
        )
        self.session.add(summary_record)
        self.session.commit()
        self.session.refresh(summary_record)

        try:
            if embedding_service is None:
                from mems.services.embedding import get_embedding_service

                embedding_service = await get_embedding_service()
            vector_service = await get_vector_service()
            summary_vector = (await embedding_service.embed([summary]))[0]
            await vector_service.upsert(
                collection_name=f"agent_{l1.agent_id}",
                points=[
                    {
                        "id": vector_id,
                        "vector": summary_vector,
                        "payload": {
                            "tenant_id": l1.tenant_id,
                            "user_id": l1.user_id,
                            "agent_id": l1.agent_id,
                            "scope": l1.scope,
                            "memory_type": "l2_summary",
                            "vector_id": vector_id,
                            "content": summary,
                        },
                    }
                ],
            )
            summary_record.vector_status = "ready"
            summary_record.last_sync_error = None
            summary_record.last_sync_at = datetime.now(timezone.utc)
        except Exception as exc:
            logger.error("Failed to sync L2 summary vector replica: %s", exc)
            summary_record.vector_status = "failed"
            summary_record.last_sync_error = f"vector: {exc}"
            summary_record.last_sync_at = datetime.now(timezone.utc)
        self.session.add(summary_record)
        return 1

    async def distill(
        self,
        agent_id: str,
        batch_size: int = 10,
        force: bool = False,
        embedding_service: Optional[EmbeddingProvider] = None,
    ) -> DistillResponse:
        _ = embedding_service
        query = select(MemsL1Episodic).where(MemsL1Episodic.agent_id == agent_id)
        if not force:
            query = query.where(
                MemsL1Episodic.is_distilled == False,  # noqa: E712
                MemsL1Episodic.importance_score >= 0.5,
            )

        l1_records = self.session.exec(query.limit(batch_size)).all()
        if not l1_records:
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
        audit_records = []

        for l1 in l1_records:
            if l1.id is None:
                continue

            filter_reason = self._filter_record(l1)
            if filter_reason:
                l1.is_distilled = True
                self.session.add(l1)
                audit_records.append(
                    {
                        "tenant_id": l1.tenant_id,
                        "user_id": l1.user_id,
                        "agent_id": l1.agent_id,
                        "scope": l1.scope,
                        "l1_id": l1.id,
                        "discarded": [{"text": l1.content, "reason": filter_reason}],
                        "profile_updates": [],
                        "facts": [],
                        "events": [],
                        "conflict_candidates": [],
                        "long_term_summary": "",
                    }
                )
                distilled_count += 1
                continue

            existing_context = self._build_existing_context(l1)
            try:
                extracted = await self._extract_candidates(
                    agent_id, l1, existing_context
                )
                reconciled = await self._reconcile_candidates(
                    extracted, existing_context
                )
            except Exception as exc:
                logger.error(f"LLM Error for L1 id={l1.id}: {exc}")
                continue

            local_created = 0
            local_updated = 0
            for update in reconciled.profile_updates:
                c, u = self._reconcile_profile_item(l1, update, l1.id)
                local_created += c
                local_updated += u

            for fact in reconciled.facts:
                c, u = self._reconcile_fact_item(l1, fact, l1.id)
                local_created += c
                local_updated += u

            for event in reconciled.events:
                local_created += self._commit_event(l1, event, l1.id)

            local_created += await self._commit_summary(
                l1,
                reconciled.long_term_summary,
                l1.id,
                embedding_service,
            )

            for conflict in reconciled.conflict_candidates:
                self.session.add(
                    MemsL2ConflictLog(
                        **self._identity_payload(l1),
                        memory_type=conflict.memory_type,
                        old_value=conflict.old,
                        new_value=conflict.new,
                        resolution="reviewed",
                        reason=conflict.reason,
                        source_l1_ids=[l1.id],
                    )
                )
                local_created += 1

            l1.is_distilled = True
            self.session.add(l1)

            created_count += local_created
            updated_count += local_updated
            distilled_count += 1
            audit_records.append(
                {
                    "tenant_id": l1.tenant_id,
                    "user_id": l1.user_id,
                    "agent_id": l1.agent_id,
                    "scope": l1.scope,
                    "l1_id": l1.id,
                    **reconciled.model_dump(),
                }
            )

        self.session.commit()

        l2_writer = JsonlWriter(settings.storage_l2_path, "l2")
        for record in audit_records:
            l2_writer.write(agent_id, record)

        return DistillResponse(
            success=True,
            distilled_count=distilled_count,
            l2_created=created_count,
            l2_updated=updated_count,
            message=f"Distilled {distilled_count} L1 records",
        )


def check_distill_threshold() -> int:
    """检查是否达到蒸馏阈值，返回需要蒸馏的记录数"""
    with Session(engine) as session:
        records = session.exec(
            select(MemsL1Episodic).where(
                MemsL1Episodic.is_distilled == False,  # noqa: E712
                MemsL1Episodic.importance_score >= 0.5,
            )
        ).all()
        return len(records)


async def trigger_distill_automatically(
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """自动触发蒸馏任务（供调度器调用）"""
    try:
        from mems.services.embedding import get_embedding_service

        if agent_id is None:
            threshold = settings.DISTILL_THRESHOLD
            current_count = check_distill_threshold()
            if current_count < threshold:
                logger.info(
                    f"Distill threshold not reached: {current_count}/{threshold}"
                )
                return {
                    "triggered": False,
                    "reason": f"threshold not reached ({current_count}/{threshold})",
                    "processed": 0,
                }

            with Session(engine) as session:
                agent_ids = session.exec(
                    select(MemsL1Episodic.agent_id)
                    .where(MemsL1Episodic.is_distilled == False)  # noqa: E712
                    .distinct()
                ).all()

            total_processed = 0
            for aid in agent_ids:
                if not aid:
                    continue
                embedding_service = await get_embedding_service()
                with Session(engine) as session:
                    result = await DistillService(session).distill(
                        agent_id=aid,
                        batch_size=settings.DISTILL_BATCH_SIZE,
                        force=False,
                        embedding_service=embedding_service,
                    )
                    total_processed += result.distilled_count

            logger.info(f"Auto distill triggered: processed {total_processed} records")
            return {
                "triggered": True,
                "reason": "threshold reached",
                "processed": total_processed,
            }

        embedding_service = await get_embedding_service()
        with Session(engine) as session:
            result = await DistillService(session).distill(
                agent_id=agent_id,
                batch_size=settings.DISTILL_BATCH_SIZE,
                force=True,
                embedding_service=embedding_service,
            )
        logger.info(
            f"Auto distill for {agent_id}: processed {result.distilled_count} records"
        )
        return {
            "triggered": True,
            "agent_id": agent_id,
            "processed": result.distilled_count,
        }
    except Exception as exc:
        logger.error(f"Auto distill failed: {exc}")
        return {"triggered": False, "error": str(exc), "processed": 0}
