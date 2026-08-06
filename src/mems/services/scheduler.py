import logging
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
        """Enforce a process-wide scheduler singleton.

        确保调度器在进程内保持单例。
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the underlying AsyncIO scheduler once.

        仅初始化一次底层 AsyncIO 调度器。
        """
        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler()

    @property
    def scheduler(self) -> AsyncIOScheduler:
        """Expose the underlying APScheduler instance.

        暴露底层 APScheduler 实例。
        """
        return self._scheduler

    def start(self):
        """Start the scheduler if it is not already running.

        如果调度器尚未运行，则启动它。
        """
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started")

    def shutdown(self):
        """Stop the scheduler if it is currently running.

        如果调度器正在运行，则停止它。
        """
        if self._scheduler.running:
            self._scheduler.shutdown()
            logger.info("Scheduler stopped")

    def add_archive_job(self, func, hour: int = 3, minute: int = 0):
        """Schedule the daily archive job.

        注册每日归档定时任务。
        """
        self._scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=minute),
            id="archive_job",
            name="Memory Archive",
            replace_existing=True,
        )
        logger.info(f"Archive job scheduled at {hour:02d}:{minute:02d}")

scheduler_service = SchedulerService()


def get_scheduler() -> SchedulerService:
    """Return the module-level scheduler singleton.

    返回模块级调度器单例。
    """
    return scheduler_service
