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
        """Bind the archive service to a database session.

        将归档服务绑定到一个数据库会话。
        """
        self.session = session

    async def archive(
        self,
        agent_id: str,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        scope: Optional[str] = None,
        days: int = 30,
    ) -> ArchiveResponse:
        """Archive expired L1 records into an L3 JSONL batch.

        将过期的 L1 记录归档到一批 L3 JSONL 文件中。
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

        query = select(MemsL1Episodic).where(
            MemsL1Episodic.agent_id == agent_id,
            MemsL1Episodic.created_at < cutoff_date,
            MemsL1Episodic.is_archived == False,  # noqa: E712
        )
        if tenant_id is not None:
            query = query.where(MemsL1Episodic.tenant_id == tenant_id)
        if user_id is not None:
            query = query.where(MemsL1Episodic.user_id == user_id)
        if scope is not None:
            query = query.where(MemsL1Episodic.scope == scope)

        l1_records = self.session.exec(query).all()

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
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        scope_suffix = scope or "default"
        user_suffix = user_id or "all_users"
        tenant_suffix = tenant_id or "default"
        filename = f"l3_{agent_id}_{tenant_suffix}_{user_suffix}_{scope_suffix}_{time_period}_{batch_id}.jsonl"
        filepath = archive_dir / filename
        temp_filepath = archive_dir / f".{filename}.tmp"

        records_to_archive = []
        for record in l1_records:
            records_to_archive.append(
                {
                    "id": record.id,
                    "tenant_id": record.tenant_id,
                    "user_id": record.user_id,
                    "agent_id": record.agent_id,
                    "session_id": record.session_id,
                    "scope": record.scope,
                    "content": record.content,
                    "importance_score": record.importance_score,
                    "is_distilled": record.is_distilled,
                    "is_archived": True,
                    "metadata": record.metadata_json,
                    "created_at": record.created_at.isoformat(),
                }
            )

        try:
            with open(temp_filepath, "w", encoding="utf-8") as f:
                for record in records_to_archive:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            temp_filepath.replace(filepath)
        except Exception as exc:
            for record in l1_records:
                record.archive_status = "failed"
                record.last_sync_error = f"archive: {exc}"
                record.last_sync_at = datetime.now(timezone.utc)
                self.session.add(record)
            self.session.commit()
            temp_filepath.unlink(missing_ok=True)
            raise

        summary = f"Archived {len(records_to_archive)} records from {agent_id}"

        l3_archive = MemsL3Archive(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            scope=scope,
            time_period=time_period,
            summary_text=summary,
            file_path=str(filepath),
            record_count=len(records_to_archive),
        )
        self.session.add(l3_archive)

        for record in l1_records:
            record.is_archived = True
            record.archive_status = "ready"
            record.last_sync_error = None
            record.last_sync_at = datetime.now(timezone.utc)
            self.session.add(record)

        self.session.commit()

        return ArchiveResponse(
            success=True,
            archived_count=len(records_to_archive),
            file_path=str(filepath),
            message=f"Archived {len(records_to_archive)} records",
        )


async def trigger_archive_automatically(
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run scheduled archive work for one agent or all agents.

    为单个或全部 agent 执行调度触发的归档任务。
    """
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
                    select(
                        MemsL1Episodic.tenant_id,
                        MemsL1Episodic.user_id,
                        MemsL1Episodic.agent_id,
                        MemsL1Episodic.scope,
                    )
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
            for tenant_id, user_id, aid, scope in agent_ids:
                if not aid:
                    continue
                with Session(engine) as session:
                    archive_service = ArchiveService(session)
                    result = await archive_service.archive(
                        agent_id=aid,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        scope=scope,
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
