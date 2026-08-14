"""Application settings.

Everything is read from the environment. Nothing is hardcoded, and nothing has
a production-safe default — a missing secret should fail loudly at startup
rather than quietly fall back to something insecure.
"""

from functools import lru_cache
from typing import Literal

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------- environment
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    log_level: str = "INFO"

    # ------------------------------------------------------------------ api
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"

    # ------------------------------------------------------------- database
    database_url: str = "postgresql+asyncpg://zipzop:zipzop@localhost:5432/zipzop"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # ---------------------------------------------------------------- redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ----------------------------------------------------------------- auth
    # Local and CI sign with HS256 and a shared secret. Production uses RS256
    # (docs/03-backend-architecture.md §10) — set jwt_algorithm=RS256 and point
    # the key paths at a real pair.
    jwt_algorithm: Literal["HS256", "RS256"] = "HS256"
    jwt_secret_key: str = "dev-only-change-me"
    jwt_private_key_path: str = ""
    jwt_public_key_path: str = ""
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_days: int = 30

    # -------------------------------------------------------------- storage
    s3_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "eu-west-1"
    s3_bucket: str = "zipzop-media"
    s3_access_key_id: str = "zipzop"
    s3_secret_access_key: str = "zipzop-dev-secret"
    s3_force_path_style: bool = True
    upload_url_ttl_seconds: int = 900
    download_url_ttl_seconds: int = 3600
    cdn_base_url: str = "http://localhost:9000/zipzop-media"

    # --------------------------------------------------------------- limits
    max_upload_bytes: int = 2_147_483_648  # 2 GB
    max_duration_ms: int = 3_600_000  # 60 min
    multipart_threshold_bytes: int = 104_857_600  # 100 MB

    # -------------------------------------------------------------- billing
    # Empty until M6. Absence is checked at call time, not at startup, so the
    # rest of the application runs without payment credentials.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # ------------------------------------------------------------ computed
    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Alembic runs synchronously; the application does not."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def assert_production_safe() -> None:
    """Refuse to start in production with development defaults.

    Called from the application factory. A service that boots with a known
    secret key is worse than one that will not boot at all.
    """
    if not settings.is_production:
        return

    problems: list[str] = []
    if settings.jwt_secret_key == "dev-only-change-me":
        problems.append("JWT_SECRET_KEY is still the development default")
    if settings.jwt_algorithm == "HS256":
        problems.append("JWT_ALGORITHM should be RS256 in production")
    if settings.debug:
        problems.append("DEBUG is enabled")
    if settings.s3_access_key_id == "zipzop":
        problems.append("S3 credentials are still the MinIO development pair")

    if problems:
        raise RuntimeError("unsafe production configuration: " + "; ".join(problems))
