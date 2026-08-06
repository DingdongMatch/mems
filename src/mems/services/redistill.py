import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlmodel import Session, select

from mems.config import settings
from mems.database import engine
from mems.models import MemsL3Archive, MemsRedistillProgress
from mems.schemas import RedistillResponse
from mems.services.distill import DistillService, DistillSource
from mems.services.embedding import get_embedding_service
from mems.services.jsonl_utils import JsonlReader


logger = logging.getLogger(__name__)


def content_fingerprint(record: Dict[str, Any]) -> str:
    """Stable fingerprint of identity + content + messages.

    身份 + 内容 + 消息的稳定指纹，用于再蒸馏去重。
    """
    canonical = json.dumps(
        {
            "tenant_id": record.get("tenant_id"),
            "user_id": record.get("user_id"),
            "agent_id": record.get("agent_id"),
            "scope": record.get("scope"),
            "content": record.get("content"),
            "messages": record.get("messages"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """Normalize naive datetimes to UTC for safe comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _record_created_at(record: Dict[str, Any]) -> datetime:
    raw = record.get("created_at")
    if isinstance(raw, str):
        try:
            return _as_utc(datetime.fromisoformat(raw))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


async def trigger_redistill(
    agent_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    min_importance: float = 0.0,
    batch_size: Optional[int] = None,
) -> RedistillResponse:
    """Re-distill archived L3 records into upgraded L2 with the current model.

    用当前模型对 L3 归档记录执行再蒸馏，升级 L2。按「内容指纹 + 模型名」去重，
    模型更换后旧记录会自动进入待重蒸队列。
    """
    batch_size = batch_size or settings.REDISTILL_BATCH_SIZE
    model = settings.OPENAI_MODEL
    start_date = _as_utc(start_date) if start_date else None
    end_date = _as_utc(end_date) if end_date else None

    try:
        with Session(engine) as session:
            query = select(MemsL3Archive)
            if agent_id:
                query = query.where(MemsL3Archive.agent_id == agent_id)
            archives = session.exec(query).all()

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
        embedding_service = await get_embedding_service()

        processed = created = updated = skipped = 0
        with Session(engine) as session:
            service = DistillService(session)
            for archive in archives:
                path = Path(archive.file_path)
                if not path.exists():
                    logger.warning(f"Archive file missing: {path}")
                    continue
                records = reader.read(path)
                for line_no, record in enumerate(records, start=1):
                    if processed >= batch_size:
                        break
                    if agent_id and record.get("agent_id") != agent_id:
                        continue
                    if (
                        min_importance > 0
                        and float(record.get("importance_score", 0.0))
                        < min_importance
                    ):
                        continue
                    record_created = _record_created_at(record)
                    if start_date and record_created < start_date:
                        continue
                    if end_date and record_created > end_date:
                        continue

                    fingerprint = content_fingerprint(record)
                    already = session.exec(
                        select(MemsRedistillProgress).where(
                            MemsRedistillProgress.content_hash == fingerprint,
                            MemsRedistillProgress.model == model,
                        )
                    ).first()
                    if already:
                        skipped += 1
                        continue

                    if not record.get("content"):
                        skipped += 1
                        continue

                    source = DistillSource.from_archive_record(
                        record, str(path), line_no
                    )
                    try:
                        created_in, updated_in = await service.process_source(
                            source, embedding_service
                        )
                    except Exception as exc:
                        logger.error(
                            f"Re-distill failed for {path}:{line_no}: {exc}"
                        )
                        continue

                    session.add(
                        MemsRedistillProgress(
                            content_hash=fingerprint,
                            model=model,
                            l3_path=str(path),
                            l3_line=line_no,
                        )
                    )
                    processed += 1
                    created += created_in
                    updated += updated_in
                if processed >= batch_size:
                    break
            session.commit()

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
        logger.error(f"Re-distill run failed: {exc}")
        return RedistillResponse(
            success=False,
            processed=0,
            created=0,
            updated=0,
            skipped=0,
            message=str(exc),
        )
