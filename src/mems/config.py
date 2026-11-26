from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # Database
    DATABASE_URL: str = "postgresql+psycopg://mems:mems@localhost:5432/mems"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: SecretStr = SecretStr("")

    # Qdrant
    QDRANT_URL: str = ""
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_GRPC_PORT: int = 6334
    QDRANT_API_KEY: SecretStr = SecretStr("")
    QDRANT_TIMEOUT: int = 30
    QDRANT_HTTPS: bool = False
    QDRANT_PREFER_GRPC: bool = False

    # Embedding
    EMBEDDING_PROVIDER: Literal["sentence-transformers", "openai"] = (
        "sentence-transformers"
    )
    SENTENCE_TRANSFORMERS_MODEL: str = "BAAI/bge-small-zh-v1.5"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_API_KEY: SecretStr = SecretStr("")
    OPENAI_EMBEDDING_BASE_URL: str = ""

    # LLM. None allows local embedding-only startup; the client validates on use.
    LLM_PROVIDER: Literal["openai"] | None = None
    OPENAI_API_KEY: SecretStr = SecretStr("")
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT: int = 300

    # App
    APP_ENV: Literal["development", "test", "production"] = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8210
    LOG_LEVEL: str = "INFO"
    # HTTP body logging is opt-in because bodies may contain memory content.
    LOG_HTTP_BODY_ENABLED: bool = False
    LOG_HTTP_BODY_MAX_CHARS: int = Field(default=2000, ge=0)
    HTTP_REQUEST_MAX_BYTES: int = Field(default=1024 * 1024, ge=1024)
    QUERY_MAX_CHARS: int = Field(default=8000, ge=1)
    MEMORY_CONTENT_MAX_CHARS: int = Field(default=32000, ge=1)
    MEMORY_MESSAGE_MAX_COUNT: int = Field(default=100, ge=1)
    MEMORY_MESSAGE_CONTENT_MAX_CHARS: int = Field(default=32000, ge=1)

    # Archive
    ARCHIVE_DAYS: int = 30
    ARCHIVE_STORAGE_PATH: str = "storage/l3_archive"

    # Scheduler
    SCHEDULER_ENABLED: bool = True
    OUTBOX_WORKER_ENABLED: bool = True
    OUTBOX_LEASE_SECONDS: int = Field(default=300, ge=60)
    OUTBOX_MAX_ATTEMPTS: int = Field(default=5, ge=1)
    WORKER_POLL_SECONDS: float = 1.0
    DISTILL_THRESHOLD: int = 100
    DISTILL_BATCH_SIZE: int = 50
    DISTILL_HIGH_IMPORTANCE_THRESHOLD: float = 0.8
    DISTILL_IMMEDIATE_BATCH_SIZE: int = 1
    # Max L3 records re-distilled per manual run.
    # 每次手动再蒸馏最多处理的 L3 记录数。
    REDISTILL_BATCH_SIZE: int = Field(default=100, ge=1, le=1000)
    ARCHIVE_CRON_HOUR: int = 3
    ARCHIVE_CRON_MINUTE: int = 0

    L0_DEFAULT_TTL_SECONDS: int = 1800
    # Max active L0 snapshots recalled per agent in a query.
    # 查询时每个 agent 最多召回的活跃 L0 快照数。
    L0_RECALL_MAX_SNAPSHOTS: int = 10

    # L0 Memory Output Pipeline (Redis Stream, disabled by default)
    # 用户可用自己的代码消费记忆事件；默认关闭，开启后每次 L0 写入/追加会发布一条事件。
    L0_PIPELINE_ENABLED: bool = False
    L0_PIPELINE_STREAM: str = "mems:l0:events"
    L0_PIPELINE_MAXLEN: int = 10000

    # Storage paths
    @property
    def storage_l3_path(self) -> Path:
        """Return the configured L3 archive directory.

        返回配置后的 L3 归档目录。
        """
        return PROJECT_ROOT / self.ARCHIVE_STORAGE_PATH

    @model_validator(mode="after")
    def validate_provider_credentials(self) -> "Settings":
        """Require credentials only for the providers selected at runtime."""
        if self.EMBEDDING_PROVIDER == "openai" and not (
            self.OPENAI_EMBEDDING_API_KEY.get_secret_value()
            or self.OPENAI_API_KEY.get_secret_value()
        ):
            raise ValueError(
                "OPENAI_EMBEDDING_API_KEY or OPENAI_API_KEY is required when "
                "EMBEDDING_PROVIDER=openai"
            )
        if self.LLM_PROVIDER == "openai" and not self.OPENAI_API_KEY.get_secret_value():
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return self


settings = Settings()


def get_settings() -> Settings:
    """Expose the singleton settings object.

    返回全局单例配置对象。
    """
    return settings
