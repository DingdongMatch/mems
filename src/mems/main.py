import logging
from pathlib import Path
from fastapi import FastAPI
from contextlib import asynccontextmanager

from mems.config import settings
from mems.database import init_db
from mems.routers import memories, monitor, simulator
from mems.services.scheduler import scheduler_service


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    Path(settings.storage_l1_path).mkdir(parents=True, exist_ok=True)
    Path(settings.storage_l2_path).mkdir(parents=True, exist_ok=True)
    Path(settings.storage_l3_path).mkdir(parents=True, exist_ok=True)

    if settings.SCHEDULER_ENABLED:
        from mems.services.distill import trigger_distill_automatically
        from mems.services.archive import trigger_archive_automatically

        scheduler_service.add_distill_job(
            trigger_distill_automatically,
            hour=settings.DISTILL_CRON_HOUR,
            minute=settings.DISTILL_CRON_MINUTE,
        )
        scheduler_service.add_archive_job(
            trigger_archive_automatically,
            hour=settings.ARCHIVE_CRON_HOUR,
            minute=settings.ARCHIVE_CRON_MINUTE,
        )
        scheduler_service.start()
        logger.info("Scheduler started with distill and archive jobs")

    yield

    if settings.SCHEDULER_ENABLED:
        scheduler_service.shutdown()


app = FastAPI(
    title="Mems - 分层记忆系统",
    description="支持多 Agent 隔离、长周期的分层记忆系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(memories.router)
app.include_router(monitor.router)
app.include_router(simulator.router)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn
    from pathlib import Path

    Path("storage/l1_raw").mkdir(parents=True, exist_ok=True)
    Path("storage/l2_knowledge").mkdir(parents=True, exist_ok=True)
    Path("storage/l3_archive").mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "mems.main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=True
    )
