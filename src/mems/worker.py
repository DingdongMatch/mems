"""Standalone outbox worker entrypoint: ``python -m mems.worker``."""

from __future__ import annotations

import asyncio
import inspect
import logging
import signal

from mems.config import settings
from mems.infrastructure.external.embedding import close_embedding_service
from mems.infrastructure.external.llm_client import close_llm_client
from mems.infrastructure.external.observability import configure_logging
from mems.infrastructure.external.redis_service import close_redis_service
from mems.infrastructure.external.vector_service import close_vector_service
from mems.infrastructure.persistence.database import engine, init_db
from mems.interfaces.bootstrap import build_outbox_worker


logger = logging.getLogger(__name__)


async def _cleanup(name: str, callback) -> None:
    try:
        result = callback()
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception("Failed to close %s during worker shutdown", name)


async def _run() -> None:
    worker_task = None
    stop_task = None
    installed_signals: list[signal.Signals] = []
    try:
        init_db()
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
                installed_signals.append(signum)
            except (NotImplementedError, RuntimeError):
                logger.debug("Signal handler is unavailable for %s", signum)

        worker = build_outbox_worker()
        worker_task = asyncio.create_task(
            worker.run_forever(poll_interval=settings.WORKER_POLL_SECONDS)
        )
        stop_task = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            (worker_task, stop_task), return_when=asyncio.FIRST_COMPLETED
        )
        if worker_task in done:
            await worker_task
    finally:
        tasks = [task for task in (worker_task, stop_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for signum in installed_signals:
            asyncio.get_running_loop().remove_signal_handler(signum)
        await _cleanup("Redis", close_redis_service)
        await _cleanup("vector store", close_vector_service)
        await _cleanup("embedding provider", close_embedding_service)
        await _cleanup("LLM client", close_llm_client)
        await _cleanup("database engine", engine.dispose)


def main() -> None:
    configure_logging(settings.LOG_LEVEL)
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
