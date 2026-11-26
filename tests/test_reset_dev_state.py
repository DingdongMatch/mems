from __future__ import annotations

import pytest
from pydantic import SecretStr

from scripts import reset_dev_state as reset


def test_reset_dry_run_never_executes_steps(monkeypatch, capsys):
    monkeypatch.setattr(reset.settings, "APP_ENV", "development")
    monkeypatch.setattr(
        reset.settings, "DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/mems"
    )

    def unexpected_step():
        raise AssertionError("dry run executed a destructive step")

    monkeypatch.setattr(reset, "reset_database", unexpected_step)
    monkeypatch.setattr(reset, "reset_redis", unexpected_step)
    monkeypatch.setattr(reset, "reset_qdrant", unexpected_step)
    monkeypatch.setattr(reset, "reset_storage", unexpected_step)
    monkeypatch.setattr(reset, "init_db", unexpected_step)

    assert reset.main([]) == 0
    output = capsys.readouterr().out
    assert "localhost:5432/mems" in output
    assert "Dry run only" in output


def test_reset_refuses_production(monkeypatch, capsys):
    monkeypatch.setattr(reset.settings, "APP_ENV", "production")
    assert reset.main(["--yes"]) == 2
    assert "APP_ENV" in capsys.readouterr().err


def test_reset_test_database_requires_test_environment(monkeypatch):
    monkeypatch.setattr(
        reset.settings,
        "DATABASE_URL",
        "postgresql+psycopg://u:p@localhost:5432/mems_test",
    )
    monkeypatch.setattr(reset.settings, "APP_ENV", "development")
    with pytest.raises(ValueError, match="APP_ENV=test"):
        reset.validate_environment()

    monkeypatch.setattr(reset.settings, "APP_ENV", "test")
    reset.validate_environment()


def test_reset_test_environment_rejects_non_test_database(monkeypatch):
    monkeypatch.setattr(
        reset.settings,
        "DATABASE_URL",
        "postgresql+psycopg://u:p@localhost:5432/mems",
    )
    monkeypatch.setattr(reset.settings, "APP_ENV", "test")
    with pytest.raises(ValueError, match="ending with _test"):
        reset.validate_environment()


def test_reset_qdrant_url_takes_precedence(monkeypatch):
    monkeypatch.setattr(reset.settings, "QDRANT_URL", "http://qdrant.remote:7333")
    monkeypatch.setattr(reset.settings, "QDRANT_HOST", "ignored")
    monkeypatch.setattr(reset.settings, "QDRANT_PORT", 1)
    assert reset.qdrant_base_url() == "http://qdrant.remote:7333"


def test_reset_accepts_secret_str_credentials(monkeypatch):
    captured: dict[str, object] = {}

    class FakeRedis:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def flushdb(self):
            captured["flushed"] = True

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(reset.redis, "Redis", FakeRedis)
    monkeypatch.setattr(reset.settings, "REDIS_PASSWORD", SecretStr("redis-secret"))

    reset.reset_redis()
    assert captured["password"] == "redis-secret"
    assert captured["flushed"] is True
    assert captured["closed"] is True


def test_reset_yes_runs_steps_in_order(monkeypatch):
    monkeypatch.setattr(reset.settings, "APP_ENV", "development")
    monkeypatch.setattr(
        reset.settings, "DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/mems"
    )
    calls: list[str] = []
    for name in ("reset_database", "reset_redis", "reset_qdrant", "reset_storage", "init_db"):
        monkeypatch.setattr(reset, name, lambda name=name: calls.append(name))

    assert reset.main(["--yes"]) == 0
    assert calls == [
        "reset_database",
        "reset_redis",
        "reset_qdrant",
        "reset_storage",
        "init_db",
    ]
