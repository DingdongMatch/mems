"""Re-distill identity-scoped L3 archives into upgraded L2 knowledge."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Iterator, Optional, Protocol

from sqlmodel import Session

from mems.application.commands import RedistillResponse
from mems.config import settings
from mems.application.distill_service import DistillApplicationService
from mems.domain.archive import ArchiveBatch
from mems.domain.identity import MemoryIdentity
from mems.domain.knowledge import DistillSource
from mems.domain.ports import (
    ArchiveRepository,
    Clock,
    RedistillProgressRepository,
)


logger = logging.getLogger(__name__)


class _AlreadyProcessed(Exception):
    pass


class RedistillRepositories(Protocol):
    """Repositories used by the re-distillation application service."""

    @property
    def archive(self) -> ArchiveRepository: ...

    @property
    def redistill(self) -> RedistillProgressRepository: ...


class ArchiveRecordReader(Protocol):
    """Streaming reader used to consume one L3 JSONL archive."""

    def iter_records(self, filepath: Path) -> Iterator[tuple[int, Dict[str, Any]]]: ...


def content_fingerprint(record: Dict[str, Any]) -> str:
    """Stable fingerprint of identity + content + messages."""
    identity = {
        "tenant_id": record.get("tenant_id"),
        "user_id": record.get("user_id"),
        "agent_id": record.get("agent_id"),
        "namespace": record.get("namespace"),
    }
    canonical = json.dumps(
        {
            **identity,
            "content": record.get("content"),
            "messages": record.get("messages"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _record_created_at(record: Dict[str, Any]) -> Optional[datetime]:
    """Parse an archive timestamp without silently substituting the current time."""
    raw = record.get("created_at")
    if isinstance(raw, datetime):
        return _as_utc(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return _as_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _record_importance(record: Dict[str, Any]) -> Optional[float]:
    raw = record.get("importance_score", 0.5)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        importance = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(importance) or not 0.0 <= importance <= 1.0:
        return None
    return importance


def _matches_identity(record: Dict[str, Any], identity: MemoryIdentity) -> bool:
    return (
        record.get("tenant_id") == identity.tenant_id
        and record.get("user_id") == identity.user_id
        and record.get("agent_id") == identity.agent_id
        and record.get("namespace") == identity.namespace_key
    )


def _matches_archive(archive: ArchiveBatch, identity: MemoryIdentity) -> bool:
    return (
        archive.tenant_id == identity.tenant_id
        and archive.user_id == identity.user_id
        and archive.agent_id == identity.agent_id
        and archive.namespace == identity.namespace_key
    )


class RedistillApplicationService:
    """Coordinate streaming archive reads, distillation, and progress writes."""

    def __init__(
        self,
        session: Session,
        repos: RedistillRepositories,
        archive_reader: ArchiveRecordReader,
        storage_path: Path,
        distill_service: DistillApplicationService,
        clock: Clock,
        model: str,
        batch_size: int,
    ) -> None:
        self._session = session
        self._repos = repos
        self._archive_reader = archive_reader
        self._storage_path = Path(storage_path).resolve()
        self._distill_service = distill_service
        self._clock = clock
        self._model = model
        self._batch_size = batch_size

    def _resolve_archive_path(self, archive: ArchiveBatch) -> Optional[Path]:
        try:
            path = Path(archive.file_path)
            if not path.is_absolute():
                path = self._storage_path / path
            path = path.resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        if not path.is_relative_to(self._storage_path) or not path.is_file():
            return None
        return path

    @staticmethod
    def _response(
        *,
        attempted: int = 0,
        failed: int = 0,
        processed: int = 0,
        created: int = 0,
        updated: int = 0,
        skipped: int = 0,
        message: str,
    ) -> RedistillResponse:
        return RedistillResponse(
            success=failed == 0,
            attempted=attempted,
            failed=failed,
            processed=processed,
            created=created,
            updated=updated,
            skipped=skipped,
            message=message,
        )

    async def run(
        self,
        identity: MemoryIdentity,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        min_importance: float = 0.0,
        batch_size: Optional[int] = None,
    ) -> RedistillResponse:
        """Process at most ``batch_size`` valid records for one exact identity."""
        started = perf_counter()
        limit = min(batch_size or self._batch_size, self._batch_size)
        start_utc = _as_utc(start) if start is not None else None
        end_utc = _as_utc(end) if end is not None else None
        processed = created = updated = skipped = attempted = failed = 0
        if settings.LLM_PROVIDER != "openai":
            return RedistillResponse(
                success=False, processed=0, created=0, updated=0, skipped=0,
                message="LLM is not configured; distillation is disabled",
            )

        try:
            archives = self._repos.archive.list(identity=identity)
            if not archives:
                return self._response(message="No archive batches found")

            for archive in archives:
                if attempted >= limit:
                    break
                if not _matches_archive(archive, identity):
                    logger.warning(
                        "Skipping archive with mismatched identity metadata",
                        extra={
                            "event": "redistill.archive_identity_mismatch",
                            "operation": "redistill",
                            "archive_id": archive.id,
                        },
                    )
                    continue

                path = self._resolve_archive_path(archive)
                if path is None:
                    logger.warning(
                        "Skipping missing or unsafe archive file",
                        extra={
                            "event": "redistill.archive_missing",
                            "operation": "redistill",
                            "archive_id": archive.id,
                        },
                    )
                    continue

                try:
                    records = self._archive_reader.iter_records(path)
                    for line_no, record in records:
                        if attempted >= limit:
                            break
                        if not _matches_identity(record, identity):
                            continue

                        created_at = _record_created_at(record)
                        importance = _record_importance(record)
                        content = record.get("content")
                        if (
                            created_at is None
                            or importance is None
                            or not isinstance(content, str)
                            or not content.strip()
                        ):
                            skipped += 1
                            continue
                        if importance < min_importance:
                            continue
                        if start_utc is not None and created_at < start_utc:
                            continue
                        if end_utc is not None and created_at > end_utc:
                            continue

                        fingerprint = content_fingerprint(record)
                        if self._repos.redistill.exists(fingerprint, self._model):
                            skipped += 1
                            continue

                        archive_path = path
                        try:
                            source = DistillSource.from_archive_record(
                                record, str(archive_path), line_no
                            )
                        except (TypeError, ValueError, KeyError):
                            skipped += 1
                            continue

                        def finalize_progress(
                            fingerprint: str = fingerprint,
                            path: Path = archive_path,
                            line_no: int = line_no,
                        ) -> None:
                            inserted = self._repos.redistill.add(
                                fingerprint,
                                self._model,
                                str(path),
                                line_no,
                            )
                            if not inserted:
                                raise _AlreadyProcessed

                        attempted += 1
                        try:
                            created_in, updated_in = await self._distill_service.process_source(
                                source, finalize=finalize_progress
                            )
                            self._session.commit()
                        except _AlreadyProcessed:
                            self._session.rollback()
                            skipped += 1
                            continue
                        except Exception as exc:
                            failed += 1
                            self._session.rollback()
                            logger.error(
                                "Re-distillation source failed",
                                exc_info=True,
                                extra={
                                    "event": "redistill.source.failed",
                                    "operation": "redistill",
                                    "source_l1_id": source.source_id,
                                    "error_type": type(exc).__name__,
                                    "result": "failed",
                                },
                            )
                            continue

                        processed += 1
                        created += created_in
                        updated += updated_in
                except OSError as exc:
                    logger.warning(
                        "Skipping unreadable archive file",
                        exc_info=True,
                        extra={
                            "event": "redistill.archive_unreadable",
                            "operation": "redistill",
                            "archive_id": archive.id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    continue

            self._session.commit()
        except Exception as exc:
            try:
                self._session.rollback()
            except Exception:
                pass
            logger.error(
                "Re-distillation run failed",
                exc_info=True,
                extra={
                    "event": "redistill.failed",
                    "operation": "redistill",
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                    "error_type": type(exc).__name__,
                    "result": "failed",
                },
            )
            return RedistillResponse(
                success=False,
                attempted=attempted,
                failed=failed,
                processed=processed,
                created=created,
                updated=updated,
                skipped=skipped,
                message="Re-distillation failed",
            )

        logger.info(
            "Re-distillation run completed",
            extra={
                "event": "redistill.completed",
                "operation": "redistill",
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "count": processed,
                "l2_created": created,
                "l2_updated": updated,
                "skipped": skipped,
                "model": self._model,
                "failed": failed,
                "attempted": attempted,
                "result": "success" if not failed else "partial_failure",
            },
        )
        return self._response(
            attempted=attempted,
            failed=failed,
            processed=processed,
            created=created,
            updated=updated,
            skipped=skipped,
            message=(
                f"Re-distilled {processed} records with {self._model}"
                f" (attempted {attempted}, failed {failed}, created {created},"
                f" updated {updated}, skipped {skipped})"
            ),
        )
