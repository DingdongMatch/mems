from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_SQLITE_DB = PROJECT_ROOT / "mems.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # Database
    DATABASE_URL: str = f"sqlite:///{DEFAULT_SQLITE_DB}"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_sqlite_database_url(cls, value: str) -> str:
        """Normalize relative SQLite URLs to absolute project paths.

        将相对 SQLite 路径规范化为项目根目录下的绝对路径。
        """
        if not isinstance(value, str):
            return value
        prefix = "sqlite:///"
        if not value.startswith(prefix) or value.startswith("sqlite:////"):
            return value
        db_path = value[len(prefix) :]
        if not db_path or Path(db_path).is_absolute():
            return value
        return f"sqlite:///{(PROJECT_ROOT / db_path).resolve()}"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # Qdrant
    QDRANT_URL: str = ""
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_GRPC_PORT: int = 6334
    QDRANT_API_KEY: str = ""
    QDRANT_TIMEOUT: int = 30
    QDRANT_HTTPS: bool = False
    QDRANT_PREFER_GRPC: bool = False

    # Embedding
    EMBEDDING_PROVIDER: Literal["sentence-transformers", "openai"] = (
        "sentence-transformers"
    )
    SENTENCE_TRANSFORMERS_MODEL: str = "BAAI/bge-small-zh-v1.5"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_API_KEY: str = ""

    # LLM
    LLM_PROVIDER: Literal["openai"] = "openai"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT: int = 300

    # App
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8210
    LOG_LEVEL: str = "INFO"

    # Archive
    ARCHIVE_DAYS: int = 30
    ARCHIVE_STORAGE_PATH: str = "storage/l3_archive"

    # Scheduler
    SCHEDULER_ENABLED: bool = True
    DISTILL_CRON_HOUR: int = 2
    DISTILL_CRON_MINUTE: int = 0
    DISTILL_THRESHOLD: int = 100
    DISTILL_BATCH_SIZE: int = 50
    ARCHIVE_CRON_HOUR: int = 3
    ARCHIVE_CRON_MINUTE: int = 0

    L0_DEFAULT_TTL_SECONDS: int = 1800

    # Storage paths
    @property
    def storage_l3_path(self) -> Path:
        """Return the configured L3 archive directory.

        返回配置后的 L3 归档目录。
        """
        return PROJECT_ROOT / self.ARCHIVE_STORAGE_PATH


settings = Settings()


def get_settings() -> Settings:
    """Expose the singleton settings object.

    返回全局单例配置对象。
    """
    return settings
