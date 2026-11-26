#!/usr/bin/env python3
"""Reset development state after an explicit confirmation."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Callable

import httpx
import redis
from pydantic import SecretStr
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mems.config import settings
from mems.infrastructure.persistence.database import init_db


ROOT = Path(__file__).parent.parent


def _secret_text(value: SecretStr | str) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def reset_database() -> None:
    """Drop all PostgreSQL tables for a clean dev reset."""
    import mems.infrastructure.persistence.models  # noqa: F401
    from sqlmodel import SQLModel

    from mems.infrastructure.persistence.database import engine

    SQLModel.metadata.drop_all(engine)


def reset_storage() -> None:
    path = Path(settings.storage_l3_path)
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
        password=_secret_text(settings.REDIS_PASSWORD) or None,
        decode_responses=True,
    )
    try:
        client.flushdb()
    finally:
        client.close()


def qdrant_base_url() -> str:
    if settings.QDRANT_URL:
        return settings.QDRANT_URL.rstrip("/")
    scheme = "https" if settings.QDRANT_HTTPS else "http"
    return f"{scheme}://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}"


def reset_qdrant() -> None:
    base_url = qdrant_base_url()
    api_key = _secret_text(settings.QDRANT_API_KEY)
    headers = {"api-key": api_key} if api_key else {}

    with httpx.Client(timeout=settings.QDRANT_TIMEOUT, headers=headers) as client:
        response = client.get(f"{base_url}/collections")
        response.raise_for_status()
        collections = response.json().get("result", {}).get("collections", [])
        for collection in collections:
            client.delete(
                f"{base_url}/collections/{collection['name']}"
            ).raise_for_status()


def reset_targets() -> dict[str, str]:
    url = make_url(settings.DATABASE_URL)
    return {
        "PostgreSQL": f"{url.host}:{url.port or 5432}/{url.database}",
        "Redis": f"{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
        "Qdrant": qdrant_base_url(),
        "L3": str(Path(settings.storage_l3_path)),
    }


def validate_environment() -> None:
    app_env = getattr(settings, "APP_ENV", "development").lower()
    if app_env not in {"development", "test"}:
        raise ValueError("APP_ENV must be development or test")
    database = make_url(settings.DATABASE_URL).database or ""
    if app_env == "test" and not database.endswith("_test"):
        raise ValueError("APP_ENV=test requires a database name ending with _test")
    if database.endswith("_test") and app_env != "test":
        raise ValueError("Resetting a _test database requires APP_ENV=test")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes", action="store_true", help="confirm destructive reset"
    )
    args = parser.parse_args(argv)

    try:
        validate_environment()
    except ValueError as exc:
        print(f"Refusing reset: {exc}", file=sys.stderr)
        return 2

    print("Reset targets:")
    for name, target in reset_targets().items():
        print(f"  {name}: {target}")
    if not args.yes:
        print("Dry run only. Re-run with --yes to execute.")
        return 0

    steps: list[tuple[str, Callable[[], None]]] = [
        ("PostgreSQL", reset_database),
        ("Redis", reset_redis),
        ("Qdrant", reset_qdrant),
        ("L3", reset_storage),
    ]
    errors: list[str] = []
    database_ok = False
    for name, step in steps:
        try:
            step()
            database_ok = database_ok or name == "PostgreSQL"
            print(f"Cleared {name}.")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            print(f"Failed to clear {name}: {exc}", file=sys.stderr)

    if database_ok:
        try:
            init_db()
            print("Reinitialized PostgreSQL schema.")
        except Exception as exc:
            errors.append(f"init_db: {exc}")
            print(f"Failed to initialize schema: {exc}", file=sys.stderr)

    if errors:
        print("Reset completed with errors:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print("Development state reset complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
