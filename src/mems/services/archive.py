import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional
from sqlmodel import Session, select

from mems.config import settings
from mems.models import MemsL1Episodic, MemsL3Archive
from mems.schemas import ArchiveResponse

logger = logging.getLogger(__name__)


class ArchiveService:
    """归档服务 - L1 → L3"""

    def __init__(self, session: Session):
        self.session = session

    async def archive(
        self,
        agent_id: str,
        days: int = 30,
    ) -> ArchiveResponse:
        """执行归档"""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

        l1_records = self.session.exec(
            select(MemsL1Episodic).where(
                MemsL1Episodic.agent_id == agent_id,
                MemsL1Episodic.created_at < cutoff_date,
                MemsL1Episodic.is_archived == False,  # noqa: E712
            )
        ).all()

        if not l1_records:
            return ArchiveResponse(
                success=True,
                archived_count=0,
                file_path="",
                message="No records to archive",
            )

        archive_dir = Path(settings.storage_l3_path)
        archive_dir.mkdir(parents=True, exist_ok=True)

        time_period = f"{datetime.now(timezone.utc).strftime('%Y_%m')}_Monthly"
        filename = f"l3_{agent_id}_{time_period}.jsonl"
        filepath = archive_dir / filename
        temp_filepath = archive_dir / f".{filename}.tmp"

        records_to_archive = []
        for record in l1_records:
            records_to_archive.append(
                {
                    "id": record.id,
                    "agent_id": record.agent_id,
                    "session_id": record.session_id,
                    "content": record.content,
                    "importance_score": record.importance_score,
                    "is_distilled": record.is_distilled,
                    "is_archived": True,
                    "metadata": record.metadata_json,
                    "created_at": record.created_at.isoformat(),
                }
            )

        with open(temp_filepath, "w", encoding="utf-8") as f:
            for record in records_to_archive:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        summary = f"Archived {len(records_to_archive)} records from {agent_id}"

        l3_archive = MemsL3Archive(
            agent_id=agent_id,
            time_period=time_period,
            summary_text=summary,
            file_path=str(filepath),
            record_count=len(records_to_archive),
        )
        self.session.add(l3_archive)

        for record in l1_records:
            record.is_archived = True
            self.session.add(record)

        self.session.commit()
        with open(filepath, "a", encoding="utf-8") as target, open(
            temp_filepath,
            "r",
            encoding="utf-8",
        ) as source:
            target.write(source.read())
        temp_filepath.unlink(missing_ok=True)

        return ArchiveResponse(
            success=True,
            archived_count=len(records_to_archive),
            file_path=str(filepath),
            message=f"Archived {len(records_to_archive)} records",
        )


async def trigger_archive_automatically(
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """自动触发归档任务（供调度器调用）"""
    import logging
    from mems.database import engine

    logger = logging.getLogger(__name__)

    try:
        if agent_id is None:
            # 检查所有 agent
            with Session(engine) as session:
                # 获取所有有超过 N 天且尚未归档的 L1 记录的 agent
                cutoff_date = datetime.now(timezone.utc) - timedelta(
                    days=settings.ARCHIVE_DAYS
                )
                agent_ids = session.exec(
                    select(MemsL1Episodic.agent_id)
                    .where(
                        MemsL1Episodic.created_at < cutoff_date,
                        MemsL1Episodic.is_archived == False,  # noqa: E712
                    )
                    .distinct()
                ).all()

            if not agent_ids:
                logger.info("No records to archive")
                return {
                    "triggered": False,
                    "reason": "no expired records",
                    "total_archived": 0,
                }

            total_archived = 0
            for aid in agent_ids:
                if not aid:
                    continue
                with Session(engine) as session:
                    archive_service = ArchiveService(session)
                    result = await archive_service.archive(
                        agent_id=aid,
                        days=settings.ARCHIVE_DAYS,
                    )
                    total_archived += result.archived_count

            logger.info(f"Auto archive triggered: archived {total_archived} records")
            return {
                "triggered": True,
                "reason": "scheduled",
                "total_archived": total_archived,
            }
        else:
            # 针对特定 agent 触发
            with Session(engine) as session:
                archive_service = ArchiveService(session)
                result = await archive_service.archive(
                    agent_id=agent_id,
                    days=settings.ARCHIVE_DAYS,
                )

            logger.info(
                f"Auto archive for {agent_id}: archived {result.archived_count} records"
            )
            return {
                "triggered": True,
                "agent_id": agent_id,
                "archived": result.archived_count,
            }
    except Exception as e:
        logger.error(f"Auto archive failed: {e}")
        return {
            "triggered": False,
            "error": str(e),
            "total_archived": 0,
        }
