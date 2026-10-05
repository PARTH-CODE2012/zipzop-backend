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
    # Container default; the dev flow binds 127.0.0.1 and prod sits behind the ALB.
    api_host: str = "0.0.0.0"  # nosec B104
    api_port: int = 8123
    #: Both spellings of the same origin — a browser sent to `127.0.0.1` and one
    #: sent to `localhost` present different `Origin` headers, and a list with
    #: only one of them rejects half the ways of opening the app.
    cors_origins: str = "http://localhost:3123,http://127.0.0.1:3123"
    #: How many reverse proxies in front of this process append to
    #: `X-Forwarded-For` — **1 behind a single ALB, 0 when nothing is**.
    #:
    #: The address rate limits count against is the entry that many places
    #: from the *right* of the chain: a proxy appends what it saw, so everything
    #: to the left of our own proxies' entries was written by the client and can
    #: say anything. 0 ignores the header altogether.
    #:
    #: ⚠️ Getting it wrong fails in two opposite directions. Too high, and a
    #: client-written entry is trusted again (docs/07-security.md §6.10). Left at
    #: 0 behind an ALB, every user shares the ALB's own address and one
    #: rate-limit bucket — 100 requests a minute for the whole product.
    trusted_proxy_hops: int = 0

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
    #: Largest picture ingest will decode, in pixels: 4K in any orientation,
    #: square included; 8K is refused. Not a quality rule — export stops at 4K
    #: — but a cost one (M7-24): what a file *costs* to decode is its pixels
    #: times its frames, and its size on disk says nothing about either. A
    #: 12 MB upload of black 8192x8192 frames took every core of the worker
    #: host and 4 GB of memory on the staging stack.
    max_video_pixels: int = 4096 * 4096
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

    #: Who processes dollars.
    #:
    #: §8.2's destination is Stripe. Stripe is **deferred, not dropped**, and
    #: until its adapter exists the only implemented provider has to take both
    #: currencies — which is what "Razorpay first" means in practice. Switching
    #: on the day Stripe lands is this one variable.
    billing_provider_for_usd: Literal["razorpay", "stripe"] = "razorpay"

    #: Days a referral commission is held before it counts as payable (M7-23).
    #:
    #: A refund or a lost chargeback reverses the commission on that payment; a
    #: commission already paid out cannot be reversed, only netted against the
    #: owner's next ones. Holding it a month means the common case — a refund
    #: asked for in the first weeks — is reversed before anyone is paid. A
    #: default proposed for the project lead to confirm; changing it changes no
    #: row, only what `GET /promo/{code}/stats` reports as payable.
    commission_hold_days: int = 30

    #: Accounts one address may open in an hour (docs/07-security.md §6.10).
    #:
    #: Measured on the local staging stack in M7: with only the shared 20-a-
    #: minute auth limit, one address opened 20 accounts a minute — 1,200 an
    #: hour, each with 300 free credits and 5 GB, all on a disposable domain.
    #: Ten an hour is far above what a household or a classroom needs and far
    #: below what a farm wants. Email verification is the stronger control and
    #: the project lead's call; this is the one that needed no mail provider.
    register_limit_per_hour: int = 10

    # ------------------------------------------------------------ computed
    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def billing_return_url_origins(self) -> list[str]:
        """Where a checkout may send the user back to.

        `returnUrl` arrives in the request body, so it is attacker-controlled:
        an open redirect on the billing path is a phishing page a customer
        reaches from a genuine payment. Only these origins are accepted, and
        they are the CORS list because that is already the set of places this
        application is served from — a second list would drift from the first.
        """
        return self.cors_origin_list

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
    # This compares against the dev default in order to refuse it; not a secret.
    if settings.jwt_secret_key == "dev-only-change-me":  # nosec B105
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
