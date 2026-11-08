"""Archive application service."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Optional

from mems.application.commands import ArchiveResponse
from mems.config import settings
from mems.domain.archive import ArchiveBatch
from mems.domain.ports import (
    ArchiveRepository,
    Clock,
    EpisodicRepository,
    UnitOfWork,
)


logger = logging.getLogger(__name__)


class ArchiveApplicationService:
    """Archive expired L1 records into an L3 JSONL batch."""

    def __init__(
        self,
        episodic: EpisodicRepository,
        archive: ArchiveRepository,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._episodic = episodic
        self._archive = archive
        self._clock = clock
        self._uow = uow

    async def archive(
        self,
        agent_id: str,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        namespace: Optional[str] = None,
        days: int = 30,
    ) -> ArchiveResponse:
        cutoff_date = self._clock.now() - timedelta(days=days)
        l1_records = self._episodic.list_expired(
            agent_id, tenant_id, user_id, namespace, cutoff_date
        )

        if not l1_records:
            return ArchiveResponse(
                success=True,
                archived_count=0,
                file_path="",
                message="No records to archive",
            )

        archive_dir = Path(settings.storage_l3_path)
        archive_dir.mkdir(parents=True, exist_ok=True)

        time_period = f"{self._clock.now().strftime('%Y_%m')}_Monthly"
        batch_id = self._clock.now().strftime("%Y%m%dT%H%M%S%f")
        namespace_suffix = hashlib.sha256((namespace or "default").encode()).hexdigest()[:12]
        user_suffix = hashlib.sha256((user_id or "all_users").encode()).hexdigest()[:12]
        tenant_suffix = hashlib.sha256((tenant_id or "default").encode()).hexdigest()[:12]
        agent_suffix = hashlib.sha256(agent_id.encode()).hexdigest()[:12]
        filename = (
            f"l3_{agent_suffix}_{tenant_suffix}_{user_suffix}_{namespace_suffix}_"
            f"{time_period}_{batch_id}.jsonl"
        )
        filepath = archive_dir / filename
        temp_filepath = archive_dir / f".{filename}.tmp"

        archive_entries = []
        for l1_record in l1_records:
            archive_entries.append(
                {
                    "id": l1_record.id,
                    "tenant_id": l1_record.tenant_id,
                    "user_id": l1_record.user_id,
                    "agent_id": l1_record.agent_id,
                    "session_id": l1_record.session_id,
                    "namespace": l1_record.namespace,
                    "content": l1_record.content,
                    "importance_score": l1_record.importance_score,
                    "is_distilled": l1_record.is_distilled,
                    "is_archived": True,
                    "messages": l1_record.messages,
                    "created_at": l1_record.created_at.isoformat(),
                }
            )

        try:
            with open(temp_filepath, "w", encoding="utf-8") as f:
                for entry in archive_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            temp_filepath.replace(filepath)
        except Exception as exc:
            for l1_record in l1_records:
                l1_record.mark_archive_failed(
                    f"archive: {exc}", self._clock.now()
                )
                self._episodic.save(l1_record)
            self._uow.commit()
            temp_filepath.unlink(missing_ok=True)
            raise

        summary = f"Archived {len(archive_entries)} records from {agent_id}"
        self._archive.add(
            ArchiveBatch(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                namespace=namespace,
                time_period=time_period,
                summary_text=summary,
                file_path=str(filepath),
                record_count=len(archive_entries),
                archived_at=self._clock.now(),
            )
        )

        for l1_record in l1_records:
            l1_record.mark_archived(self._clock.now())
            self._episodic.save(l1_record)

        self._uow.commit()

        return ArchiveResponse(
            success=True,
            archived_count=len(archive_entries),
            file_path=str(filepath),
            message=f"Archived {len(archive_entries)} records",
        )
