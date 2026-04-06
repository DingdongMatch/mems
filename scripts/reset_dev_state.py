#!/usr/bin/env python3
"""重置开发环境数据并重建数据库。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import httpx
import redis

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mems.config import settings
from mems.database import init_db


ROOT = Path(__file__).parent.parent


def reset_sqlite_db() -> None:
    database_url = settings.DATABASE_URL
    if database_url.startswith("sqlite:///"):
        db_path = ROOT / database_url.removeprefix("sqlite:///")
        if db_path.exists():
            db_path.unlink()


def reset_storage() -> None:
    for storage_path in [
        settings.storage_l3_path,
    ]:
        path = ROOT / storage_path
        path.mkdir(parents=True, exist_ok=True)
        for child in path.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()


def reset_redis() -> None:
    client = redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
    )
    client.flushdb()


def reset_qdrant() -> None:
    base_url = f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}"
    headers = {}
    if settings.QDRANT_API_KEY:
        headers["api-key"] = settings.QDRANT_API_KEY

    with httpx.Client(timeout=settings.QDRANT_TIMEOUT, headers=headers) as client:
        collections = (
            client.get(f"{base_url}/collections")
            .json()
            .get("result", {})
            .get("collections", [])
        )
        for collection in collections:
            client.delete(
                f"{base_url}/collections/{collection['name']}"
            ).raise_for_status()


def main() -> None:
    print("Resetting development state...")
    reset_sqlite_db()
    reset_storage()
    reset_redis()
    reset_qdrant()
    init_db()
    print("Development state reset complete.")


if __name__ == "__main__":
    main()
