"""Background scheduler adapter."""

from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.asyncio import (  # type: ignore[import-untyped]
    AsyncIOScheduler,
)
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class SchedulerService:
    """后台调度服务 - 管理定时任务"""

    _instance = None
    _scheduler: AsyncIOScheduler = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler()

    @property
    def scheduler(self) -> AsyncIOScheduler:
        return self._scheduler

    def start(self):
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started")

    def shutdown(self):
        if self._scheduler.running:
            self._scheduler.shutdown()
            logger.info("Scheduler stopped")

    def add_archive_job(self, func: Any, hour: int = 3, minute: int = 0):
        self._scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=minute),
            id="archive_job",
            name="Memory Archive",
            replace_existing=True,
        )
        logger.info(f"Archive job scheduled at {hour:02d}:{minute:02d}")

    def is_running(self) -> bool:
        return bool(self._scheduler.running)

    def has_archive_job(self) -> bool:
        return self._scheduler.get_job("archive_job") is not None


scheduler_service = SchedulerService()


def get_scheduler() -> SchedulerService:
    return scheduler_service
