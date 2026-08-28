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
    # Not 8000/3000: those are the two most contested ports on a developer's
    # machine, and another project holding one produced a stack that failed
    # three different ways depending on which half won. `scripts/ports.sh`
    # resolves them for the dev flow; these are the fallbacks when nothing has.
    api_host: str = "0.0.0.0"
    api_port: int = 8123
    #: Both spellings of the same origin — a browser sent to `127.0.0.1` and one
    #: sent to `localhost` present different `Origin` headers, and a list with
    #: only one of them rejects half the ways of opening the app.
    cors_origins: str = "http://localhost:3123,http://127.0.0.1:3123"

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

    # --------------------------------------------------------------- speech
    # Self-hosted faster-whisper, decided 20 August over a paid API: no
    # per-call cost that scales with usage, and the accuracy at this model size
    # is the same order as a cheap third-party service
    # (docs/11-m4-notes.md §1).
    #
    # `base` is the working default — roughly 150 MB, and about 20 s of CPU per
    # minute of media. `small` is noticeably better on accented speech and about
    # twice as slow. Both are a config change, not a deploy.
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    #: int8 on CPU is about twice the speed of float32, for a difference in
    #: word error rate that is lost in the noise at this size.
    whisper_compute_type: str = "int8"

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
    #
    # **Razorpay is the launch provider** (25 August, docs/13-mvp-direction.md).
    # Stripe is deferred, not dropped: the fields stay so adding the second
    # adapter is configuration rather than a schema change.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""

    #: Public by design — it ships in the browser bundle, like Stripe's
    #: publishable key. `rzp_test_…` in test mode, `rzp_live_…` in production.
    razorpay_key_id: str = ""
    #: **A credential, in test mode as much as in live mode.** Razorpay's own
    #: dashboard calls this "the test key", which makes it sound like sample
    #: data; it signs API calls against a real account. Never logged, never
    #: committed — see docs/07-security.md §2.
    razorpay_key_secret: str = ""
    #: A *third* secret, and not part of the pair above: it does not exist until
    #: a webhook endpoint is created in the dashboard, where you choose it.
    #: Without it the signature check on incoming webhooks cannot run, and that
    #: check is the whole defence on the billing path (§8.5).
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

    # ---------------------------------------------------------------- billing
    #
    # **Only checked when a key is present.** Billing lands in M6, and until it
    # does these are empty in every environment — refusing to boot over an
    # absent payment provider would stop a production deploy of an application
    # that does not take payments yet.
    #
    # Once one *is* configured, the same rule as everything above applies: a
    # half-configured payment provider is worse than none, because it fails at
    # the moment a customer is trying to pay rather than at startup.
    if settings.razorpay_key_id:
        if settings.razorpay_key_id.startswith("rzp_test_"):
            # The failure this exists for. Test keys accept a card, return a
            # success, and move no money — so a deploy with them looks like it
            # works, right up until someone asks where the revenue is.
            problems.append("RAZORPAY_KEY_ID is a test key (rzp_test_…) in production")
        if not settings.razorpay_key_secret:
            problems.append("RAZORPAY_KEY_ID is set but RAZORPAY_KEY_SECRET is empty")
        if not settings.razorpay_webhook_secret:
            # Without it there is no way to tell a real webhook from a forged
            # one, and webhooks are what grant credits and mark subscriptions
            # paid. An unverified billing callback is a way to grant a plan for
            # free (docs/07-security.md §6.7).
            problems.append(
                "RAZORPAY_WEBHOOK_SECRET is empty — incoming webhooks could not be verified"
            )

    if problems:
        raise RuntimeError("unsafe production configuration: " + "; ".join(problems))
