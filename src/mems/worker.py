"""Standalone outbox worker entrypoint: ``python -m mems.worker``."""

from __future__ import annotations

import asyncio

from mems.config import settings
from mems.infrastructure.persistence.database import init_db
from mems.interfaces.bootstrap import build_outbox_worker


async def _run() -> None:
    init_db()
    worker = build_outbox_worker()
    await worker.run_forever(poll_interval=settings.WORKER_POLL_SECONDS)


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
