import json
import logging
import re
import uuid
from dataclasses import dataclass
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
from mems.services.distill_prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_PROMPT,
    MULTI_VALUE_PREDICATES,
    RECONCILE_SYSTEM_PROMPT,
    RECONCILE_USER_PROMPT,
)
from mems.services.llm_client import chat as llm_chat
from mems.services.vector_service import get_vector_service


logger = logging.getLogger(__name__)

# Guards threshold-triggered distillation so only one run is active per process.
# 防止阈值触发的蒸馏在单进程内并发执行，保证同一时刻只有一个运行实例。
_distill_in_progress = False


@dataclass
class DistillSource:
    """A distilled input record independent of its storage layer.

    蒸馏输入来源记录，与存储层解耦：既可由 L1 行构造，也可由 L3 归档记录构造。
    """

    tenant_id: Optional[str]
    user_id: Optional[str]
    agent_id: str
    session_id: Optional[str]
    scope: Optional[str]
    content: str
    source_id: int
    created_at: datetime
    importance_score: float = 0.5
    # Fallback evidence pointer when the source lives in an L3 JSONL file.
    # 来源位于 L3 JSONL 时的兜底证据指针（文件路径 + 行号）。
    l3_path: Optional[str] = None
    l3_line: Optional[int] = None

    @classmethod
    def from_l1(cls, l1: MemsL1Episodic) -> "DistillSource":
        """Build a source record from an L1 episodic row.

        从 L1 情景记忆行构造来源记录。
        """
        return cls(
            tenant_id=l1.tenant_id,
            user_id=l1.user_id,
            agent_id=l1.agent_id,
            session_id=l1.session_id,
            scope=l1.scope,
            content=l1.content,
            source_id=l1.id or 0,
            created_at=l1.created_at,
            importance_score=l1.importance_score,
        )

    @classmethod
    def from_archive_record(
        cls, record: Dict[str, Any], path: str, line_no: int
    ) -> "DistillSource":
        """Build a source record from one L3 JSONL archive entry.

        从一条 L3 JSONL 归档记录构造来源记录。
        """
        created_at = datetime.now(timezone.utc)
        raw_created = record.get("created_at")
        if isinstance(raw_created, str):
            try:
                created_at = datetime.fromisoformat(raw_created)
            except ValueError:
                pass
        return cls(
            tenant_id=record.get("tenant_id"),
            user_id=record.get("user_id"),
            agent_id=record.get("agent_id", ""),
            session_id=record.get("session_id"),
            scope=record.get("scope"),
            content=record.get("content", ""),
            source_id=int(record["id"]) if record.get("id") is not None else 0,
            created_at=created_at,
            importance_score=float(record.get("importance_score", 0.5)),
            l3_path=path,
            l3_line=line_no,
        )

class DistillService:
    """记忆蒸馏服务 - L1 → L2"""

    def __init__(self, session: Session):
        """Bind the distillation service to a database session.

        将蒸馏服务绑定到一个数据库会话。
        """
        self.session = session

    def _has_llm_client(self) -> bool:
        """Check whether LLM credentials are available.

        检查当前是否具备可用的 LLM 凭据。
        """
        return bool(settings.OPENAI_API_KEY)

    @staticmethod
    def _identity_payload(source: DistillSource) -> Dict[str, Any]:
        """Extract reusable identity fields from a distill source.

        从蒸馏来源记录中提取可复用的身份字段。
        """
        return {
            "tenant_id": source.tenant_id,
            "user_id": source.user_id,
            "agent_id": source.agent_id,
            "scope": source.scope,
        }

    def _build_existing_context(self, source: DistillSource) -> Dict[str, Any]:
        """Collect nearby L2 context to guide extraction and reconciliation.

        收集关联的 L2 上下文，用于后续提取与对账。
        """
        profile_items = self.session.exec(
            select(MemsL2ProfileItem).where(
                MemsL2ProfileItem.tenant_id == source.tenant_id,
                MemsL2ProfileItem.user_id == source.user_id,
                MemsL2ProfileItem.agent_id == source.agent_id,
                MemsL2ProfileItem.scope == source.scope,
                MemsL2ProfileItem.status == "active",
            )
        ).all()
        fact_items = self.session.exec(
            select(MemsL2Fact).where(
                MemsL2Fact.tenant_id == source.tenant_id,
                MemsL2Fact.user_id == source.user_id,
                MemsL2Fact.agent_id == source.agent_id,
                MemsL2Fact.scope == source.scope,
                MemsL2Fact.status == "active",
            )
        ).all()
        summaries = self.session.exec(
            select(MemsL2Summary).where(
                MemsL2Summary.tenant_id == source.tenant_id,
                MemsL2Summary.user_id == source.user_id,
                MemsL2Summary.agent_id == source.agent_id,
                MemsL2Summary.scope == source.scope,
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

    def _filter_record(self, content: str) -> Optional[str]:
        """Skip low-value source records before sending them to the LLM.

        在调用 LLM 之前过滤低价值的来源记录。
        """
        content = content.strip()
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
        """Extract the first JSON object from raw LLM output.

        从原始 LLM 输出中提取第一个 JSON 对象。
        """
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
        """Request structured JSON from the LLM with a single fallback retry.

        请求结构化 JSON：优先 JSON mode，失败时降级重试一次，仍失败则抛出。
        """
        last_exc: Exception | None = None
        for json_mode in (True, False):
            try:
                response = await llm_chat(
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
        """Ask the LLM to extract candidate long-term memories.

        调用 LLM 从原文提取候选长期记忆结构（无已有上下文，避免提前去重）。
        """
        payload = await self._request_llm_json(
            messages=[
                {
                    "role": "system",
                    "content": EXTRACTION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": EXTRACTION_USER_PROMPT.replace(
                        "__CONTENT__", source.content
                    ),
                },
            ],
        )
        return DistillExtractionResult.model_validate(payload)

    async def _reconcile_candidates(
        self,
        extracted: DistillExtractionResult,
        existing_context: Dict[str, Any],
    ) -> DistillExtractionResult:
        """Ask the LLM to reconcile new candidates against existing L2 data.

        调用 LLM 将新候选结果与现有 L2 数据进行对账。
        """
        payload = await self._request_llm_json(
            messages=[
                {
                    "role": "system",
                    "content": RECONCILE_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": RECONCILE_USER_PROMPT.replace(
                        "__CANDIDATES__", extracted.model_dump_json(indent=2)
                    ).replace(
                        "__EXISTING_CONTEXT__",
                        json.dumps(existing_context, ensure_ascii=False),
                    ),
                },
            ],
        )
        merged = extracted.model_dump()
        for key, value in payload.items():
            merged[key] = value
        return DistillExtractionResult.model_validate(merged)

    def _append_source(self, existing_ids: list[int], source_id: int) -> list[int]:
        """Append a source L1 id if it is not already present.

        如果来源 L1 id 尚不存在，则将其追加进去。
        """
        merged = list(existing_ids)
        if source_id not in merged:
            merged.append(source_id)
        return merged

    def _reconcile_profile_item(
        self, source: DistillSource, update: DistillProfileUpdate, source_id: int
    ) -> tuple[int, int]:
        """Merge one extracted profile item into L2 profile storage.

        将单条提取出的画像信息合并到 L2 画像存储中。
        """
        existing = self.session.exec(
            select(MemsL2ProfileItem).where(
                MemsL2ProfileItem.tenant_id == source.tenant_id,
                MemsL2ProfileItem.user_id == source.user_id,
                MemsL2ProfileItem.agent_id == source.agent_id,
                MemsL2ProfileItem.scope == source.scope,
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
                **self._identity_payload(source),
                memory_type="profile",
                old_value=f"{existing.category}:{existing.key}={existing.value}",
                new_value=f"{update.category}:{update.key}={update.value}",
                resolution="superseded",
                reason="value updated by newer memory",
                source_l1_ids=[source_id],
            )
            self.session.add(conflict)

            new_item = MemsL2ProfileItem(
                **self._identity_payload(source),
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
            **self._identity_payload(source),
            category=update.category,
            key=update.key,
            value=update.value,
            confidence=update.confidence,
            source_l1_ids=[source_id],
        )
        self.session.add(new_item)
        return 1, 0

    def _reconcile_fact_item(
        self, source: DistillSource, fact: DistillFactItem, source_id: int
    ) -> tuple[int, int]:
        """Merge one extracted fact into L2 fact storage.

        将单条提取出的事实信息合并到 L2 事实存储中。
        """
        existing_exact = self.session.exec(
            select(MemsL2Fact).where(
                MemsL2Fact.tenant_id == source.tenant_id,
                MemsL2Fact.user_id == source.user_id,
                MemsL2Fact.agent_id == source.agent_id,
                MemsL2Fact.scope == source.scope,
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
                MemsL2Fact.tenant_id == source.tenant_id,
                MemsL2Fact.user_id == source.user_id,
                MemsL2Fact.agent_id == source.agent_id,
                MemsL2Fact.scope == source.scope,
                MemsL2Fact.subject == fact.subject,
                MemsL2Fact.predicate == fact.relation,
                MemsL2Fact.status == "active",
            )
        ).all()

        if fact.relation in MULTI_VALUE_PREDICATES:
            new_item = MemsL2Fact(
                **self._identity_payload(source),
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
                **self._identity_payload(source),
                memory_type="fact",
                old_value=f"{existing.subject} {existing.predicate} {existing.object}",
                new_value=f"{fact.subject} {fact.relation} {fact.object}",
                resolution="superseded",
                reason="fact updated by newer memory",
                source_l1_ids=[source_id],
            )
            self.session.add(conflict)

            new_item = MemsL2Fact(
                **self._identity_payload(source),
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
            **self._identity_payload(source),
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
        self, source: DistillSource, event: DistillEventItem, source_id: int
    ) -> int:
        """Persist one extracted event into L2 event storage.

        将单条提取出的事件写入 L2 事件存储。
        """
        new_event = MemsL2Event(
            **self._identity_payload(source),
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
        source: DistillSource,
        summary: str,
        source_id: int,
        embedding_service: Optional[EmbeddingProvider],
    ) -> int:
        """Persist a long-term summary and sync its vector replica.

        保存长期摘要，并同步其向量副本。
        """
        if not summary.strip():
            return 0

        existing = self.session.exec(
            select(MemsL2Summary).where(
                MemsL2Summary.tenant_id == source.tenant_id,
                MemsL2Summary.user_id == source.user_id,
                MemsL2Summary.agent_id == source.agent_id,
                MemsL2Summary.scope == source.scope,
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
            **self._identity_payload(source),
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
                collection_name=f"agent_{source.agent_id}",
                points=[
                    {
                        "id": vector_id,
                        "vector": summary_vector,
                        "payload": {
                            "tenant_id": source.tenant_id,
                            "user_id": source.user_id,
                            "agent_id": source.agent_id,
                            "scope": source.scope,
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
            logger.error("Failed to sync L2 summary vector replica: %r", exc)
            summary_record.vector_status = "failed"
            summary_record.last_sync_error = f"vector: {exc}"
            summary_record.last_sync_at = datetime.now(timezone.utc)
        self.session.add(summary_record)
        return 1

    async def process_source(
        self,
        source: DistillSource,
        embedding_service: Optional[EmbeddingProvider] = None,
    ) -> tuple[int, int]:
        """Extract, reconcile and commit one source record into L2.

        对单条来源记录执行提取、对账与提交，返回 (created, updated)。
        LLM 失败时抛出异常，由调用方决定是否标记为已处理。
        """
        if self._filter_record(source.content):
            return 0, 0

        existing_context = self._build_existing_context(source)
        extracted = await self._extract_candidates(source.agent_id, source)
        reconciled = await self._reconcile_candidates(extracted, existing_context)

        created = 0
        updated = 0
        for update in reconciled.profile_updates:
            c, u = self._reconcile_profile_item(source, update, source.source_id)
            created += c
            updated += u

        for fact in reconciled.facts:
            c, u = self._reconcile_fact_item(source, fact, source.source_id)
            created += c
            updated += u

        for event in reconciled.events:
            created += self._commit_event(source, event, source.source_id)

        created += await self._commit_summary(
            source,
            reconciled.long_term_summary,
            source.source_id,
            embedding_service,
        )

        for conflict in reconciled.conflict_candidates:
            self.session.add(
                MemsL2ConflictLog(
                    **self._identity_payload(source),
                    memory_type=conflict.memory_type,
                    old_value=conflict.old,
                    new_value=conflict.new,
                    resolution="reviewed",
                    reason=conflict.reason,
                    source_l1_ids=[source.source_id],
                )
            )
            created += 1

        return created, updated

    async def distill(
        self,
        agent_id: str,
        batch_size: int = 10,
        force: bool = False,
        embedding_service: Optional[EmbeddingProvider] = None,
    ) -> DistillResponse:
        """Distill a batch of L1 records into structured L2 memories.

        将一批 L1 记录蒸馏为结构化的 L2 记忆。
        """
        query = select(MemsL1Episodic).where(MemsL1Episodic.agent_id == agent_id)
        if not force:
            query = query.where(
                MemsL1Episodic.is_distilled == False,  # noqa: E712
                MemsL1Episodic.importance_score >= 0.5,
            )
            query = query.order_by(
                MemsL1Episodic.importance_score.desc(),  # type: ignore[attr-defined]
                MemsL1Episodic.created_at.asc(),  # type: ignore[attr-defined]
            )
        else:
            query = query.order_by(
                MemsL1Episodic.created_at.asc()  # type: ignore[attr-defined]
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

        for l1 in l1_records:
            if l1.id is None:
                continue

            source = DistillSource.from_l1(l1)
            try:
                local_created, local_updated = await self.process_source(
                    source, embedding_service
                )
            except Exception as exc:
                logger.error(f"LLM Error for L1 id={l1.id}: {exc}")
                continue

            l1.is_distilled = True
            self.session.add(l1)

            created_count += local_created
            updated_count += local_updated
            distilled_count += 1

        self.session.commit()

        return DistillResponse(
            success=True,
            distilled_count=distilled_count,
            l2_created=created_count,
            l2_updated=updated_count,
            message=f"Distilled {distilled_count} L1 records",
        )


def check_distill_threshold() -> int:
    """Count pending L1 records that are eligible for distillation.

    统计达到蒸馏条件、尚未处理的 L1 记录数量。
    """
    with Session(engine) as session:
        records = session.exec(
            select(MemsL1Episodic).where(
                MemsL1Episodic.is_distilled == False,  # noqa: E712
                MemsL1Episodic.importance_score >= 0.5,
            )
        ).all()
        return len(records)


async def _run_distill_if_idle(work) -> Dict[str, Any]:
    """Run a distillation work item, skipping when one is already running.

    若已有蒸馏正在运行则直接跳过，否则执行给定的蒸馏工作项。
    """
    global _distill_in_progress
    if _distill_in_progress:
        return {
            "triggered": False,
            "reason": "distill already running",
            "processed": 0,
        }
    _distill_in_progress = True
    try:
        return await work()
    finally:
        _distill_in_progress = False


async def trigger_distill_if_due() -> Dict[str, Any]:
    """Trigger distillation when the pending threshold is reached.

    当未蒸馏记录达到阈值时触发蒸馏。该函数由写入路径以后台任务方式调用，
    无定时任务依赖；若已有蒸馏正在运行则直接跳过。
    """
    return await _run_distill_if_idle(trigger_distill_automatically)


async def distill_high_value_if_idle(agent_id: str) -> Dict[str, Any]:
    """Immediately distill the top-priority pending record for one agent.

    对指定 agent 立即蒸馏一条最高优先级的未蒸馏记录（batch 由
    DISTILL_IMMEDIATE_BATCH_SIZE 控制，配合批内排序优先处理高价值记录）。
    """

    async def work() -> Dict[str, Any]:
        try:
            from mems.services.embedding import get_embedding_service

            embedding_service = await get_embedding_service()
            with Session(engine) as session:
                result = await DistillService(session).distill(
                    agent_id=agent_id,
                    batch_size=settings.DISTILL_IMMEDIATE_BATCH_SIZE,
                    force=False,
                    embedding_service=embedding_service,
                )
            logger.info(
                f"High-value distill for {agent_id}: "
                f"processed {result.distilled_count} records"
            )
            return {
                "triggered": True,
                "agent_id": agent_id,
                "processed": result.distilled_count,
            }
        except Exception as exc:
            logger.error(f"High-value distill failed for {agent_id}: {exc}")
            return {"triggered": False, "error": str(exc), "processed": 0}

    return await _run_distill_if_idle(work)


async def trigger_distill_automatically(
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run scheduled distillation for one agent or across all agents.

    为单个或全部 agent 执行调度触发的蒸馏任务。
    """
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
