import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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
        """启动调度器"""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started")

    def shutdown(self):
        """停止调度器"""
        if self._scheduler.running:
            self._scheduler.shutdown()
            logger.info("Scheduler stopped")

    def add_distill_job(self, func, hour: int = 2, minute: int = 0):
        """添加蒸馏定时任务"""
        self._scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=minute),
            id="distill_job",
            name="Memory Distillation",
            replace_existing=True,
        )
        logger.info(f"Distill job scheduled at {hour:02d}:{minute:02d}")

    def add_archive_job(self, func, hour: int = 3, minute: int = 0):
        """添加归档定时任务"""
        self._scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=minute),
            id="archive_job",
            name="Memory Archive",
            replace_existing=True,
        )
        logger.info(f"Archive job scheduled at {hour:02d}:{minute:02d}")

    def add_interval_job(self, func, minutes: int = 60, id: str = None):
        """添加间隔任务"""
        self._scheduler.add_job(
            func,
            "interval",
            minutes=minutes,
            id=id,
            name=id,
            replace_existing=True,
        )
        logger.info(f"Interval job '{id}' scheduled every {minutes} minutes")


scheduler_service = SchedulerService()


def get_scheduler() -> SchedulerService:
    return scheduler_service