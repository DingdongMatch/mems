import json
import re
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlmodel import Session, select, func

from mems.config import settings
from mems.database import engine
from mems.models import MemsL1Episodic, MemsL2Semantic
from mems.schemas import DistillResponse
from mems.services.embedding import EmbeddingProvider
from mems.services.jsonl_utils import JsonlWriter
from mems.services.llm_client import chat as llm_chat


DISTRICT_PROMPT = """你是一个知识提取专家。请从以下对话内容中提取实体关系三元组。

要求：
1. 提取形式为 (主体, 关系, 客体) 的三元组
2. 关系类型包括：喜欢、讨厌、擅长、不擅长、认识、是、属于、相关等
3. 只提取明确的信息，不要过度推断
4. 如果没有有价值的信息，返回空数组

对话内容：
{content}

请以 JSON 数组格式返回，格式如下：
[
  {{"subject": "主体", "predicate": "关系", "object": "客体", "confidence": 0.9}},
  ...
]
"""


class DistillService:
    """记忆蒸馏服务 - L1 → L2"""

    def __init__(self, session: Session):
        self.session = session

    def _has_llm_client(self) -> bool:
        from mems.config import settings
        return bool(settings.OPENAI_API_KEY)

    async def distill(
        self,
        agent_id: str,
        batch_size: int = 10,
        force: bool = False,
        embedding_service: Optional[EmbeddingProvider] = None,
    ) -> DistillResponse:
        """执行蒸馏"""
        query = select(MemsL1Episodic).where(
            MemsL1Episodic.agent_id == agent_id,
            MemsL1Episodic.is_distilled == False,
        )
        if not force:
            query = query.where(MemsL1Episodic.importance_score >= 0.5)

        l1_records = self.session.exec(query.limit(batch_size)).all()

        if not l1_records:
            return DistillResponse(
                success=True,
                distilled_count=0,
                l2_created=0,
                l2_updated=0,
                message="No records to distill",
            )

        has_llm = self._has_llm_client()
        l2_created = 0
        l2_updated = 0

        for l1 in l1_records:
            content = l1.content

            if has_llm:
                try:
                    response = await llm_chat(
                        messages=[
                            {"role": "user", "content": DISTRICT_PROMPT.format(content=content)}
                        ]
                    )
                    
                    if not response:
                        triples = []
                    else:
                        json_match = re.search(r"\[[\s\S]*\]", response)
                        if json_match:
                            json_str = json_match.group()
                            if json_str.startswith("```"):
                                json_str = json_str.split("```")[1]
                                if json_str.startswith("json"):
                                    json_str = json_str[4:]
                            triples = json.loads(json_str.strip())
                        else:
                            triples = []
                except Exception as e:
                    logger.error(f"LLM Error for L1 id={l1.id}: {e}")
                    triples = []
            else:
                triples = []

            if triples:
                for triple in triples:
                    existing = self.session.exec(
                        select(MemsL2Semantic).where(
                            MemsL2Semantic.agent_id == agent_id,
                            MemsL2Semantic.subject == triple.get("subject"),
                            MemsL2Semantic.predicate == triple.get("predicate"),
                            MemsL2Semantic.object == triple.get("object"),
                            MemsL2Semantic.is_active == True,
                        )
                    ).first()

                    if existing:
                        existing.is_active = False
                        existing.version += 1
                        self.session.add(existing)

                        new_l2 = MemsL2Semantic(
                            agent_id=agent_id,
                            subject=triple.get("subject"),
                            predicate=triple.get("predicate"),
                            object=triple.get("object"),
                            confidence=triple.get("confidence", 0.8),
                            source_ids=[l1.id],
                            version=existing.version + 1,
                        )
                        self.session.add(new_l2)
                        l2_updated += 1
                    else:
                        new_l2 = MemsL2Semantic(
                            agent_id=agent_id,
                            subject=triple.get("subject"),
                            predicate=triple.get("predicate"),
                            object=triple.get("object"),
                            confidence=triple.get("confidence", 0.8),
                            source_ids=[l1.id],
                        )
                        self.session.add(new_l2)
                        l2_created += 1

            l1.is_distilled = True
            self.session.add(l1)

        self.session.commit()

        l2_writer = JsonlWriter(settings.storage_l2_path, "l2")
        l2_writer.write(
            agent_id,
            {
                "distilled_at": datetime.utcnow().isoformat(),
                "l1_count": len(l1_records),
                "l2_created": l2_created,
                "l2_updated": l2_updated,
            },
        )

        return DistillResponse(
            success=True,
            distilled_count=len(l1_records),
            l2_created=l2_created,
            l2_updated=l2_updated,
            message=f"Distilled {len(l1_records)} L1 records",
        )


def check_distill_threshold() -> int:
    """检查是否达到蒸馏阈值，返回需要蒸馏的记录数"""
    from mems.database import engine
    from sqlmodel import func
    
    with Session(engine) as session:
        count = session.exec(
            select(func.count(MemsL1Episodic.id)).where(
                MemsL1Episodic.is_distilled == False,
                MemsL1Episodic.importance_score >= 0.5,
            )
        ).one()
        return count


async def trigger_distill_automatically(agent_id: str = None) -> Dict[str, Any]:
    """自动触发蒸馏任务（供调度器调用）"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        from mems.services.embedding import get_embedding_service
        
        # 如果没有指定 agent_id，检查所有 agent
        if agent_id is None:
            threshold = settings.DISTILL_THRESHOLD
            current_count = check_distill_threshold()
            
            if current_count < threshold:
                logger.info(f"Distill threshold not reached: {current_count}/{threshold}")
                return {
                    "triggered": False,
                    "reason": f"threshold not reached ({current_count}/{threshold})",
                    "processed": 0,
                }
            
            # 获取所有有未蒸馏记录的 agent
            with Session(engine) as session:
                agent_ids = session.exec(
                    select(MemsL1Episodic.agent_id).where(
                        MemsL1Episodic.is_distilled == False
                    ).distinct()
                ).all()
            
            total_processed = 0
            for aid in agent_ids:
                embedding_service = await get_embedding_service()
                with Session(engine) as session:
                    distill_service = DistillService(session)
                    result = await distill_service.distill(
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
        else:
            # 针对特定 agent 触发
            embedding_service = await get_embedding_service()
            with Session(engine) as session:
                distill_service = DistillService(session)
                result = await distill_service.distill(
                    agent_id=agent_id,
                    batch_size=settings.DISTILL_BATCH_SIZE,
                    force=True,
                    embedding_service=embedding_service,
                )
            
            logger.info(f"Auto distill for {agent_id}: processed {result.distilled_count} records")
            return {
                "triggered": True,
                "agent_id": agent_id,
                "processed": result.distilled_count,
            }
    except Exception as e:
        logger.error(f"Auto distill failed: {e}")
        return {
            "triggered": False,
            "error": str(e),
            "processed": 0,
        }